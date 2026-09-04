"""Agrège les mesures du banc et rend le comparatif entre modèles.

Lit le JSONL produit par `bench_generation` et n'affiche que ce qui est
comparable : une question ne compte que si tous les modèles retenus l'ont
traitée, sans quoi l'écart mesuré ne serait qu'un écart d'échantillon.
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

CRITERES = ("fidelite", "pertinence", "completude", "ton")


def charger(chemin: Path) -> list[dict[str, Any]]:
    """Lit le journal de mesures, une par ligne."""
    return [
        json.loads(ligne)
        for ligne in chemin.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]


def _par_modele(mesures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Regroupe les mesures par modèle, indexées par question."""
    groupes: dict[str, dict[str, Any]] = defaultdict(dict)
    for mesure in mesures:
        groupes[mesure["modele"]][mesure["question"]] = mesure
    return groupes


def _moyenne(valeurs: list[float]) -> float | None:
    """Moyenne d'une liste, None si elle est vide."""
    return statistics.fmean(valeurs) if valeurs else None


def _ligne(intitule: str, colonnes: list[str], largeur: int = 22) -> str:
    """Formate une ligne de tableau à colonnes fixes."""
    return f"{intitule:<14}" + "".join(f"{c:>{largeur}}" for c in colonnes)


def _notes(mesures: list[dict[str, Any]], critere: str) -> list[float]:
    """Notes d'un critère, en ignorant les jugements illisibles."""
    return [
        m["notes"][critere] for m in mesures if m.get("notes") and critere in m["notes"]
    ]


def _cellule(moyenne: float | None, gabarit: str = "{:.3f}") -> str:
    """Une moyenne mise en forme, ou un tiret quand il n'y a rien à moyenner."""
    return "—" if moyenne is None else gabarit.format(moyenne)


def _notes_du_juge(retenues: dict[str, list], modeles: list[str]) -> None:
    """Tableau des critères, puis la moyenne d'ensemble."""
    print("\n--- Notes du juge, sur les questions communes ---")
    print(_ligne("critère", modeles))
    for critere in CRITERES:
        colonnes = [_cellule(_moyenne(_notes(retenues[m], critere))) for m in modeles]
        print(_ligne(critere, colonnes))

    globales = [
        _cellule(_moyenne([n for c in CRITERES for n in _notes(retenues[m], c)]))
        for m in modeles
    ]
    print(_ligne("ENSEMBLE", globales))


def _part_cache(mesures: list) -> str:
    """Part des appels où le cache a effectivement servi."""
    touches = [m for m in mesures if m["tokens_caches"] > 0]
    return f"{100 * len(touches) / len(mesures):.0f} %"


def _questions_par_jour(mesures: list) -> str:
    """Questions tenables par jour, cache déduit du quota.

    Les tokens servis par le cache ne sont pas décomptés du quota journalier :
    seule la part facturée borne le nombre d'appels.
    """
    entree = _moyenne([m["tokens_entree"] for m in mesures]) or 0
    cache = _moyenne([m["tokens_caches"] for m in mesures]) or 0
    facture = entree - cache
    return f"{200000 / facture:.0f}" if facture > 0 else "—"


def _cout_et_cache(retenues: dict[str, list], modeles: list[str]) -> None:
    """Tokens moyens, taux de cache touché, et volume tenable par jour."""
    print("\n--- Coût et cache ---")
    for intitule, cle in (
        ("entrée", "tokens_entree"),
        ("sortie", "tokens_sortie"),
        ("en cache", "tokens_caches"),
    ):
        colonnes = [
            _cellule(_moyenne([m[cle] for m in retenues[modele]]) or 0, "{:.0f}")
            for modele in modeles
        ]
        print(_ligne(intitule, colonnes))

    # Le cache ne mord pas à tous les appels : la moyenne sur l'ensemble est
    # le seul chiffre honnête, le bénéfice « quand ça marche » trompe.
    print(_ligne("cache touché", [_part_cache(retenues[m]) for m in modeles]))
    print(_ligne("quest./jour", [_questions_par_jour(retenues[m]) for m in modeles]))


def comparer(groupes: dict[str, dict[str, Any]], modeles: list[str]) -> None:
    """Affiche le comparatif sur les seules questions communes."""
    communes = set(groupes[modeles[0]])
    for modele in modeles[1:]:
        communes &= set(groupes[modele])

    print(f"\nQuestions traitées par tous les modèles : {len(communes)}")
    for modele in modeles:
        total = len(groupes[modele])
        print(f"   {modele:26} {total} mesure(s)")

    if not communes:
        print("\nAucune question commune : rien de comparable pour l'instant.")
        return

    retenues = {m: [groupes[m][q] for q in communes] for m in modeles}

    _notes_du_juge(retenues, modeles)
    _cout_et_cache(retenues, modeles)


def _verifier_contextes(groupes: dict[str, dict[str, Any]], modeles: list[str]) -> None:
    """Signale les questions où les modèles n'ont pas lu le même contexte."""
    divergentes = [
        question
        for question in set(groupes[modeles[0]]) & set(groupes[modeles[1]])
        if len(
            {
                groupes[m][question].get("contexte_sha")
                for m in modeles
                if groupes[m][question].get("contexte_sha") != "ancien-format"
            }
        )
        > 1
    ]
    if divergentes:
        print(
            f"\nATTENTION : {len(divergentes)} question(s) comparées sur des "
            "contextes différents — leur écart ne veut rien dire."
        )


def main() -> None:
    """Point d'entrée : lit le journal et rend le comparatif."""
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--sortie", type=Path, default=Path("bench_generation.jsonl"))
    args = parseur.parse_args()

    groupes = _par_modele(charger(args.sortie))
    modeles = sorted(groupes)
    if not modeles:
        print("Aucune mesure.")
        return

    comparer(groupes, modeles)
    if len(modeles) >= 2:  # noqa: PLR2004
        _verifier_contextes(groupes, modeles)


if __name__ == "__main__":
    main()
