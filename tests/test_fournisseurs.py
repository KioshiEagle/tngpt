"""Reconnaissance du fournisseur d'une clé, et ce qu'on en déduit."""

from inspect import signature

import pytest
from groq.resources.chat.completions import Completions as GroqCompletions
from openai.resources.chat.completions import Completions as OpenAICompletions

from app.back.ctf import RAG_GROQ_PARAMS
from app.back.fournisseurs import (
    CEREBRAS,
    DEEPSEEK,
    GROQ,
    MISTRAL,
    adapter_params,
    fournisseur,
    modeles,
    resoudre,
)
from app.back.generate import CHAT_GROQ_PARAMS
from app.back.groqpool import _construire, fournisseur_du_client
from app.back.seamap import MAP_GROQ_PARAMS
from app.back.types import GroqParams


@pytest.mark.parametrize(
    ("secret", "attendu"),
    [
        ("gsk_QH0000000000000000", GROQ),
        ("sk-0000000000000000", DEEPSEEK),
        # « csk- » se termine par « sk- » : le piège que `startswith` évite.
        ("csk-k3t0000000000000", CEREBRAS),
    ],
)
def test_prefixe_reconnu(secret: str, attendu: str) -> None:
    """Chaque préfixe connu désigne son fournisseur."""
    assert fournisseur(secret) == attendu


@pytest.mark.parametrize("secret", ["", "   ", "xx-0000", "GSK_0000", None])
def test_prefixe_inconnu(secret: str | None) -> None:
    """Un préfixe non reconnu ne désigne aucun fournisseur.

    None plutôt que Groq : une clé mal reconnue partirait sur la mauvaise API
    et n'échouerait qu'en 401, très loin d'ici.
    """
    assert fournisseur(secret) is None  # ty: ignore[invalid-argument-type]


def test_secret_entoure_d_espaces() -> None:
    """Un secret copié-collé avec des espaces reste reconnu."""
    assert fournisseur("  sk-0000000000000000\n") == DEEPSEEK


# --- Du secret au client -----------------------------------------------------


@pytest.mark.parametrize(
    ("secret", "attendu"),
    [
        ("gsk_QH0000000000000000", GROQ),
        ("sk-0000000000000000", DEEPSEEK),
        ("csk-k3t0000000000000", CEREBRAS),
    ],
)
def test_le_client_pointe_sur_le_bon_hote(secret: str, attendu: str) -> None:
    """Le client construit vise l'API du fournisseur, et se laisse relire."""
    assert fournisseur_du_client(_construire(secret)) == attendu


def test_client_sans_hote_connu_est_traite_en_groq() -> None:
    """Un double de test n'a pas de `base_url` : il garde le chemin historique."""

    class _Faux:
        pass

    assert fournisseur_du_client(_Faux()) == GROQ  # ty: ignore[invalid-argument-type]


# --- Modèles -----------------------------------------------------------------


def test_le_modele_suit_le_fournisseur() -> None:
    """Une clé DeepSeek ne doit jamais partir avec un id de modèle Groq."""
    principal, _ = modeles(DEEPSEEK)
    assert principal.startswith("deepseek-")
    assert "qwen" not in principal


# --- Fournisseur déclaré par le pool ------------------------------------------

_CLE_MISTRAL = "cle-de-test-sans-prefixe-reconnaissable"


def test_le_declare_l_emporte_sur_le_prefixe() -> None:
    """Sans quoi une clé Mistral opaque partirait chez Groq, et ferait un 401."""
    assert resoudre(_CLE_MISTRAL, MISTRAL) == MISTRAL


def test_sans_declaration_le_prefixe_decide_encore() -> None:
    """Les clés d'avant la colonne restent reconnues comme avant."""
    assert resoudre("gsk_QH0000000000000000", None) == GROQ
    assert resoudre("sk-0000000000000000", None) == DEEPSEEK


def test_une_declaration_inconnue_ne_fait_pas_autorite() -> None:
    """Une valeur hors liste retombe sur le préfixe plutôt que d'être suivie."""
    assert resoudre("gsk_QH0000000000000000", "azure") == GROQ
    assert resoudre(_CLE_MISTRAL, "azure") is None


def test_une_cle_declaree_mistral_vise_le_bon_hote() -> None:
    """Bout en bout : la déclaration doit survivre jusqu'à l'URL appelée."""
    client = _construire(_CLE_MISTRAL, MISTRAL)
    assert fournisseur_du_client(client) == MISTRAL
    assert "api.mistral.ai" in str(client.base_url)


def test_le_modele_mistral_suit_son_fournisseur() -> None:
    """Aucun id Groq ni DeepSeek ne doit partir vers api.mistral.ai."""
    principal, repli = modeles(MISTRAL)
    assert principal.startswith("mistral-")
    assert repli.startswith("mistral-")
    assert "qwen" not in principal


def test_mistral_a_un_repli_distinct() -> None:
    """Principal et repli séparés : `repli_possible` peut donc jouer."""
    principal, repli = modeles(MISTRAL)
    assert principal != repli


def test_une_cle_mistral_n_est_pas_reconnue_au_prefixe() -> None:
    """Ses clés sont opaques : les deviner enverrait le pool au mauvais hôte.

    Garde-fou du choix documenté dans `fournisseurs` : Mistral se construit
    explicitement, et une clé inconnue vaut None plutôt qu'une supposition.
    """
    assert fournisseur("cle-de-test-sans-prefixe-reconnaissable") is None


def test_deepseek_n_a_pas_de_modele_de_repli() -> None:
    """Sa limite se compte en connexions simultanées, pas en quota par modèle.

    Principal et repli confondus : `repli_possible` reste donc faux et
    l'échelle temporise au lieu de basculer pour rien.
    """
    principal, repli = modeles(DEEPSEEK)
    assert principal == repli


# --- Traduction des paramètres ------------------------------------------------


def test_groq_garde_ses_parametres_intacts() -> None:
    """Le fournisseur historique ne traverse aucune traduction."""
    assert adapter_params(CHAT_GROQ_PARAMS, GROQ) == CHAT_GROQ_PARAMS


def test_deepseek_ne_recoit_jamais_reasoning_format() -> None:
    """Le paramètre est propre à Groq : l'envoyer vaut un 400."""
    for params in (CHAT_GROQ_PARAMS, RAG_GROQ_PARAMS):
        assert "reasoning_format" not in adapter_params(params, DEEPSEEK)


def test_le_chat_desactive_le_raisonnement_chez_deepseek() -> None:
    """« effort none » côté Groq se dit `thinking disabled` côté DeepSeek."""
    adapte = adapter_params(CHAT_GROQ_PARAMS, DEEPSEEK)
    assert adapte["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in adapte


def test_le_chal_rag_garde_son_raisonnement_visible() -> None:
    """Le canal de fuite du chal 3 doit survivre à la traduction."""
    adapte = adapter_params(RAG_GROQ_PARAMS, DEEPSEEK)
    assert adapte["extra_body"]["thinking"] == {"type": "enabled"}
    # L'outil scellé est le cœur du chal : il ne doit pas tomber en route.
    assert adapte["tools"] == RAG_GROQ_PARAMS["tools"]
    assert adapte["max_completion_tokens"] == RAG_GROQ_PARAMS["max_completion_tokens"]


def test_un_effort_du_vocabulaire_groq_est_retire_et_non_traduit() -> None:
    """« default » n'existe pas chez DeepSeek : mieux vaut l'omettre.

    `thinking` porte déjà l'essentiel ; inventer une correspondance vaudrait un
    400 sur une valeur que personne n'a demandée.
    """
    adapte = adapter_params({"reasoning_effort": "default"}, DEEPSEEK)
    assert adapte["extra_body"]["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in adapte


def test_mistral_ne_recoit_pas_le_vocabulaire_groq() -> None:
    """Ni `reasoning_format` ni `thinking` : Mistral refuserait les deux."""
    adapte = adapter_params(CHAT_GROQ_PARAMS, MISTRAL)
    assert "reasoning_format" not in adapte
    assert "reasoning_effort" not in adapte
    assert "extra_body" not in adapte


def test_le_chal_rag_garde_son_outil_chez_mistral() -> None:
    """L'outil scellé est le cœur du chal 3 : le filtrage ne doit pas l'emporter."""
    adapte = adapter_params(RAG_GROQ_PARAMS, MISTRAL)
    assert adapte["tools"] == RAG_GROQ_PARAMS["tools"]
    assert adapte["max_completion_tokens"] == RAG_GROQ_PARAMS["max_completion_tokens"]


def test_un_effort_commun_aux_deux_est_conserve() -> None:
    """« low » est valide des deux côtés : rien à retirer."""
    adapte = adapter_params({"reasoning_effort": "low"}, DEEPSEEK)
    assert adapte["reasoning_effort"] == "low"


def test_fournisseur_inconnu_perd_le_vocabulaire_groq_sans_rien_inventer() -> None:
    """On retire ce qui est propre à Groq, sans supposer le reste."""
    adapte = adapter_params(CHAT_GROQ_PARAMS, CEREBRAS)
    assert "reasoning_format" not in adapte
    assert "extra_body" not in adapte


# --- Garde-fou : ce qu'on émet doit exister dans la signature du SDK ----------

# Ajoutés par `_attempt` autour des paramètres de la spec.
_PARAMS_FIXES = frozenset({"model", "messages", "temperature", "stream"})


@pytest.mark.parametrize(
    ("nom", "params"),
    [
        ("chat", CHAT_GROQ_PARAMS),
        ("chal rag", RAG_GROQ_PARAMS),
        ("carte", MAP_GROQ_PARAMS),
    ],
)
@pytest.mark.parametrize(
    ("cible", "completions"),
    [(GROQ, GroqCompletions), (DEEPSEEK, OpenAICompletions)],
)
def test_aucun_parametre_hors_signature_du_sdk(
    nom: str,
    params: GroqParams,
    cible: str,
    completions: type[GroqCompletions] | type[OpenAICompletions],
) -> None:
    """Un paramètre hors signature lève un TypeError avant tout appel réseau.

    C'est ainsi que `thinking` est passé : accepté par DeepSeek, mais refusé
    par le SDK lui-même. Les extensions d'un fournisseur passent par
    `extra_body`, que ce test ne franchit volontairement pas.
    """
    attendus = set(signature(completions.create).parameters)
    emis = set(adapter_params(params, cible)) | _PARAMS_FIXES
    assert emis <= attendus, f"{nom} → {cible} : {sorted(emis - attendus)}"
