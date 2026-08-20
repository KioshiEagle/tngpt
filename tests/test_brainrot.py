"""Tests du mode brainrot : la couche s'ajoute au chat sans en défaire les règles.

Sans réseau : on n'inspecte que le prompt et le CallSpec construits à l'import.
"""

from app.back.brainrot import (
    BRAINROT_PROMPT_PATH,
    BRAINROT_SPEC,
    BRAINROT_SYSTEM,
)
from app.back.generate import CHAT_GROQ_PARAMS, CHAT_SPEC, CHAT_SYSTEM, build_prompt


def test_prompt_present() -> None:
    """Le fichier de prompt brainrot est livré à côté du module."""
    assert BRAINROT_PROMPT_PATH.is_file()


def test_couche_ajoutee_au_prompt_du_chat() -> None:
    """Le prompt brainrot prolonge celui du chat, il ne le remplace pas.

    C'est ce qui garde l'ancrage factuel et le périmètre en vigueur : sans le
    prompt de base, le mode deviendrait un chat sans règles.
    """
    assert BRAINROT_SYSTEM.startswith(CHAT_SYSTEM)
    assert len(BRAINROT_SYSTEM) > len(CHAT_SYSTEM)
    assert "brainrot_mode" in BRAINROT_SYSTEM


def test_renvoi_hors_perimetre_inchange() -> None:
    """La phrase de hors périmètre reste littérale dans les deux modes."""
    renvoi = "demande à chatgpt, me casse pas les couilles"
    assert renvoi in CHAT_SYSTEM
    assert renvoi in BRAINROT_SYSTEM


def test_spec_reste_un_chat() -> None:
    """Le spec ne change que le prompt et la température, pas la mécanique."""
    assert BRAINROT_SPEC.params == CHAT_GROQ_PARAMS
    assert BRAINROT_SPEC.build is build_prompt
    assert BRAINROT_SPEC.consume is CHAT_SPEC.consume
    assert BRAINROT_SPEC.send_history is True


def test_temperature_plus_haute_que_le_chat() -> None:
    """La voix brainrot a besoin de latitude, là où le chat restitue."""
    assert BRAINROT_SPEC.temperature > CHAT_SPEC.temperature
