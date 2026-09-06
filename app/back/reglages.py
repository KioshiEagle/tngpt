"""Réglages globaux de l'app, lus par le front et écrits par le panel admin.

Un réglage absent de la table vaut « éteint » : une base neuve démarre donc
dans l'état par défaut, sans qu'aucune migration n'ait à semer de ligne.
"""

from .models import Setting, db

# Habillage « flamme » du mode brainrot : palette braise et canard en ombre
# chinoise, à la place du rose. Caché tant qu'un admin ne l'allume pas.
FLAMME = "flamme"

_ACTIF = "on"
_INACTIF = "off"


def est_actif(cle: str) -> bool:
    """Indique si le réglage `cle` est allumé.

    Args:
        cle: Nom du réglage, parmi les constantes de ce module.

    Returns:
        True si le réglage existe et vaut « on », False sinon.

    """
    reglage = db.session.get(Setting, cle)
    return reglage is not None and reglage.value == _ACTIF


def basculer(cle: str, *, actif: bool, user_id: int | None = None) -> None:
    """Allume ou éteint le réglage `cle`, en créant la ligne au besoin.

    Args:
        cle: Nom du réglage, parmi les constantes de ce module.
        actif: Nouvel état voulu.
        user_id: Auteur du changement, pour tracer qui a touché à quoi.

    """
    reglage = db.session.get(Setting, cle)
    if reglage is None:
        reglage = Setting(key=cle)
        db.session.add(reglage)
    reglage.value = _ACTIF if actif else _INACTIF
    reglage.updated_by = user_id
    db.session.commit()
