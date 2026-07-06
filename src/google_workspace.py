"""Bounded Google Drive and Sheets access for resume screening."""

import io
import os
from pathlib import Path
import tempfile
from time import sleep

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
PDF_MIME_TYPE = "application/pdf"
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_RESUME_TEXT = 40_000
API_ATTEMPTS = 3


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in .env.")
    return value


class GoogleWorkspace:
    """Own authenticated clients and enforce configured resource boundaries."""

    def __init__(self, key_path: str = "keys.json") -> None:
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=[DRIVE_READ_SCOPE, SHEETS_SCOPE],
        )
        self.drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.folder_id = _required_env("RESUME_FOLDER_ID")
        self.sheet_id = _required_env("SHEET_ID")

    @staticmethod
    def execute(request):
        """Retry only throttling and provider-side failures."""
        for attempt in range(1, API_ATTEMPTS + 1):
            try:
                return request.execute()
            except HttpError as error:
                status = getattr(error.resp, "status", 0)
                if status not in {429, 500, 502, 503, 504} or attempt == API_ATTEMPTS:
                    raise
                sleep(2 ** (attempt - 1))
        raise RuntimeError("Google API retry loop ended unexpectedly.")

    def list_resume_pdfs(self) -> list[dict]:
        files: list[dict] = []
        page_token = None
        query = (
            f"'{self.folder_id}' in parents and trashed = false "
            f"and mimeType = '{PDF_MIME_TYPE}'"
        )
        while True:
            response = self.execute(
                self.drive.files().list(
                    q=query,
                    spaces="drive",
                    fields=(
                        "nextPageToken,"
                        "files(id,name,mimeType,size,modifiedTime,md5Checksum,capabilities/canDownload)"
                    ),
                    orderBy="name",
                    pageSize=1000,
                    pageToken=page_token,
                )
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    def download_resume_pdf(self, file_metadata: dict) -> Path:
        if not file_metadata.get("capabilities", {}).get("canDownload", True):
            raise PermissionError("Drive reports that this PDF cannot be downloaded.")
        size = int(file_metadata.get("size", 0) or 0)
        if size > MAX_PDF_BYTES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_BYTES}-byte limit.")
        request = self.drive.files().get_media(fileId=file_metadata["id"])
        temporary = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        path = Path(temporary.name)
        try:
            downloader = MediaIoBaseDownload(temporary, request, chunksize=1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=2)
                if temporary.tell() > MAX_PDF_BYTES:
                    raise ValueError(f"PDF exceeds the {MAX_PDF_BYTES}-byte limit.")
            temporary.close()
            return path
        except Exception:
            temporary.close()
            path.unlink(missing_ok=True)
            raise

    def extract_pdf_text(self, path: Path) -> dict:
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                return {"status": "review_required", "error": "Encrypted PDF."}
            if len(reader.pages) > MAX_PDF_PAGES:
                return {
                    "status": "review_required",
                    "error": f"PDF exceeds the {MAX_PDF_PAGES}-page limit.",
                }
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = "\n\n".join(value for value in pages if value)
            if len(text) < 100:
                return {
                    "status": "review_required",
                    "error": "No usable text found; the PDF may be scanned.",
                }
            return {
                "status": "completed",
                "text": text[:MAX_RESUME_TEXT],
                "page_count": len(reader.pages),
                "truncated": len(text) > MAX_RESUME_TEXT,
            }
        except Exception as error:
            return {"status": "review_required", "error": f"Unreadable PDF: {error}"}

    def spreadsheet_metadata(self) -> dict:
        return self.execute(
            self.sheets.spreadsheets().get(
                spreadsheetId=self.sheet_id,
                fields="sheets(properties(sheetId,title,gridProperties))",
            )
        )

    def get_values(self, range_name: str) -> list[list]:
        response = self.execute(
            self.sheets.spreadsheets()
            .values()
            .get(spreadsheetId=self.sheet_id, range=range_name)
        )
        return response.get("values", [])

    def update_values(self, range_name: str, values: list[list]) -> None:
        self.execute(
            self.sheets.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.sheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": values},
            )
        )

    def append_values(self, range_name: str, values: list[list]) -> None:
        self.execute(
            self.sheets.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.sheet_id,
                range=range_name,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
        )

    def batch_update(self, requests: list[dict]) -> None:
        if requests:
            self.execute(
                self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=self.sheet_id,
                    body={"requests": requests},
                )
            )
