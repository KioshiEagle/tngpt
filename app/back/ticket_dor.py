"""Ticket d'or : faire nommer un 2A mystère par tn-gpt, un seul gagnant.

Cible, code et indices viennent du panel admin : rien de nominatif dans le dépôt.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path

from .clubs import motif_mot, normalize
from .generate import (
    CHAT_GROQ_PARAMS,
    CallSpec,
    CompletionConsumer,
    _stream_chunks,
    build_prompt_anonyme,
)
from .llm import Chunk
from .models import Conversation, TicketDor, TicketDorProposition, User, db

JEU = "ticket_dor"
MAX_ESSAIS = 5
# Un indice de plus toutes les N tentatives (questions + propositions confondues).
_TENTATIVES_PAR_INDICE = 6
MAX_PROPOSITION = 100
# Prénom et nom : un mot seul viserait plusieurs personnes et brûlerait un essai.
MIN_MOTS_PROPOSITION = 2
# Fautes de frappe tolérées par mot : aucune jusqu'à 3 lettres, 2 dès 8, sinon 1.
_MOT_COURT = 3
_MOT_LONG = 8
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
    indices_debloques: int = 0
    indices_total: int = 0


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


def proposition_valide(proposition: str) -> bool:
    """Vrai si la proposition compte au moins un prénom et un nom."""
    return len(_mots(proposition)) >= MIN_MOTS_PROPOSITION


def _fautes(a: str, b: str) -> int:
    """Distance d'édition, une inversion de deux lettres voisines comptant pour une."""
    precedente, ligne = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        courante = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            courante[j] = min(
                ligne[j] + 1, courante[j - 1] + 1, ligne[j - 1] + (ca != cb)
            )
            if precedente and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                courante[j] = min(courante[j], precedente[j - 2] + 1)
        precedente, ligne = ligne, courante
    return ligne[-1]


def _tolerance(mot: str) -> int:
    """Fautes admises sur un mot : aucune s'il est court, sinon « Léa » vaut « Léo »."""
    if len(mot) <= _MOT_COURT:
        return 0
    return 1 if len(mot) < _MOT_LONG else 2


def proposition_juste(proposition: str, cible: str) -> bool:
    """Vrai si la proposition est le nom, à des fautes de frappe près, en tout ordre.

    Autant de mots que le nom, pas plus : sinon une liste de noms gagnerait.
    """
    attendus, proposes = _mots(cible), _mots(proposition)
    if not attendus or len(proposes) != len(attendus):
        return False
    return any(
        all(
            _fautes(p, a) <= _tolerance(a) for p, a in zip(ordre, attendus, strict=True)
        )
        for ordre in permutations(proposes)
    )


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


def tentatives(user_id: int) -> int:
    """Tentatives au sens large : chaque message envoyé, question ou proposition."""
    conversation = conversation_de(user_id)
    if conversation is None:
        return 0
    return sum(1 for m in conversation.messages if m.get("role") == "user")


def est_dispense(user_id: int) -> bool:
    """Vrai si ce compte est dispensé du jeu (2A/3A cochés en admin)."""
    user = db.session.get(User, user_id)
    return bool(user and user.ticket_dor_dispense)


def conversation_de(user_id: int) -> Conversation | None:
    """La conversation de jeu du joueur : une seule, la plus récente."""
    return db.session.scalars(
        db.select(Conversation)
        .filter_by(user_id=user_id, jeu=JEU)
        .order_by(Conversation.conversation_id.desc())
    ).first()


def liste_indices(ticket: TicketDor | None) -> list[str]:
    """Les indices saisis en admin, un par ligne, puces retirées."""
    lignes = (ticket.indices or "").splitlines() if ticket is not None else []
    return [ligne.strip().lstrip("-•*").strip() for ligne in lignes if ligne.strip()]


def indices_debloques(ticket: TicketDor | None, tentatives_faites: int) -> list[str]:
    """Un indice d'office, puis un de plus par tranche de `_TENTATIVES_PAR_INDICE`."""
    return liste_indices(ticket)[: 1 + tentatives_faites // _TENTATIVES_PAR_INDICE]


def etat(user_id: int) -> Etat:
    """L'état du jeu vu par ce joueur : bouton, essais, victoire."""
    ticket = partie()
    utilises = essais_utilises(user_id)
    restants = max(0, MAX_ESSAIS - utilises)
    termine = ticket is not None and ticket.gagnant_id is not None
    gagnant = termine and ticket is not None and ticket.gagnant_id == user_id
    conversation = conversation_de(user_id)
    # Un dispensé (2A/3A) ne voit ni ne joue, sauf pour constater une victoire passée.
    ouvert_pour_lui = lancee(ticket) and (not est_dispense(user_id) or gagnant)
    return Etat(
        visible=ouvert_pour_lui,
        jouable=ouvert_pour_lui and not termine and restants > 0,
        restants=restants,
        termine=termine,
        gagnant=gagnant,
        code=ticket.code if gagnant and ticket is not None else None,
        conversation_id=conversation.conversation_id if conversation else None,
        indices_debloques=len(indices_debloques(ticket, tentatives(user_id))),
        indices_total=len(liste_indices(ticket)),
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
        # La proposition compte comme une tentative de plus : un indice peut tomber.
        faites = tentatives(user_id)
        if len(indices_debloques(ticket, faites + 1)) > len(
            indices_debloques(ticket, faites)
        ):
            reponse += " Un nouvel indice est débloqué : demande-le à tn-gpt."
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


def _consommateur(user_id: int, conversation_id: int) -> CompletionConsumer:
    """Lecteur du chat de jeu : la réponse, puis l'annonce si le nom y a échappé."""

    def consume(completion: Iterator[Chunk]) -> Iterator[str]:
        sortie = ""
        for morceau in _stream_chunks(completion):
            sortie += morceau
            yield morceau
        conversation = db.session.get(Conversation, conversation_id)
        messages = conversation.messages if conversation is not None else []
        annonce = victoire_par_le_chat(user_id, messages, sortie)
        if annonce:
            yield f"\n\n{annonce}"

    return consume


def spec_for(ticket: TicketDor, user_id: int, conversation_id: int) -> CallSpec:
    """CallSpec du chat de jeu : nom et indices dans le prompt, aucune archive."""
    # Le modèle ne voit que les indices débloqués : insister n'en arrache pas d'autres.
    debloques = indices_debloques(ticket, tentatives(user_id))
    indices = "\n".join(f"- {i}" for i in debloques) or _SANS_INDICES
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
        consume=_consommateur(user_id, conversation_id),
        gros_modele=True,
    )


# --- Administration ------------------------------------------------------------


@dataclass(frozen=True)
class NomPropose:
    """Un nom proposé, toutes graphies confondues, et combien de fois."""

    nom: str
    nombre: int
    joueurs: int
    juste: bool


def joueurs_et_dispenses() -> list[User]:
    """Tous les comptes, pour cocher qui est dispensé du jeu ; par nom."""
    return list(
        db.session.scalars(
            db.select(User).order_by(User.user_surname, User.user_firstname)
        )
    )


def regler_dispenses(user_ids: set[str], admin_id: int) -> int:
    """Coche les comptes dispensés et décoche les autres ; rend le total coché."""
    del admin_id
    vises = {int(i) for i in user_ids if i.isdigit()}
    dispenses = 0
    for user in db.session.scalars(db.select(User)):
        user.ticket_dor_dispense = user.user_id in vises
        dispenses += user.ticket_dor_dispense
    db.session.commit()
    return dispenses


def classement_propositions() -> list[NomPropose]:
    """Les noms proposés, du plus au moins joué ; casse, accents et ordre confondus."""
    groupes: dict[tuple[str, ...], list[TicketDorProposition]] = {}
    for proposition in db.session.scalars(
        db.select(TicketDorProposition).order_by(TicketDorProposition.created_at)
    ):
        cle = tuple(sorted(_mots(proposition.texte)))
        groupes.setdefault(cle, []).append(proposition)
    classement = [
        NomPropose(
            # La graphie la plus récente : c'est celle qu'on vient de voir passer.
            nom=props[-1].texte,
            nombre=len(props),
            joueurs=len({p.user_id for p in props}),
            juste=any(p.juste for p in props),
        )
        for props in groupes.values()
    ]
    return sorted(classement, key=lambda n: n.nombre, reverse=True)


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
