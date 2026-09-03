"""Tolérance http du flux OAuth, réservée au bouclage."""

import os

from oauthlib.oauth2.rfc6749.utils import is_secure_transport

from app.back.auth import _en_local, _http_tolere

_VARIABLE = "OAUTHLIB_INSECURE_TRANSPORT"


def _contexte(url: str) -> object:
    """Contexte de requête de l'application pour une URL donnée."""
    from main import app  # noqa: PLC0415

    return app.test_request_context(url)


def test_le_bouclage_est_reconnu() -> None:
    """Google autorise http sur le bouclage, oauthlib non : d'où la levée."""
    for base in ("http://localhost:8000", "http://127.0.0.1:8000"):
        with _contexte(base + "/auth/callback"):  # ty: ignore[invalid-context-manager]
            assert _en_local()


def test_un_vrai_domaine_en_http_reste_refuse() -> None:
    """La levée ne doit jamais couvrir autre chose qu'une machine de dev.

    Sans quoi un jeton OAuth pourrait circuler en clair sur le réseau.
    """
    with _contexte("http://tngpt.example.net/auth/callback"):  # ty: ignore[invalid-context-manager]
        assert not _en_local()


def test_la_levee_ne_survit_pas_a_l_echange() -> None:
    """Posée durablement, elle vaudrait aussi pour les requêtes suivantes."""
    avant = os.environ.get(_VARIABLE)
    with (
        _contexte("http://localhost:8000/auth/callback"),  # ty: ignore[invalid-context-manager]
        _http_tolere(),
    ):
        assert is_secure_transport("http://exemple.test/cb")
    assert os.environ.get(_VARIABLE) == avant
