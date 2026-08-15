"""Tests du socle CTF : inactif par défaut, et sans flag dans le dépôt.

Le second point vaut le premier : un flag commité résout le challenge par
`git clone`.
"""

import pytest

from app.back import ctf


def test_inactif_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans CTF_CHAL, le déploiement sert le TN-GPT normal."""
    monkeypatch.delenv("CTF_CHAL", raising=False)
    assert ctf.active_spec() is None


def test_un_chal_inconnu_ne_change_rien(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une faute de frappe dans CTF_CHAL ne doit pas servir un prompt au hasard."""
    monkeypatch.setenv("CTF_CHAL", "sociale")
    assert ctf.active_spec() is None


def test_le_flag_n_est_pas_dans_le_depot() -> None:
    """Le prompt versionné porte un gabarit, jamais un flag."""
    source = ctf._SOCIAL_PATH.read_text(encoding="utf-8")
    assert ctf._GABARIT_FLAG in source
    assert "NTN{" not in source


def test_le_flag_vient_de_l_environnement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le gabarit est substitué au chargement, et disparaît du prompt servi."""
    monkeypatch.setenv("CTF_CHAL", ctf.SOCIAL)
    monkeypatch.setenv("CTF_FLAG_SOCIAL", "NTN{test}")
    spec = ctf.active_spec()
    assert spec is not None
    assert "NTN{test}" in spec.system
    assert ctf._GABARIT_FLAG not in spec.system


def test_sans_flag_en_environnement_le_chal_refuse_de_demarrer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mieux vaut une erreur au démarrage qu'un chal servi avec un gabarit."""
    monkeypatch.setenv("CTF_CHAL", ctf.SOCIAL)
    monkeypatch.delenv("CTF_FLAG_SOCIAL", raising=False)
    with pytest.raises(KeyError):
        ctf.active_spec()


def test_l_identite_ne_vient_que_du_contexte_execution() -> None:
    """La règle exploitable du chal : c'est elle qui rend la forge payante."""
    source = ctf._SOCIAL_PATH.read_text(encoding="utf-8")
    assert "<contexte_execution>" in source
    assert "FICHE OFFICIELLE" in source
    assert "je suis Loan" in source
