"""Routes de pages, et ce que leurs gabarits en attendent."""

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "front" / "templates"

_HTTP_OK = 200


def _adapter() -> object:
    """Table des URL de l'application, liée à un hôte quelconque."""
    from main import app  # noqa: PLC0415

    return app.url_map.bind("localhost")


def _client() -> object:
    """Client de test de l'application."""
    from main import app  # noqa: PLC0415

    return app.test_client()


def test_la_page_rgpd_est_servie() -> None:
    """Le bouton du menu profil pointe sur /rgpd : la route doit exister."""
    endpoint, _ = _adapter().match("/rgpd")  # ty: ignore[unresolved-attribute]
    assert endpoint == "chat.rgpd"


def test_la_page_rgpd_est_lisible_sans_compte() -> None:
    """On doit pouvoir lire le traitement des données avant d'y consentir.

    Un `login_required` ici renverrait 302 vers la connexion, donc obligerait
    à créer un compte pour savoir ce qu'on accepte.
    """
    reponse = _client().get("/rgpd")  # ty: ignore[unresolved-attribute]
    assert reponse.status_code == _HTTP_OK


def test_la_page_de_connexion_mene_au_rgpd() -> None:
    """Sinon la page est publique mais introuvable pour qui n'a pas de compte."""
    reponse = _client().get("/auth/login")  # ty: ignore[unresolved-attribute]
    assert 'href="/rgpd"' in reponse.get_data(as_text=True)


def test_le_menu_profil_mene_a_la_page_rgpd() -> None:
    """Sans le lien, la route existe mais reste inatteignable."""
    index = (_TEMPLATES / "index.html").read_text(encoding="utf-8")
    assert 'href="/rgpd"' in index


def test_nouvelle_conv_suit_la_route_courante() -> None:
    """Régression : le bouton repartait sur / et faisait quitter le chal.

    Le gabarit ne doit pas coder la racine en dur ; c'est la vue qui décide,
    et elle sert `/ctf/<chal>` quand on est sur un challenge.
    """
    index = (_TEMPLATES / "index.html").read_text(encoding="utf-8")
    assert 'href="{{ nouvelle_conv | default(\'/\') }}" class="new-btn"' in index
    assert '<a href="/" class="new-btn">' not in index


def test_la_vue_ctf_passe_sa_propre_route() -> None:
    """La valeur servie au gabarit doit être celle du chal, pas la racine."""
    source = (Path(__file__).resolve().parent.parent / "app" / "routes.py").read_text(
        encoding="utf-8"
    )
    assert 'nouvelle_conv=f"/ctf/{chal}"' in source
