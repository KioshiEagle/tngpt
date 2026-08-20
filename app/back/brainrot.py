"""Mode brainrot : le prompt du chat, plus une couche de voix dégénérée.

Activé par un toggle côté front (voir `routes.chat`). La couche s'ajoute au
prompt normal au lieu de le remplacer : l'ancrage factuel reste en vigueur.
"""

from pathlib import Path

from .generate import CHAT_GROQ_PARAMS, CHAT_SYSTEM, CallSpec

BRAINROT_PROMPT_PATH = Path(__file__).with_name("brainrot_prompt.md")

# Lu à l'import, comme CHAT_SYSTEM : le prompt ne varie pas d'une requête à
# l'autre, seul le spec choisi change.
BRAINROT_SYSTEM = (
    CHAT_SYSTEM + "\n\n" + BRAINROT_PROMPT_PATH.read_text(encoding="utf-8").strip()
)

# Plus haute que les 0.3 du chat : la voix brainrot vit de ses écarts, et à
# température basse le modèle retombe sur les trois mêmes tics.
BRAINROT_TEMPERATURE = 0.85

BRAINROT_SPEC = CallSpec(
    system=BRAINROT_SYSTEM,
    params=CHAT_GROQ_PARAMS,
    temperature=BRAINROT_TEMPERATURE,
)
