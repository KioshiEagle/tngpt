import os, time, shutil, argparse
from pathlib import Path
from dotenv import load_dotenv
from drivetolocal import DriveManager
from pdftomd import DocumentProcessor
from mdtoqdrant import VectorStore

BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR.parent.parent / ".env")
TEMP_PDF = BASE_DIR / "temp/pdfs"
TEMP_MD = BASE_DIR / "temp/markdowns"
logfile = BASE_DIR / "processed_files.json"

def step_drive():
    TEMP_PDF.mkdir(parents=True, exist_ok=True)
    dm = DriveManager(str(BASE_DIR / "service-account.json"))
    folder_ids = [fid.strip() for fid in os.getenv("DRIVE_FOLDER_IDS").split(",")]
    dm.download_all_from_folders(folder_ids, TEMP_PDF)
    print("✅ Drive -> Local terminé.")

def step_pdf():
    TEMP_MD.mkdir(parents=True, exist_ok=True)
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD)
    print("✅ PDF -> MD terminé.")

def step_qdrant():
    vs = VectorStore(os.getenv("QDRANT_URL"), os.getenv("QDRANT_API_KEY"))
    vs.upload_directory(TEMP_MD, logfile)
    print("✅ MD -> Qdrant terminé.")

def run_pipeline():
    for d in [TEMP_PDF, TEMP_MD]: d.mkdir(parents=True, exist_ok=True)
    step_drive()
    step_pdf()
    step_qdrant()
    shutil.rmtree(BASE_DIR / "temp")
    print("✨ Pipeline terminé et dossiers temp nettoyés.")

if __name__ == "__main__":
    """
    A LIRE C'EST UTILE

    python ingest.py                    # pipeline complet
    python ingest.py --step drive       # seulement Drive -> Local
    python ingest.py --step pdf         # seulement PDF -> MD
    python ingest.py --step qdrant      # seulement MD -> Qdrant
    python ingest.py --loop             # pipeline complet en boucle toutes les 30s
    python drivetolocal.py              # standalone
    python pdftomd.py                   # standalone
    python mdtoqdrant.py                # standalone
    """
    parser = argparse.ArgumentParser(description="Pipeline d'ingestion de documents")
    parser.add_argument(
        "--step",
        choices=["drive", "pdf", "qdrant"],
        help="Exécuter uniquement cette étape. Sans argument = pipeline complet."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Répéter la commande toutes les 30 secondes."
    )
    args = parser.parse_args()

    steps = {"drive": step_drive, "pdf": step_pdf, "qdrant": step_qdrant}
    fn = steps.get(args.step, run_pipeline)

    if args.loop:
        while True:
            fn()
            time.sleep(30)
    else:
        fn()
