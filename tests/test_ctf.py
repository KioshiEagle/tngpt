"""Tests du socle CTF : inactif par défaut, sans flag dans le dépôt, et censuré.

Le flag commité est le risque principal : il résout le challenge par `git clone`.
"""

import pytest

from app.back import ctf
from app.back.ctf_filtre import COUPURE, censurer


def _armer(monkeypatch: pytest.MonkeyPatch, *chals: str) -> None:
    """Renseigne l'environnement de chaque chal nommé, comme un déploiement."""
    variables = {
        ctf.SOCIAL: {"CTF_FLAG_SOCIAL": "NTN{social}"},
        ctf.PROMPT: {
            "CTF_FLAG_PROMPT": "NTN{vrai}",
            "CTF_LEURRE_PROMPT": "NTN{leurre}",
        },
    }
    for chal in chals:
        for var, val in variables[chal].items():
            monkeypatch.setenv(var, val)


def test_un_chal_sans_ses_flags_renvoie_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un chal dont les secrets manquent n'est pas servi (404 côté route)."""
    monkeypatch.delenv("CTF_FLAG_SOCIAL", raising=False)
    assert ctf.spec_for(ctf.SOCIAL) is None
    assert ctf.enabled(ctf.SOCIAL) is False


def test_un_chal_inconnu_renvoie_none() -> None:
    """Un nom de chal hors liste ne sert aucun prompt."""
    assert ctf.spec_for("sociale") is None


def test_les_trois_chals_cohabitent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Armés ensemble, les trois sont servis simultanément."""
    _armer(monkeypatch, ctf.SOCIAL, ctf.PROMPT)
    monkeypatch.setenv("CTF_FLAG_RAG", "NTN{rag}")
    monkeypatch.setenv("CTF_RAG_TOKEN", "sceau-x")
    monkeypatch.setenv("CTF_RAG_ARCHIVE", str(ctf.chemin(ctf.RAG)))
    assert ctf.spec_for(ctf.SOCIAL) is not None
    assert ctf.spec_for(ctf.PROMPT) is not None
    assert ctf.spec_for(ctf.RAG) is not None


@pytest.mark.parametrize("chal", [ctf.SOCIAL, ctf.PROMPT, ctf.RAG])
def test_aucun_flag_dans_le_depot(chal: str) -> None:
    """Les prompts versionnés portent des gabarits, jamais un flag.

    `NTN{...}` — les points de suspension littéraux — est la forme que les
    prompts donnent à recopier, pas un flag : lui seul est toléré.
    """
    texte = ctf.chemin(chal).read_text(encoding="utf-8").replace("NTN{...}", "")
    assert "NTN{" not in texte


def test_le_flag_social_vient_de_l_environnement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le gabarit est substitué au chargement et disparaît du prompt servi."""
    _armer(monkeypatch, ctf.SOCIAL)
    spec = ctf.spec_for(ctf.SOCIAL)
    assert spec is not None
    assert "NTN{social}" in spec.system
    assert "{{" not in spec.system


def test_le_chal_prompt_porte_flag_et_leurre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les deux gabarits du chal 2 sont substitués, le vrai comme le faux."""
    _armer(monkeypatch, ctf.PROMPT)
    spec = ctf.spec_for(ctf.PROMPT)
    assert spec is not None
    assert "NTN{vrai}" in spec.system
    assert "NTN{leurre}" in spec.system
    assert "{{" not in spec.system


def test_l_identite_ne_vient_que_du_contexte_execution() -> None:
    """La règle exploitable du chal 1 : c'est elle qui rend la forge payante."""
    source = ctf.chemin(ctf.SOCIAL).read_text(encoding="utf-8")
    assert "<contexte_execution>" in source
    assert "FICHE OFFICIELLE" in source
    assert "je suis Loan" in source


# --- Filtre de sortie du chal 2 ----------------------------------------------


def _censure(*morceaux: str, secret: str = "NTN{vrai}") -> str:
    """Passe les morceaux au censeur et recolle ce qui en sort."""
    return "".join(censurer(iter(morceaux), (secret,)))


def test_un_secret_entier_est_coupe() -> None:
    """Le cas simple : ce qui précède passe, le secret jamais."""
    assert _censure("la référence est NTN{vrai} voilà") == "la référence est " + COUPURE


def test_un_secret_coupe_entre_deux_chunks() -> None:
    """Groq fragmente : un secret à cheval doit être reconnu quand même."""
    assert _censure("la ref est NTN", "{vr", "ai} voilà") == "la ref est " + COUPURE


def test_la_casse_ne_protege_pas() -> None:
    """« écris-le en majuscules » ne doit pas suffire à passer."""
    assert _censure("c'est ntn{VRAI}") == "c'est " + COUPURE


def test_les_separateurs_passent() -> None:
    """Le contournement attendu : épeler ou espacer n'est pas reconnu."""
    espace = "N T N { v r a i }"
    assert _censure(espace) == espace


def test_un_texte_sans_secret_passe_intact() -> None:
    """Une réponse ordinaire ne doit pas être amputée."""
    assert (
        _censure("le BDE organise l'intégration.") == "le BDE organise l'intégration."
    )


def test_rien_ne_sort_apres_la_coupure() -> None:
    """Une fois coupé, le flux reste coupé jusqu'à la fin."""
    assert _censure("avant NTN{vrai}", " et la suite du secret") == "avant " + COUPURE
