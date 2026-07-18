import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from .models import Query, RetrievalEvent, User, db
from .types import SearchResult

logger = logging.getLogger(__name__)

_MAX_QUESTION = 500
_MAX_TITLE = 300
_MAX_SOURCE = 128


@dataclass
class QuotaStatus:
    """État du quota journalier d'un utilisateur.

    `limit` et `remaining` valent None quand l'utilisateur n'est pas plafonné
    (un administrateur).
    """

    used: int
    limit: int | None
    remaining: int | None

    @property
    def exceeded(self) -> bool:
        """Indique si le quota journalier est atteint."""
        return self.limit is not None and self.used >= self.limit


def _day_start() -> datetime:
    """Début du jour calendaire courant (UTC) : borne de comptage du quota."""
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_quota(user: User) -> int | None:
    """Limite quotidienne effective : surcharge utilisateur, sinon défaut global.

    Renvoie None pour un administrateur, qui n'est jamais plafonné — sinon il se
    bloquerait lui-même en testant l'application.
    """
    if user.is_admin():
        return None
    if user.quota_daily is not None:
        return user.quota_daily
    return current_app.config["DEFAULT_DAILY_QUOTA"]


def questions_today(user_id: int) -> int:
    """Nombre de questions posées par l'utilisateur depuis minuit (UTC)."""
    return (
        db.session.scalar(
            db.select(db.func.count(Query.query_id)).where(
                Query.user_id == user_id,
                Query.created_at >= _day_start(),
            )
        )
        or 0
    )


def quota_status(user: User) -> QuotaStatus:
    """Usage du jour, limite effective et solde restant pour un utilisateur."""
    limit = daily_quota(user)
    used = questions_today(user.user_id)
    remaining = None if limit is None else max(limit - used, 0)
    return QuotaStatus(used=used, limit=limit, remaining=remaining)


def seconds_until_reset() -> int:
    """Secondes avant la remise à zéro du quota (prochain minuit UTC)."""
    now = datetime.now(UTC)
    tomorrow = _day_start() + timedelta(days=1)
    return int((tomorrow - now).total_seconds())


def _truncate(value: object, length: int) -> str | None:
    """Tronque une métadonnée Qdrant à la taille de sa colonne."""
    if not value:
        return None
    return str(value)[:length]


def log_retrieval(
    user_id: int,
    question: str,
    top_k: int,
    results: list[SearchResult],
) -> int | None:
    """Journalise une question et les chunks retrouvés, et renvoie l'id de requête.

    N'échoue jamais : le monitoring ne doit pas pouvoir casser le chat. Une
    erreur d'écriture est tracée puis absorbée, et la réponse part quand même.
    """
    try:
        query = Query(
            user_id=user_id,
            question=question[:_MAX_QUESTION],
            top_k=top_k,
            result_count=len(results),
        )
        db.session.add(query)
        db.session.flush()  # attribue query_id sans valider la transaction

        for rank, result in enumerate(results, start=1):
            metadata = result["metadata"]
            db.session.add(
                RetrievalEvent(
                    query_id=query.query_id,
                    point_id=result["point_id"],
                    source_id=_truncate(metadata.get("source"), _MAX_SOURCE),
                    title=_truncate(metadata.get("title"), _MAX_TITLE),
                    rank=rank,
                    score=result["score"],
                    semantic_score=result["semantic_score"],
                    freshness_score=result["freshness_score"],
                )
            )

        db.session.commit()
    except SQLAlchemyError:
        logger.exception("Échec de journalisation du retrieval (question ignorée)")
        db.session.rollback()
        return None
    else:
        return query.query_id
