import os
import shutil
import time
from pathlib import Path

from dotenv import load_dotenv

from .drivetolocal import DriveManager
from .mdtoqdrant import VectorStore
from .pdftomd import DocumentProcessor

load_dotenv()
BASE_DIR = Path(__file__).parent.resolve()
TEMP_PDF = BASE_DIR / "temp/pdfs"
TEMP_MD = BASE_DIR / "temp/markdowns"
logfile = BASE_DIR / "processed_files.json"


def run_pipeline() -> None:
    """Lance le pipeline complet d'ingestion des documents."""
    # 0. Préparation des dossiers
    for d in [TEMP_PDF, TEMP_MD]:
        d.mkdir(parents=True, exist_ok=True)

    # 1. ÉTAPE DRIVE -> LOCAL
    dm = DriveManager(BASE_DIR / "service-account.json")

    folder_ids_env = os.getenv("DRIVE_FOLDER_IDS")
    folder_ids = folder_ids_env.split(",") if folder_ids_env else []
    dm.download_all_from_folders(folder_ids, TEMP_PDF)

    # 2. ÉTAPE LOCAL -> MD
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD)

    # 3. ÉTAPE MD -> QDRANT
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if qdrant_url is None:
        msg = "Variable d'environnement QDRANT_URL manquante."
        raise RuntimeError(msg)

    if qdrant_api_key is None:
        msg = "Variable d'environnement QDRANT_API_KEY manquante."
        raise RuntimeError(msg)

    vs = VectorStore(qdrant_url, qdrant_api_key)
    vs.upload_directory(TEMP_MD, logfile)

    # 4. NETTOYAGE
    shutil.rmtree(BASE_DIR / "temp")
    print("✨ Pipeline terminé et dossiers temp nettoyés.")


if __name__ == "__main__":
    while True:
        run_pipeline()
        time.sleep(600)
