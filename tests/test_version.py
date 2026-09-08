"""La version affichée au panel, et ce qu'elle prétend être.

Le piège serait d'annoncer la dernière release publiée comme étant celle qui
tourne : après un déploiement raté, c'est exactement l'information trompeuse.
D'où la provenance, rendue avec le nom.
"""

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from app.back import version

_RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _module_neuf() -> Iterator[None]:
    """Relit le module autour de chaque test : constantes et cache repartent à zéro."""
    importlib.reload(version)
    yield
    importlib.reload(version)


def _recharger(monkeypatch: pytest.MonkeyPatch, **environnement: str) -> ModuleType:
    """Relit le module avec l'environnement donné, les constantes étant figées."""
    for cle, valeur in environnement.items():
        monkeypatch.setenv(cle, valeur)
    return importlib.reload(version)


def test_l_estampille_de_l_image_fait_foi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gravée au build, elle dit ce que le conteneur exécute : rien ne la précède."""
    module = _recharger(monkeypatch, APP_VERSION="v1.4.2")

    nom, provenance = module.version_affichee()

    assert nom == "v1.4.2"
    assert provenance == module.GRAVEE
    assert module.url_release(nom).endswith("/releases/tag/v1.4.2")


def test_sans_estampille_on_montre_la_derniere_publiee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repli sur GitHub, annoncé comme tel : ce n'est pas forcément ce qui tourne."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    module = importlib.reload(version)
    monkeypatch.setattr(module, "derniere_release_publiee", lambda: "v2.1.11")

    nom, provenance = module.version_affichee()

    assert nom == "v2.1.11"
    assert provenance == module.PUBLIEE


def test_github_injoignable_ne_casse_pas_le_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le panel doit s'afficher sans réseau : l'appel échoue en silence."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    module = importlib.reload(version)

    def _tombe(*_args: object, **_kwargs: object) -> None:
        panne = "pas de réseau"
        raise httpx.ConnectError(panne)

    monkeypatch.setattr(module.httpx, "get", _tombe)

    nom, provenance = module.version_affichee()

    assert nom == module.SANS_VERSION
    assert provenance == module.INCONNUE
    assert module.url_release(nom) is None


def test_le_cache_vide_n_empeche_pas_le_premier_appel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression : au démarrage, GitHub n'était jamais interrogé.

    Le cache était initialisé à l'horodatage 0, mais `monotonic()` compte depuis
    le boot de la machine : quelques minutes après un redémarrage, ce 0 passait
    pour tout frais et le cache rendait son vide initial. Le panel affichait
    « injoignable » sans qu'aucun appel n'ait eu lieu.
    """
    monkeypatch.delenv("APP_VERSION", raising=False)
    module = importlib.reload(version)
    appels = []

    def _repondre(*_args: object, **_kwargs: object) -> object:
        appels.append(1)
        return type(
            "Reponse",
            (),
            {
                "raise_for_status": lambda _s: None,
                "json": lambda _s: {"tag_name": "v9.9.9"},
            },
        )()

    monkeypatch.setattr(module.httpx, "get", _repondre)
    # Juste après un boot : la machine n'a que quelques minutes d'uptime.
    monkeypatch.setattr(module.time, "monotonic", lambda: 42.0)

    assert module.derniere_release_publiee() == "v9.9.9"
    assert appels, "GitHub n'a pas été interrogé"


def test_la_revision_est_abregee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un SHA complet déborde du pied de sidebar ; sept caractères suffisent."""
    module = _recharger(monkeypatch, APP_REVISION="0123456789abcdef0123456789abcdef")

    assert module.REVISION == "0123456"


def test_l_image_grave_la_version_au_build() -> None:
    """Sans ces `ARG`, le conteneur ignore ce qu'il est et se rabat sur GitHub."""
    dockerfile = (_RACINE / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG APP_VERSION" in dockerfile
    assert "ENV APP_VERSION=$APP_VERSION" in dockerfile


def test_la_ci_passe_la_version_a_l_image() -> None:
    """Les `ARG` du Dockerfile ne servent que si la CI les renseigne."""
    workflow = (_RACINE / ".github" / "workflows" / "ci-cd.yml").read_text(
        encoding="utf-8"
    )
    assert "APP_VERSION=${{ steps.version.outputs.version }}" in workflow
    assert "APP_REVISION=${{ github.sha }}" in workflow
