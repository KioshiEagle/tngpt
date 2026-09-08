"""Routes de pages, et ce que leurs gabarits en attendent."""

from pathlib import Path

from tests.conftest import creer_app

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "front" / "templates"

_HTTP_OK = 200


def _adapter() -> object:
    """Table des URL de l'application, liée à un hôte quelconque."""
    return creer_app().url_map.bind("localhost")


def _client() -> object:
    """Client de test d'une application jetable."""
    return creer_app().test_client()


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


def test_le_prompt_interdit_le_vocabulaire_de_plomberie() -> None:
    """« je trouve pas dans mes archives » ne veut rien dire pour un élève.

    La règle existait en prose ; elle nomme désormais les mots proscrits, ce qui
    la rend vérifiable — ici comme à la lecture d'une réponse.
    """
    prompt = (
        Path(__file__).resolve().parent.parent / "app" / "back" / "system_prompt.md"
    ).read_text(encoding="utf-8")
    assert "ne paraissent jamais dans une réponse" in prompt
    for mot in ("« archive »", "« source »", "« document »"):
        assert mot in prompt, f"le mot {mot} n'est plus listé comme proscrit"


def test_le_prompt_borne_les_sources_au_jour_demande() -> None:
    """Un mail de février répondait à « c'est quoi l'event de ce soir ».

    La règle d'édition ne jouait qu'à l'année ; celle-ci descend au jour, seule
    échelle qui écarte une annonce du 18 février quand on est le 8 septembre.
    """
    prompt = (
        Path(__file__).resolve().parent.parent / "app" / "back" / "system_prompt.md"
    ).read_text(encoding="utf-8")
    assert "Le même piège existe au jour près" in prompt
    assert "planning de la période en cours" in prompt
