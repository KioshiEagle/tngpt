import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

class DriveManager:
    def __init__(self, json_path):
        self.creds = service_account.Credentials.from_service_account_file(
            json_path,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        self.service = build('drive', 'v3', credentials=self.creds)

    def _list_pdfs_recursive(self, folder_id):
        """Liste tous les PDFs dans un dossier et ses sous-dossiers (avec pagination)."""
        files = []
        shared_drive_kwargs = dict(
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
        )

        # PDFs directs dans ce dossier
        page_token = None
        while True:
            try:
                resp = self.service.files().list(
                    q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
                    fields="nextPageToken, files(id, name)",
                    pageToken=page_token,
                    **shared_drive_kwargs,
                ).execute()
            except HttpError as e:
                print(f"⚠️ Impossible de lister les PDFs du dossier {folder_id} : {e.resp.status} {e.reason}")
                break
            files.extend(resp.get('files', []))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break

        # Sous-dossiers (récursion)
        page_token = None
        while True:
            try:
                resp = self.service.files().list(
                    q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    fields="nextPageToken, files(id)",
                    pageToken=page_token,
                    **shared_drive_kwargs,
                ).execute()
            except HttpError as e:
                print(f"⚠️ Impossible de lister les sous-dossiers de {folder_id} : {e.resp.status} {e.reason}")
                break
            for subfolder in resp.get('files', []):
                files.extend(self._list_pdfs_recursive(subfolder['id']))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break

        return files

    def download_all_from_folders(self, folder_ids, target_dir):
        """Télécharge tous les PDFs (récursivement) depuis les dossiers donnés."""
        for folder_id in folder_ids:
            files = self._list_pdfs_recursive(folder_id)

            for f in files:
                # Utilise l'ID Drive comme nom de fichier : unique, pas de collision
                file_path = os.path.join(target_dir, f"{f['id']}.pdf")
                print(f"📥 Drive -> Local : {f['name']} ({f['id']})")

                try:
                    request = self.service.files().get_media(fileId=f['id'], supportsAllDrives=True)
                    with open(file_path, "wb") as pdf_file:
                        downloader = MediaIoBaseDownload(pdf_file, request)
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()

                except HttpError as e:
                    print(f"⚠️ Erreur sur {f['name']} : {e.resp.status}")
                    continue
