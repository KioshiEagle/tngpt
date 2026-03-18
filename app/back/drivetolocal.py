import os
import io
import re  # Indispensable pour le nettoyage
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

    def download_all_from_folders(self, folder_ids, target_dir):
        """Télécharge les PDF en conservant leur nom d'origine nettoyé."""
        for folder_id in folder_ids:
            query = f"'{folder_id}' in parents and mimeType='application/pdf'"
            items = self.service.files().list(q=query, fields="files(id, name)").execute().get('files', [])
            
            for f in items:
                # 1. NETTOYAGE DU NOM
                # On enlève l'extension .pdf du nom d'origine pour le stem
                base_name = os.path.splitext(f['name'])[0]
                # On remplace tout ce qui n'est pas alphanumérique, espace ou tiret par '_'
                safe_name = re.sub(r'[^\w\s-]', '_', base_name).strip()
                
                try:
                    # On utilise le nom d'origine (safe) au lieu de l'ID
                    file_path = os.path.join(target_dir, f"{safe_name}.pdf")
                    
                    print(f"📥 Drive -> Local : {safe_name}.pdf")
                    
                    request = self.service.files().get_media(fileId=f['id'])
                    with open(file_path, "wb") as pdf_file:
                        downloader = MediaIoBaseDownload(pdf_file, request)
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
                            
                except HttpError as e:
                    print(f"⚠️ Erreur sur {f['name']} : {e.resp.status}")
                    continue