import logging

from sqlalchemy.exc import SQLAlchemyError

from .models import Query, RetrievalEvent, db
from .types import SearchResult

logger = logging.getLogger(__name__)

_MAX_QUESTION = 500
_MAX_TITLE = 300
_MAX_SOURCE = 128


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
