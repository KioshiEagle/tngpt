import random
from pathlib import Path
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import chromadb
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")

def ingest_pdfs(pdf_folder="./data", collection_name="documents"):
    """
    Charge tous les PDFs d'un dossier (récursif) et les stocke dans ChromaDB
    """
    # 1. Initialisation du modèle et du client
    model = SentenceTransformer('all-MiniLM-L6-v2')
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    
    # Nettoyage de l'ancienne collection pour repartir de zéro
    try:
        client.delete_collection(name=collection_name)
    except:
        pass
    collection = client.create_collection(name=collection_name)
    
    pdf_path = Path(pdf_folder)
    # Recherche récursive de tous les .pdf
    pdf_files = list(pdf_path.rglob("*.pdf"))
    
    print(f"Trouvé {len(pdf_files)} PDFs")
    
    temp_docs = []
    temp_metadatas = []
    temp_ids = []
    
    # 2. Extraction et nettoyage de sécurité
    for pdf_file in pdf_files:
        try:
            reader = PdfReader(pdf_file)
            for page_num, page in enumerate(reader.pages):
                raw_text = page.extract_text()
                
                # Vérification : Doit être du texte, non vide, et assez long
                if raw_text and isinstance(raw_text, str) and len(raw_text.strip()) > 20:
                    # Sécurité ultime : On force l'encodage UTF-8 pour supprimer les caractères toxiques
                    clean_text = raw_text.encode("utf-8", "ignore").decode("utf-8").strip()
                    
                    if clean_text:
                        temp_docs.append(clean_text)
                        temp_metadatas.append({
                            "source": pdf_file.name,
                            "page": page_num + 1
                        })
                        # ID unique pour éviter les collisions dans Chroma
                        temp_ids.append(f"{pdf_file.stem}_p{page_num + 1}_{random.randint(0, 100000)}")
        except Exception as e:
            print(f"⚠️ Fichier ignoré ({pdf_file.name}): {e}")

    if not temp_docs:
        print("❌ Aucun texte valide extrait. Vérifie tes fichiers dans le dossier data.")
        return

    print(f"Génération des embeddings pour {len(temp_docs)} pages...")
    
    # 3. Encodage par batchs pour la stabilité
    # show_progress_bar=True affichera la progression sur ton M4
    try:
        embeddings = model.encode(
            temp_docs, 
            batch_size=32, 
            show_progress_bar=True, 
            convert_to_numpy=True
        )
        
        # 4. Stockage dans ChromaDB
        collection.add(
            embeddings=embeddings.tolist(),
            documents=temp_docs,
            metadatas=temp_metadatas,
            ids=temp_ids
        )
        print(f"✅ Ingestion terminée : {len(temp_docs)} pages indexées dans ChromaDB")
        
    except TypeError as e:
        print(f"❌ Erreur critique de type lors de l'encodage : {e}")
        print("Il reste probablement un caractère non-textuel caché dans les PDF.")
    except Exception as e:
        print(f"❌ Erreur inattendue : {e}")

if __name__ == "__main__":
    ingest_pdfs()