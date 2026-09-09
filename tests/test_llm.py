"""Client de complétion : découpage du flux, erreurs, réessais.

Il remplace deux SDK dont on n'appelait qu'une méthode. Ce qu'ils faisaient
gratuitement — parser le SSE, distinguer un timeout d'un 429, ne pas rejouer
un flux déjà commencé — se teste désormais ici.
"""

import httpx
import pytest

from app.back.llm import (
    Client,
    LLMConnexionError,
    LLMStatutError,
    LLMTimeoutError,
    _chunks_du_flux,
)

_HTTP_TROP_DE_REQUETES = 429
_HTTP_NON_AUTORISE = 401
_DEUX_ESSAIS = 2


def _flux(lignes: list[str]) -> httpx.Response:
    """Réponse httpx qui rend ces lignes, comme un flux SSE."""
    corps = "".join(f"{ligne}\n" for ligne in lignes).encode()
    return httpx.Response(200, content=corps)


def test_le_texte_est_rendu_morceau_par_morceau() -> None:
    """Le cas courant : des deltas de contenu, puis la fin du flux."""
    reponse = _flux(
        [
            'data: {"choices":[{"delta":{"content":"Mouton"}}]}',
            'data: {"choices":[{"delta":{"content":" \\u00d7 Ergo"}}]}',
            "data: [DONE]",
        ]
    )

    morceaux = [c.choices[0].delta.content for c in _chunks_du_flux(reponse)]

    assert morceaux == ["Mouton", " \u00d7 Ergo"]


def test_les_appels_d_outil_sont_reconstruits() -> None:
    """La carte des mers lit `delta.tool_calls[].function.arguments`."""
    appel = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"name":"carte","arguments":"{\\"a\\":"}}]}}]}'
    )
    reponse = _flux([appel, "data: [DONE]"])

    (chunk,) = list(_chunks_du_flux(reponse))
    (appel,) = chunk.choices[0].delta.tool_calls or []

    assert appel.function.name == "carte"
    assert appel.function.arguments == '{"a":'


def test_une_ligne_illisible_ne_coupe_pas_le_flux() -> None:
    """Un fournisseur qui bafouille ne doit pas emporter la réponse en cours."""
    reponse = _flux(
        [
            'data: {"choices":[{"delta":{"content":"avant"}}]}',
            "data: {ceci n'est pas du json",
            'data: {"choices":[{"delta":{"content":"après"}}]}',
            "data: [DONE]",
        ]
    )

    morceaux = [c.choices[0].delta.content for c in _chunks_du_flux(reponse)]

    assert morceaux == ["avant", "après"]


def test_ce_qui_suit_la_fin_de_flux_est_ignore() -> None:
    """`[DONE]` ferme le flux : ce qui traîne derrière n'est pas une réponse."""
    reponse = _flux(
        [
            'data: {"choices":[{"delta":{"content":"fin"}}]}',
            "data: [DONE]",
            'data: {"choices":[{"delta":{"content":"de trop"}}]}',
        ]
    )

    morceaux = [c.choices[0].delta.content for c in _chunks_du_flux(reponse)]

    assert morceaux == ["fin"]


def _client_bouchonne(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport, essais: int = 2
) -> Client:
    """Client dont les requêtes partent dans un transport de test."""
    # Capturé avant la substitution, sinon la fabrique s'appellerait elle-même.
    vrai_client = httpx.Client

    def fabrique(**_kwargs: object) -> httpx.Client:
        # Timeout et réglages n'ont pas de sens face à un transport bouchonné.
        return vrai_client(transport=transport)

    monkeypatch.setattr("app.back.llm.httpx.Client", fabrique)
    return Client(
        api_key="cle-de-test", base_url="https://exemple.test/v1", max_retries=essais
    )


def test_un_statut_definitif_ne_se_rejoue_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une clé refusée le restera : réessayer ne fait que retarder l'erreur."""
    appels = []

    def repondre(_requete: httpx.Request) -> httpx.Response:
        appels.append(1)
        return httpx.Response(_HTTP_NON_AUTORISE, text="clé invalide")

    client = _client_bouchonne(monkeypatch, httpx.MockTransport(repondre))

    with pytest.raises(LLMStatutError) as erreur:
        client.chat.completions.create(model="m", messages=[], stream=True)

    assert erreur.value.status_code == _HTTP_NON_AUTORISE
    assert len(appels) == 1, "un 401 ne doit pas être rejoué"


def test_une_surcharge_est_rejouee(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 et 5xx sont passagers : c'est tout l'intérêt du pool de clés."""
    appels = []

    def repondre(_requete: httpx.Request) -> httpx.Response:
        appels.append(1)
        if len(appels) < _DEUX_ESSAIS:
            return httpx.Response(_HTTP_TROP_DE_REQUETES, text="trop vite")
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{}}]}\n')

    monkeypatch.setattr("app.back.llm._ATTENTE_ENTRE_ESSAIS", 0)
    client = _client_bouchonne(monkeypatch, httpx.MockTransport(repondre))

    list(client.chat.completions.create(model="m", messages=[], stream=True))

    assert len(appels) == _DEUX_ESSAIS, "le second essai doit aboutir"


def test_une_panne_reseau_remonte_traduite(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'échelle de repli teste nos exceptions, pas celles de httpx."""

    def tomber(_requete: httpx.Request) -> httpx.Response:
        message = "pas de route"
        raise httpx.ConnectError(message)

    monkeypatch.setattr("app.back.llm._ATTENTE_ENTRE_ESSAIS", 0)
    client = _client_bouchonne(monkeypatch, httpx.MockTransport(tomber), essais=0)

    with pytest.raises(LLMConnexionError):
        client.chat.completions.create(model="m", messages=[], stream=True)


def test_un_timeout_se_distingue_d_une_panne(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le repli change selon les deux : un timeout vaut un autre fournisseur."""

    def temporiser(_requete: httpx.Request) -> httpx.Response:
        message = "trop long"
        raise httpx.ReadTimeout(message)

    monkeypatch.setattr("app.back.llm._ATTENTE_ENTRE_ESSAIS", 0)
    client = _client_bouchonne(monkeypatch, httpx.MockTransport(temporiser), essais=0)

    with pytest.raises(LLMTimeoutError):
        client.chat.completions.create(model="m", messages=[], stream=True)


def test_une_reponse_d_un_bloc_est_lue(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'extraction de métadonnées des PDF n'utilise pas le flux."""

    def repondre(_requete: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "titre"}}]}
        )

    client = _client_bouchonne(monkeypatch, httpx.MockTransport(repondre))

    reponse = client.chat.completions.create(model="m", messages=[])

    assert reponse.choices[0].message.content == "titre"


def test_l_attente_demandee_est_transmise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sur 429, l'échelle de repli lit `retry-after` pour décider d'attendre."""

    def repondre(_requete: httpx.Request) -> httpx.Response:
        return httpx.Response(
            _HTTP_TROP_DE_REQUETES, text="calme-toi", headers={"retry-after": "12"}
        )

    monkeypatch.setattr("app.back.llm._ATTENTE_ENTRE_ESSAIS", 0)
    client = _client_bouchonne(monkeypatch, httpx.MockTransport(repondre), essais=0)

    with pytest.raises(LLMStatutError) as erreur:
        client.chat.completions.create(model="m", messages=[], stream=True)

    assert erreur.value.retry_after == 12.0  # noqa: PLR2004
