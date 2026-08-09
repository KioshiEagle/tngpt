"""Tests du filtrage <think> et de l'échelle de repli Groq.

Aucun accès réseau : le stream Groq est simulé, et `_classify_error` est une
fonction de décision pure.

Ces deux mécaniques ont toutes deux été cassées par le merge des branches
conversations et clubs-info — la classe `_ThinkFilter` et l'appel à
`_classify_error` avaient disparu tout en laissant leurs points d'appel — sans
qu'aucun test ne s'en aperçoive. D'où ce fichier.
"""

from collections.abc import Iterator
from typing import cast

import httpx
import pytest
from groq import APIConnectionError, APIStatusError, APITimeoutError, Stream
from groq.types.chat import ChatCompletionChunk

from app.back.generate import (
    _EMPTY_ANSWER,
    _classify_error,
    _stream_chunks,
    _ThinkFilter,
)


def _flux(*fragments: str) -> Stream[ChatCompletionChunk]:
    """Simule un stream Groq qui livre `fragments` en autant de chunks."""

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    def flux() -> Iterator[object]:
        for fragment in fragments:
            yield _Chunk(fragment)

    return cast("Stream[ChatCompletionChunk]", flux())


def _filtre(*fragments: str) -> str:
    """Passe les fragments dans le filtre et recolle ce qui en sort."""
    filtre = _ThinkFilter()
    sortie = "".join("".join(filtre.feed(f)) for f in fragments)
    return sortie + "".join(filtre.flush())


# --- Filtrage des blocs <think> ---------------------------------------------


def test_bloc_think_entier_dans_un_chunk() -> None:
    """Le cas simple : le bloc arrive d'une pièce et disparaît."""
    assert _filtre("Salut <think>je réfléchis</think>ça va") == "Salut ça va"


def test_texte_sans_think_passe_intact() -> None:
    """Sans balise, le filtre est transparent."""
    assert _filtre("Le BDE organise l'intégration.") == "Le BDE organise l'intégration."


def test_bloc_think_coupe_entre_deux_chunks() -> None:
    """Le cas d'usage même de la classe : Groq fragmente les tags."""
    assert _filtre("Salut <thi", "nk>caché</thi", "nk>ça va") == "Salut ça va"


def test_balise_ouvrante_seule_ne_fuit_pas() -> None:
    """Un flux coupé avant </think> ne doit rien laisser passer du raisonnement."""
    assert _filtre("<think>raisonnement interrompu") == ""


def test_plusieurs_blocs_think() -> None:
    """Le filtre rebascule d'état à chaque paire de balises."""
    assert _filtre("a<think>x</think>b<think>y</think>c") == "abc"


def test_stream_chunks_filtre_et_recolle() -> None:
    """Bout en bout sur un stream simulé, tel que le chat le consomme."""
    morceaux = _stream_chunks(_flux("Le BDE ", "<think>hmm</think>", "existe."))
    assert "".join(morceaux) == "Le BDE existe."


def test_stream_chunks_ne_rend_jamais_une_reponse_vide() -> None:
    """Tout filtré ⇒ message de repli, sinon le front n'affiche aucune bulle."""
    assert "".join(_stream_chunks(_flux("<think>tout est parti"))) == _EMPTY_ANSWER


# --- Échelle de repli Groq ---------------------------------------------------


def _erreur(code: int) -> APIStatusError:
    reponse = httpx.Response(code, request=httpx.Request("POST", "http://groq.test"))
    return APIStatusError("boom", response=reponse, body=None)


def test_429_attend_puis_retente() -> None:
    """Rate limit Groq : on temporise avant de reprendre."""
    outcome = _classify_error(_erreur(429), 0, 3)
    assert (outcome.retry, outcome.backoff) == (True, True)


def test_413_retente_avec_moins_de_contexte() -> None:
    """Prompt trop gros : on rogne les archives et on relance."""
    outcome = _classify_error(_erreur(413), 0, 3)
    assert (outcome.retry, outcome.smaller_context) == (True, True)


def test_400_avec_parametres_retente_sans_eux() -> None:
    """Modèle qui refuse les paramètres de CHAT_GROQ_PARAMS : on réessaie nu."""
    outcome = _classify_error(_erreur(400), 0, 3, has_params=True)
    assert (outcome.retry, outcome.drop_params) == (True, True)


def test_400_sans_parametres_est_fatal() -> None:
    """Rien à retirer : inutile de boucler, on rend la main avec un message."""
    outcome = _classify_error(_erreur(400), 0, 3, has_params=False)
    assert outcome.retry is False
    assert outcome.error_message is not None


def test_derniere_tentative_ne_retente_plus() -> None:
    """Le budget d'essais épuisé, même un 429 devient fatal."""
    outcome = _classify_error(_erreur(429), 2, 3)
    assert outcome.retry is False
    assert outcome.error_message is not None


@pytest.mark.parametrize(
    "erreur",
    [
        APITimeoutError(request=httpx.Request("POST", "http://groq.test")),
        APIConnectionError(request=httpx.Request("POST", "http://groq.test")),
        ValueError("imprévu"),
    ],
)
def test_erreurs_non_http_donnent_toujours_un_message(erreur: Exception) -> None:
    """`_stream_with_retries` fait un `assert` dessus : il ne doit jamais manquer."""
    outcome = _classify_error(erreur, 0, 3)
    assert outcome.retry is False
    assert outcome.error_message
