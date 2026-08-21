"""Garde-fou : une base neuve doit se peupler par les seules migrations.

Les rôles 5 à 13 avaient été saisis à la main sur la base d'origine, donc
aucune migration ne les créait : tout déploiement neuf s'arrêtait sur une
violation de clé étrangère, et seul un dump SQL rattrapait le coup. Ces tests
relisent les données semées par les migrations et vérifient qu'aucun
identifiant référencé n'y manque.
"""

from pathlib import Path
from typing import Any

_VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"

_SOCLE = "b3d7c1a9e042_clubs_assos_roles_et_bureaux"
_PLAQUETTE = "c4e91a72d508_description_et_clubs_de_la_plaquette"
_BUREAUX_CLUBS = "f512c8ab6d70_bureaux_2026_2027_des_clubs"
_BUREAUX_ASSOS = "d8b2f04ce591_bureaux_du_bde_et_d_anim_est"


def _constantes(nom: str) -> dict[str, Any]:
    """Constantes d'une migration, sans passer par Alembic ni par une base."""
    chemin = _VERSIONS / f"{nom}.py"
    espace: dict[str, Any] = {}
    exec(compile(chemin.read_text(encoding="utf-8"), str(chemin), "exec"), espace)
    return espace


def test_tous_les_roles_references_sont_crees() -> None:
    """`club_roles` et `asso_roles` ont une clé étrangère vers `roles`."""
    socle = _constantes(_SOCLE)
    assos = _constantes(_BUREAUX_ASSOS)

    crees = {role["role_id"] for role in socle["ROLES"]}
    crees |= {rid for rid, _ in assos["ROLES_SAISIS_A_LA_MAIN"]}
    crees |= {rid for rid, _ in assos["NOUVEAUX_ROLES"]}

    references = {role_id for _, role_id, _ in assos["BUREAUX"]}
    references |= {role_id for role_id, *_ in _constantes(_BUREAUX_CLUBS)["BUREAUX"]}

    assert not references - crees, (
        f"rôles référencés sans être créés : {sorted(references - crees)}"
    )


def test_les_intitules_de_roles_sont_uniques() -> None:
    """`roles.role_name` porte une contrainte UNIQUE : un doublon casse l'INSERT."""
    socle = _constantes(_SOCLE)
    assos = _constantes(_BUREAUX_ASSOS)

    noms = [role["role_name"] for role in socle["ROLES"]]
    noms += [nom for _, nom in assos["ROLES_SAISIS_A_LA_MAIN"]]
    noms += [nom for _, nom in assos["NOUVEAUX_ROLES"]]

    doublons = {nom for nom in noms if noms.count(nom) > 1}
    assert not doublons, f"intitulés en double : {sorted(doublons)}"


def test_tous_les_clubs_references_sont_crees() -> None:
    """`club_roles` a une clé étrangère vers `clubs`."""
    clubs = _constantes(_BUREAUX_CLUBS)
    crees = {club_id for club_id, *_ in _constantes(_PLAQUETTE)["CLUBS"]}
    crees |= {club_id for club_id, _ in clubs["NOUVEAUX_CLUBS"]}

    references = {club_id for _, club_id, _, _ in clubs["BUREAUX"]}

    assert not references - crees, (
        f"clubs référencés sans être créés : {sorted(references - crees)}"
    )


def test_toutes_les_assos_referencees_sont_creees() -> None:
    """`asso_roles` et `clubs` ont une clé étrangère vers `assos`."""
    crees = {asso["asso_id"] for asso in _constantes(_SOCLE)["ASSOS"]}
    crees |= {asso["asso_id"] for asso in _constantes(_PLAQUETTE)["ASSOS"]}

    references = {asso_id for asso_id, _, _ in _constantes(_BUREAUX_ASSOS)["BUREAUX"]}
    references |= {asso_id for _, _, _, asso_id, _ in _constantes(_PLAQUETTE)["CLUBS"]}

    assert not references - crees, (
        f"assos référencées sans être créées : {sorted(references - crees)}"
    )
