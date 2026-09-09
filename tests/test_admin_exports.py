"""Export des journaux depuis le panel : la garde d'accès.

Les journaux portent les questions de vraies personnes : la garde sur la route
vaut autant que le code qui la sert.
"""

from pathlib import Path

_ADMIN = Path(__file__).resolve().parent.parent / "app" / "back" / "admin.py"


def test_l_export_des_journaux_est_reserve_aux_admins() -> None:
    """Les journaux portent les questions posées : la garde ne doit pas sauter."""
    source = _ADMIN.read_text(encoding="utf-8")
    declaration = source.index('@admin_bp.route("/export/logs")')
    assert "@admin_required" in source[declaration : declaration + 200]
