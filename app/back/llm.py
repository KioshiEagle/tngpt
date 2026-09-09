"""Client des API de complétion compatibles OpenAI, bâti sur httpx.

Les quatre fournisseurs du pool — Mistral, DeepSeek, Cerebras, Groq — exposent
le même `POST /chat/completions`, et l'application n'appelle que celui-là. Deux
SDK y répondaient : 47 Mo de mémoire et 880 modules chargés au démarrage pour
une seule méthode, quand httpx, déjà chargé pour Qdrant, la porte en deux cents
lignes.

Les objets rendus gardent la forme de ceux des SDK — `chunk.choices[0].delta` —
pour que les consommateurs de flux n'aient pas à changer.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

import httpx

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Ligne de fin du flux SSE, commune à tous ces fournisseurs.
_FIN_DE_FLUX = "[DONE]"
_PREFIXE_DONNEE = "data:"
# Au-delà, l'appel ne viendra pas : même seuil que celui réglé sur les SDK.
_TIMEOUT_DEFAUT = 20.0
_ESSAIS_DEFAUT = 2
# Statuts qui valent la peine d'être retentés : surcharge et pannes passagères.
_STATUTS_REESSAYABLES = frozenset({408, 409, 429, 500, 502, 503, 504})
_ATTENTE_ENTRE_ESSAIS = 0.5


class LLMError(Exception):
    """Échec d'un appel à une API de complétion."""


class LLMConnexionError(LLMError):
    """Le fournisseur est injoignable : DNS, TCP, TLS."""


class LLMTimeoutError(LLMError):
    """Le fournisseur n'a rien répondu dans le délai imparti."""


class LLMStatutError(LLMError):
    """Le fournisseur a répondu, mais par une erreur HTTP."""

    def __init__(
        self, message: str, status_code: int, retry_after: float | None = None
    ) -> None:
        """Retient ce sur quoi se décide le repli : le code et l'attente demandée.

        Args:
            message: Corps de la réponse, tronqué.
            status_code: Code HTTP rendu par le fournisseur.
            retry_after: Secondes réclamées en en-tête, quand il y en a.

        """
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_after(reponse: httpx.Response) -> float | None:
    """Délai réclamé en en-tête `retry-after`, en secondes.

    Args:
        reponse: Réponse en erreur du fournisseur.

    Returns:
        Le délai, ou None s'il est absent ou donné sous forme de date HTTP.

    """
    brut = reponse.headers.get("retry-after")
    if not brut:
        return None
    try:
        return float(brut)
    except ValueError:
        return None


@dataclass
class ToolCallFunction:
    """Partie « fonction » d'un appel d'outil, arguments compris."""

    name: str | None = None
    arguments: str = ""


@dataclass
class ToolCall:
    """Appel d'outil, tel qu'il arrive morceau par morceau dans le flux."""

    index: int = 0
    id: str | None = None
    type: str | None = None
    function: ToolCallFunction = field(default_factory=ToolCallFunction)


@dataclass
class Delta:
    """Incrément de contenu d'un chunk : du texte, ou un bout d'appel d'outil."""

    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCall] | None = None


@dataclass
class Choice:
    """Une des complétions demandées ; il n'y en a qu'une ici."""

    delta: Delta
    finish_reason: str | None = None
    index: int = 0


@dataclass
class Chunk:
    """Un morceau de flux, de la même forme que celui des SDK."""

    choices: list[Choice]


@dataclass
class Message:
    """Message complet, quand la réponse n'est pas demandée en flux."""

    content: str | None = None


@dataclass
class ChoixComplet:
    """Une complétion rendue d'un bloc."""

    message: Message
    finish_reason: str | None = None


@dataclass
class Reponse:
    """Réponse hors flux, de la même forme que celle des SDK."""

    choices: list[ChoixComplet]


def _lire_delta(brut: dict[str, Any]) -> Delta:
    """Construit un `Delta` à partir du JSON d'un chunk.

    Args:
        brut: Objet `delta` rendu par le fournisseur.

    Returns:
        Le delta, ses appels d'outils reconstruits.

    """
    appels = brut.get("tool_calls")
    return Delta(
        content=brut.get("content"),
        reasoning=brut.get("reasoning"),
        tool_calls=[
            ToolCall(
                index=appel.get("index", 0),
                id=appel.get("id"),
                type=appel.get("type"),
                function=ToolCallFunction(
                    name=(appel.get("function") or {}).get("name"),
                    arguments=(appel.get("function") or {}).get("arguments") or "",
                ),
            )
            for appel in appels
        ]
        if appels
        else None,
    )


def _chunks_du_flux(reponse: httpx.Response) -> Iterator[Chunk]:
    """Découpe un flux SSE en chunks.

    Args:
        reponse: Réponse HTTP ouverte, en cours de lecture.

    Yields:
        Les chunks, dans l'ordre d'arrivée.

    """
    for ligne in reponse.iter_lines():
        if not ligne.startswith(_PREFIXE_DONNEE):
            continue
        charge = ligne[len(_PREFIXE_DONNEE) :].strip()
        if charge == _FIN_DE_FLUX:
            return
        try:
            brut = json.loads(charge)
        except ValueError:
            # Un fournisseur qui bafouille ne doit pas emporter la réponse
            # déjà servie : on saute la ligne et on continue de lire.
            logger.warning("Chunk illisible ignoré : %.120s", charge)
            continue
        yield Chunk(
            choices=[
                Choice(
                    delta=_lire_delta(choix.get("delta") or {}),
                    finish_reason=choix.get("finish_reason"),
                    index=choix.get("index", 0),
                )
                for choix in brut.get("choices") or []
            ]
        )


class Completions:
    """Point d'appel unique : `create`, en flux."""

    def __init__(self, client: "Client") -> None:
        """Retient le client porteur de l'URL et de la clé.

        Args:
            client: Client qui expose ce point d'appel.

        """
        self._client = client

    @overload
    def create(
        self,
        *,
        stream: Literal[True],
        **params: Any,  # noqa: ANN401
    ) -> Iterator[Chunk]: ...

    @overload
    def create(
        self,
        *,
        stream: Literal[False] = ...,
        **params: Any,  # noqa: ANN401
    ) -> Reponse: ...

    def create(self, **params: Any) -> Iterator[Chunk] | Reponse:
        """Demande une complétion, en flux ou d'un bloc selon `stream`.

        Args:
            **params: Corps de la requête — `model`, `messages`, `stream`, et
                le reste tel que le fournisseur l'attend.

        Returns:
            Les chunks du flux, ou la réponse complète.

        """
        if params.get("stream"):
            return self._client.flux("/chat/completions", params)
        return self._client.appel("/chat/completions", params)


class Chat:
    """Espace de noms `client.chat.completions`, comme dans les SDK."""

    def __init__(self, client: "Client") -> None:
        """Expose les complétions du client.

        Args:
            client: Client qui porte cet espace de noms.

        """
        self.completions = Completions(client)


class Client:
    """Client d'un fournisseur compatible OpenAI."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = _TIMEOUT_DEFAUT,
        max_retries: int = _ESSAIS_DEFAUT,
    ) -> None:
        """Prépare le client sans ouvrir de connexion.

        Args:
            api_key: Clé du fournisseur.
            base_url: Racine de son API, sans barre finale.
            timeout: Délai entre deux morceaux du flux.
            max_retries: Nombre de tentatives supplémentaires.

        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.chat = Chat(self)

    def appel(self, chemin: str, corps: dict[str, Any]) -> Reponse:
        """Complétion rendue d'un bloc, pour les usages hors chat.

        Args:
            chemin: Chemin de l'API, relatif à `base_url`.
            corps: Charge utile JSON.

        Returns:
            La réponse complète.

        """
        return self._avec_reessais(lambda: self._poster(chemin, corps))

    def _poster(self, chemin: str, corps: dict[str, Any]) -> Reponse:
        """Envoie la requête et lit la réponse entière.

        Args:
            chemin: Chemin de l'API, relatif à `base_url`.
            corps: Charge utile JSON.

        Returns:
            La réponse, ses choix reconstruits.

        Raises:
            LLMConnexionError: Le fournisseur est injoignable.
            LLMTimeoutError: Il n'a pas répondu dans le délai.
            LLMStatutError: Il a répondu par une erreur HTTP.

        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                reponse = client.post(
                    f"{self.base_url}{chemin}", json=corps, headers=self._entetes()
                )
        except httpx.TimeoutException as erreur:
            raise LLMTimeoutError(str(erreur)) from erreur
        except httpx.HTTPError as erreur:
            raise LLMConnexionError(str(erreur)) from erreur

        if reponse.status_code >= httpx.codes.BAD_REQUEST:
            raise LLMStatutError(
                reponse.text[:500], reponse.status_code, _retry_after(reponse)
            )

        brut = reponse.json()
        return Reponse(
            choices=[
                ChoixComplet(
                    message=Message(
                        content=(choix.get("message") or {}).get("content")
                    ),
                    finish_reason=choix.get("finish_reason"),
                )
                for choix in brut.get("choices") or []
            ]
        )

    def _entetes(self) -> dict[str, str]:
        """En-têtes d'authentification, communs à tous ces fournisseurs."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def flux(self, chemin: str, corps: dict[str, Any]) -> Iterator[Chunk]:
        """Appelle le fournisseur et rend les chunks du flux.

        Les tentatives portent sur l'ouverture de la réponse, jamais sur un
        flux déjà commencé : renvoyer la requête après trois phrases servies
        les répéterait à l'écran.

        Args:
            chemin: Chemin de l'API, relatif à `base_url`.
            corps: Charge utile JSON.

        Returns:
            Les chunks du flux.

        Raises:
            LLMConnexionError: Le fournisseur est injoignable.
            LLMTimeoutError: Il n'a pas répondu dans le délai.
            LLMStatutError: Il a répondu par une erreur HTTP.

        """
        return self._avec_reessais(lambda: self._ouvrir(chemin, corps))

    def _avec_reessais(self, appel: Callable[[], _T]) -> _T:
        """Rejoue l'appel tant que l'échec paraît passager.

        Args:
            appel: Ce qu'on tente, et qu'on rejouera tel quel.

        Returns:
            Ce que l'appel a rendu.

        Raises:
            LLMError: Le dernier échec, une fois les essais épuisés.

        """
        derniere: LLMError | None = None
        for essai in range(self.max_retries + 1):
            try:
                return appel()
            except LLMStatutError as erreur:
                if erreur.status_code not in _STATUTS_REESSAYABLES:
                    raise
                derniere = erreur
            except (LLMConnexionError, LLMTimeoutError) as erreur:
                derniere = erreur
            if essai < self.max_retries:
                time.sleep(_ATTENTE_ENTRE_ESSAIS * (essai + 1))
        raise derniere  # ty: ignore[invalid-raise]

    def _ouvrir(self, chemin: str, corps: dict[str, Any]) -> Iterator[Chunk]:
        """Ouvre la réponse en flux, en laissant remonter nos propres erreurs.

        Args:
            chemin: Chemin de l'API, relatif à `base_url`.
            corps: Charge utile JSON.

        Returns:
            Les chunks, le client HTTP étant refermé à la fin de l'itération.

        Raises:
            LLMConnexionError: Le fournisseur est injoignable.
            LLMTimeoutError: Il n'a pas répondu dans le délai.
            LLMStatutError: Il a répondu par une erreur HTTP.

        """
        client = httpx.Client(timeout=self.timeout)
        gestionnaire = client.stream(
            "POST",
            f"{self.base_url}{chemin}",
            json=corps,
            headers=self._entetes(),
        )
        try:
            reponse = gestionnaire.__enter__()
        except httpx.TimeoutException as erreur:
            client.close()
            raise LLMTimeoutError(str(erreur)) from erreur
        except httpx.HTTPError as erreur:
            client.close()
            raise LLMConnexionError(str(erreur)) from erreur

        if reponse.status_code >= httpx.codes.BAD_REQUEST:
            detail = reponse.read().decode("utf-8", "replace")[:500]
            gestionnaire.__exit__(None, None, None)
            client.close()
            raise LLMStatutError(detail, reponse.status_code, _retry_after(reponse))

        def lire() -> Iterator[Chunk]:
            try:
                yield from _chunks_du_flux(reponse)
            except httpx.TimeoutException as erreur:
                raise LLMTimeoutError(str(erreur)) from erreur
            except httpx.HTTPError as erreur:
                raise LLMConnexionError(str(erreur)) from erreur
            finally:
                gestionnaire.__exit__(None, None, None)
                client.close()

        return lire()
