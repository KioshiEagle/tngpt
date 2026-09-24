"""Filtrage par fournisseur du tirage du pool : le chal RAG en dépend.

Sans lui, le round-robin peut tirer une clé Cerebras ou Mistral pour ce chal :
`adapter_params` y tait le raisonnement, seul canal de fuite de l'épreuve.
"""

from collections.abc import Iterator

import pytest
from flask import Flask

from app.back import groqpool
from app.back.fournisseurs import (
    CEREBRAS,
    DEEPSEEK,
    GROQ,
    MISTRAL,
    RAISONNEMENT_VISIBLE,
)
from app.back.models import GroqKey, db


@pytest.fixture
def app_base(monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    """Application jetable sur une base SQLite en mémoire, cache de clients vidé.

    Le cache de clients est global au module : sans le vider, un id de clé
    réutilisé d'un test à l'autre renverrait le client d'un fournisseur défunt.
    """
    monkeypatch.setattr(groqpool, "_clients", {})
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def _ajouter(secret: str, fournisseur: str | None) -> GroqKey:
    """Ajoute une clé active au pool et la renvoie, déjà persistée."""
    cle = GroqKey(secret=secret, fournisseur=fournisseur, active=True)
    db.session.add(cle)
    db.session.commit()
    return cle


@pytest.mark.usefixtures("app_base")
def test_sans_filtre_le_tirage_ignore_le_fournisseur() -> None:
    """Round-robin ordinaire : la clé la moins utilisée sort, fournisseur ignoré."""
    _ajouter("csk-000000000000000", CEREBRAS)
    client, _ = groqpool.acquire()
    assert groqpool.fournisseur_du_client(client) == CEREBRAS


@pytest.mark.usefixtures("app_base")
def test_le_filtre_saute_les_fournisseurs_muets() -> None:
    """Régression : le chal RAG ne doit jamais tirer une clé qui tait le raisonnement.

    Cerebras, moins récemment utilisée (`last_used_at` nul en premier), sortirait
    en tête sans le filtre — silencieusement, et le chal serait mort.
    """
    _ajouter("csk-000000000000000", CEREBRAS)
    groq_key = _ajouter("gsk_QH0000000000000000", GROQ)
    client, key_id = groqpool.acquire(RAISONNEMENT_VISIBLE)
    assert groqpool.fournisseur_du_client(client) == GROQ
    assert key_id == groq_key.groq_key_id


@pytest.mark.usefixtures("app_base")
def test_deepseek_reste_autorise_par_le_filtre() -> None:
    """DeepSeek laisse fuir `reasoning_content` : il ne doit pas être écarté."""
    _ajouter("sk-00000000000000000", DEEPSEEK)
    client, _ = groqpool.acquire(RAISONNEMENT_VISIBLE)
    assert groqpool.fournisseur_du_client(client) == DEEPSEEK


@pytest.mark.usefixtures("app_base")
def test_aucune_clef_autorisee_retombe_sur_groq() -> None:
    """Pool tout Cerebras/Mistral : le repli sur `GROQ_API_KEY` reste sûr."""
    _ajouter("csk-000000000000000", CEREBRAS)
    _ajouter("m-secret-opaque", MISTRAL)
    client, key_id = groqpool.acquire(RAISONNEMENT_VISIBLE)
    assert groqpool.fournisseur_du_client(client) == GROQ
    assert key_id is None
