"""Ticket d'or : faire nommer un 2A mystère par tn-gpt, un seul gagnant.

Cible, code et indices viennent du panel admin : rien de nominatif dans le dépôt.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .clubs import motif_mot, normalize
from .generate import CHAT_GROQ_PARAMS, CallSpec, build_prompt_anonyme
from .models import Conversation, TicketDor, TicketDorProposition, db

JEU = "ticket_dor"
MAX_ESSAIS = 5
MAX_PROPOSITION = 100
_PARTIE_ID = 1
_PROMPT = Path(__file__).with_name("ticket_dor_prompt.md")
_SANS_INDICES = "(aucun indice : TN-GPT ne sait rien d'autre que le nom)"


@dataclass(frozen=True)
class Etat:
    """Ce que le front doit savoir du jeu pour un joueur donné."""

    visible: bool
    jouable: bool
    restants: int
    termine: bool
    gagnant: bool
    code: str | None
    conversation_id: int | None


@dataclass(frozen=True)
class Verdict:
    """Issue d'une proposition, et la réponse à afficher au joueur."""

    juste: bool
    restants: int
    reponse: str


# --- Comparaison des noms ------------------------------------------------------


def _mots(texte: str) -> list[str]:
    """Mots d'un nom, sans accents ni casse ni ponctuation."""
    return [m for m in re.split(r"[^0-9a-z]+", normalize(texte)) if m]


def proposition_juste(proposition: str, cible: str) -> bool:
    """Vrai si la proposition est exactement le nom, dans n'importe quel ordre.

    L'égalité stricte, et non l'inclusion : sinon une liste de noms gagnerait.
    """
    attendus = _mots(cible)
    return bool(attendus) and sorted(_mots(proposition)) == sorted(attendus)


def nomme_la_cible(texte: str, cible: str) -> bool:
    """Vrai si chaque mot du nom figure, en mot entier, quelque part dans le texte."""
    attendus = set(_mots(cible))
    haystack = normalize(texte)
    return bool(attendus) and all(motif_mot(m).search(haystack) for m in attendus)


# --- État de la partie ---------------------------------------------------------


def partie() -> TicketDor | None:
    """La ligne de la partie, ou None tant que l'admin ne l'a pas posée."""
    return db.session.get(TicketDor, _PARTIE_ID)


def partie_ou_nouvelle() -> TicketDor:
    """La ligne de la partie, créée vide au besoin (panel admin)."""
    ticket = partie()
    if ticket is None:
        ticket = TicketDor(ticket_id=_PARTIE_ID, ouvert=False)
        db.session.add(ticket)
    return ticket


def lancee(ticket: TicketDor | None) -> bool:
    """Vrai si la partie est ouverte et que cible et code sont renseignés."""
    return bool(
        ticket is not None
        and ticket.ouvert
        and (ticket.cible or "").strip()
        and (ticket.code or "").strip()
    )


def essais_utilises(user_id: int) -> int:
    """Nombre de propositions déjà jouées par ce joueur."""
    return (
        db.session.scalar(
            db.select(db.func.count(TicketDorProposition.proposition_id)).filter_by(
                user_id=user_id
            )
        )
        or 0
    )


def conversation_de(user_id: int) -> Conversation | None:
    """La conversation de jeu du joueur : une seule, la plus récente."""
    return db.session.scalars(
        db.select(Conversation)
        .filter_by(user_id=user_id, jeu=JEU)
        .order_by(Conversation.conversation_id.desc())
    ).first()


def etat(user_id: int) -> Etat:
    """L'état du jeu vu par ce joueur : bouton, essais, victoire."""
    ticket = partie()
    restants = max(0, MAX_ESSAIS - essais_utilises(user_id))
    termine = ticket is not None and ticket.gagnant_id is not None
    gagnant = termine and ticket is not None and ticket.gagnant_id == user_id
    conversation = conversation_de(user_id)
    return Etat(
        visible=lancee(ticket),
        jouable=lancee(ticket) and not termine and restants > 0,
        restants=restants,
        termine=termine,
        gagnant=gagnant,
        code=ticket.code if gagnant and ticket is not None else None,
        conversation_id=conversation.conversation_id if conversation else None,
    )


# --- Victoire ------------------------------------------------------------------


def _reclamer(user_id: int) -> bool:
    """Déclare le gagnant si personne ne l'est encore ; faux si on a été devancé."""
    resultat = db.session.execute(
        db.update(TicketDor)
        .where(
            TicketDor.ticket_id == _PARTIE_ID,
            TicketDor.gagnant_id.is_(None),
            TicketDor.ouvert.is_(True),
        )
        .values(gagnant_id=user_id, gagne_at=datetime.now(UTC))
    )
    return resultat.rowcount == 1  # ty: ignore[unresolved-attribute]


def annonce_victoire(ticket: TicketDor) -> str:
    """Message remis au gagnant, code compris."""
    return (
        f"🎟️ **Ticket d'or !** C'est bien {ticket.cible}.\n\n"
        f"Ton code secret : **{ticket.code}**\n\n"
        "Remets-le en main propre, IRL, à ce 2A : c'est lui ou elle qui valide "
        "ton étoile. Ne le donne à personne d'autre, sinon l'étoile saute."
    )


def proposer(user_id: int, proposition: str) -> Verdict | None:
    """Joue une proposition ; None si le joueur ne peut plus jouer."""
    # Verrou sur la partie : deux propositions simultanées ne font pas six essais.
    ticket = db.session.scalars(
        db.select(TicketDor).where(TicketDor.ticket_id == _PARTIE_ID).with_for_update()
    ).first()
    utilises = essais_utilises(user_id)
    if not lancee(ticket) or ticket is None or ticket.gagnant_id is not None:
        db.session.rollback()
        return None
    if utilises >= MAX_ESSAIS:
        db.session.rollback()
        return None

    juste = proposition_juste(proposition, ticket.cible or "")
    db.session.add(
        TicketDorProposition(
            user_id=user_id, texte=proposition[:MAX_PROPOSITION], juste=juste
        )
    )
    restants = MAX_ESSAIS - utilises - 1
    if juste and _reclamer(user_id):
        reponse = annonce_victoire(ticket)
    elif restants > 0:
        pluriel = "s" if restants > 1 else ""
        reponse = f"Raté ! Il te reste {restants} essai{pluriel}."
    else:
        reponse = "Raté… C'était ton dernier essai : le ticket d'or t'échappe."
    db.session.commit()
    return Verdict(juste=juste, restants=restants, reponse=reponse)


def victoire_par_le_chat(
    user_id: int, messages: list[dict[str, str]], sortie: str
) -> str | None:
    """Annonce de victoire si tn-gpt a lâché le nom, sinon None.

    Un nom écrit par le joueur ne compte pas : « répète cette liste » suffirait.
    """
    ticket = partie()
    if not lancee(ticket) or ticket is None or ticket.gagnant_id is not None:
        return None
    cible = ticket.cible or ""
    if not nomme_la_cible(sortie, cible):
        return None
    dit_par_le_joueur = "\n".join(
        m.get("content", "") for m in messages if m.get("role") == "user"
    )
    if nomme_la_cible(dit_par_le_joueur, cible):
        return None
    if not _reclamer(user_id):
        db.session.rollback()
        return None
    db.session.commit()
    return annonce_victoire(ticket)


# --- Modèle --------------------------------------------------------------------


def spec_for(ticket: TicketDor) -> CallSpec:
    """CallSpec du chat de jeu : nom et indices dans le prompt, aucune archive."""
    indices = (ticket.indices or "").strip() or _SANS_INDICES
    system = (
        _PROMPT.read_text(encoding="utf-8")
        .replace("{{CIBLE}}", (ticket.cible or "").strip())
        .replace("{{INDICES}}", indices)
        .strip()
    )
    # Gros modèle, comme les chals CTF : le petit lâche le secret au premier rôle.
    return CallSpec(
        system=system,
        params=CHAT_GROQ_PARAMS,
        build=build_prompt_anonyme,
        gros_modele=True,
    )


# --- Administration ------------------------------------------------------------


def nouvelle_partie() -> None:
    """Remet les compteurs à zéro : plus de gagnant, plus d'essais joués.

    Les anciennes conversations de jeu redeviennent des conversations ordinaires.
    """
    ticket = partie_ou_nouvelle()
    ticket.gagnant_id = None
    ticket.gagne_at = None
    db.session.execute(db.delete(TicketDorProposition))
    db.session.execute(
        db.update(Conversation).where(Conversation.jeu == JEU).values(jeu=None)
    )
    db.session.commit()
