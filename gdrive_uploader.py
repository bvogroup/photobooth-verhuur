"""
Google Drive uploader for photobooth sessions.

Uploads photos and strips to a shared Google Drive folder
so event organizers can share all photos with guests afterward.

Setup:
1. Go to https://console.cloud.google.com
2. Create a project and enable Google Drive API
3. Create OAuth 2.0 credentials (Desktop application)
4. Download client_secrets.json to C:\\Photobooth\\
5. Set GDRIVE_ENABLED = True in config.py
6. First run will open browser for authorization
"""

import os
import threading
from PyQt5.QtCore import QThread, pyqtSignal

import config


class GDriveUploader:
    """Upload files to Google Drive."""

    def __init__(self):
        self._drive = None
        self._event_folder_id = None

    def authenticate(self):
        """Authenticate with Google Drive API."""
        try:
            from pydrive2.auth import GoogleAuth
            from pydrive2.drive import GoogleDrive

            secrets_path = os.path.join(config.BASE_DIR, "client_secrets.json")
            creds_path = os.path.join(config.BASE_DIR, "gdrive_credentials.json")

            gauth = GoogleAuth()

            # Use settings for automated auth
            gauth.settings.update({
                "client_config_file": secrets_path,
                "save_credentials": True,
                "save_credentials_backend": "file",
                "save_credentials_file": creds_path,
            })

            if os.path.exists(creds_path):
                gauth.LoadCredentialsFile(creds_path)

            if gauth.credentials is None:
                gauth.LocalWebserverAuth()
            elif gauth.access_token_expired:
                gauth.Refresh()
            else:
                gauth.Authorize()

            gauth.SaveCredentialsFile(creds_path)
            self._drive = GoogleDrive(gauth)
            print("[GDRIVE] Authenticatie geslaagd")
            return True

        except Exception as e:
            print(f"[GDRIVE] FOUT bij authenticatie: {e}")
            return False

    def _get_or_create_event_folder(self, folder_name):
        """Get or create the event folder on Google Drive."""
        if self._event_folder_id:
            return self._event_folder_id

        # Search for existing folder
        query = (
            f"title='{folder_name}' and "
            "mimeType='application/vnd.google-apps.folder' and "
            "trashed=false"
        )
        file_list = self._drive.ListFile({"q": query}).GetList()
        if file_list:
            self._event_folder_id = file_list[0]["id"]
            print(f"[GDRIVE] Bestaande map gevonden: {folder_name}")
            return self._event_folder_id

        # Create new folder
        folder = self._drive.CreateFile({
            "title": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        })
        folder.Upload()
        self._event_folder_id = folder["id"]
        print(f"[GDRIVE] Nieuwe map aangemaakt: {folder_name}")
        return self._event_folder_id

    def upload_file(self, file_path, folder_name):
        """Upload a single file to the event folder."""
        if not self._drive:
            if not self.authenticate():
                return False

        try:
            folder_id = self._get_or_create_event_folder(folder_name)
            filename = os.path.basename(file_path)

            gfile = self._drive.CreateFile({
                "title": filename,
                "parents": [{"id": folder_id}],
            })
            gfile.SetContentFile(file_path)
            gfile.Upload()
            print(f"[GDRIVE] Geupload: {filename}")
            return True

        except Exception as e:
            print(f"[GDRIVE] FOUT bij uploaden {file_path}: {e}")
            return False

    def upload_session(self, session_id, strip_path, photo_paths, folder_name=None):
        """Upload all photos from a session."""
        if not folder_name:
            folder_name = getattr(config, "GDRIVE_FOLDER_NAME", "Photobooth Event")

        files = list(photo_paths)
        if strip_path:
            files.append(strip_path)

        success = 0
        for f in files:
            if os.path.isfile(f) and self.upload_file(f, folder_name):
                success += 1

        print(f"[GDRIVE] Sessie {session_id}: {success}/{len(files)} bestanden geupload")
        return success == len(files)

    def get_share_link(self, folder_name=None):
        """Get a shareable link for the event folder."""
        if not folder_name:
            folder_name = getattr(config, "GDRIVE_FOLDER_NAME", "Photobooth Event")

        if not self._drive:
            if not self.authenticate():
                return None

        try:
            folder_id = self._get_or_create_event_folder(folder_name)

            # Make folder publicly accessible
            folder = self._drive.CreateFile({"id": folder_id})
            folder.FetchMetadata()
            folder.InsertPermission({
                "type": "anyone",
                "role": "reader",
            })

            return folder.get("alternateLink", f"https://drive.google.com/drive/folders/{folder_id}")

        except Exception as e:
            print(f"[GDRIVE] FOUT bij delen: {e}")
            return None


class GDriveUploadThread(QThread):
    """Background thread for uploading a session to Google Drive."""

    upload_complete = pyqtSignal(bool)  # success

    def __init__(self, uploader, session_id, strip_path, photo_paths, folder_name=None):
        super().__init__()
        self.uploader = uploader
        self.session_id = session_id
        self.strip_path = strip_path
        self.photo_paths = photo_paths
        self.folder_name = folder_name

    def run(self):
        success = self.uploader.upload_session(
            self.session_id, self.strip_path, self.photo_paths, self.folder_name
        )
        self.upload_complete.emit(success)
