import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()
client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))

# On scanne TOUS les chunks du n°22 (limit=100)
points, _ = client.scroll(collection_name="documents", limit=100, with_payload=True)

print("🔍 Scan profond du contenu du n°22...")
for p in points:
    source = str((p.payload or {}).get("source", ""))
    text = (p.payload or {}).get("text", "")

    if "22" in source:
        # On affiche TOUT le texte pour vérifier ce qui a été ingéré
        if "Aridhi" in text or "Sabeur" in text:
            print(f"✅ TROUVÉ ! Voici la citation : {text}")
        elif "Citations" in text:
            print(f"📌 Rubrique Citations détectée : {text[:100]}...")
