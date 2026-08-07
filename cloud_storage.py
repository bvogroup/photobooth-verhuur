"""
Cloud photo storage via Cloudflare R2.

Uploads photo strips to R2 with a random unguessable URL.
Photos auto-expire after CLOUD_PHOTO_EXPIRY_MIN minutes.
Guests scan QR code → download photo → photo gets deleted.

R2 is S3-compatible, so we use boto3.
"""

import os
import uuid
import time
import threading

try:
    import boto3
    from botocore.config import Config as BotoConfig
    _BOTO3_AVAILABLE = True
except ImportError as e:
    _BOTO3_AVAILABLE = False
    print(f"[CLOUD] boto3 import fout: {e}")
    import traceback
    traceback.print_exc()

from PyQt5.QtCore import QThread, pyqtSignal

import config


# Laatste terugval als álles leeg is. Dit is de waarde die vóór deze
# wijziging hardgecodeerd in cloud_storage stond, zodat een booth met een
# lege config nooit een kapotte QR-code toont.
_DEFAULT_WORKER_URL = "https://qr.bootharoo.com"


def _gallery_url_template() -> str:
    """Zoek de sjabloon-URL voor de fotopagina van de gast.

    Volgorde (de eerste niet-lege waarde wint):
      1. omgevingsvariabele BOOTHAROO_GALLERY_URL
      2. gallery_url_template in booth_settings.json (per booth instelbaar)
      3. config.CLOUD_GALLERY_URL_TEMPLATE (meegeleverd in de build)

    Leeg resultaat = de aanroeper valt terug op het oude
    {CLOUD_WORKER_URL}/gallery/{session_id}.

    Faalt er iets (kapotte json, module ontbreekt), dan wordt die bron
    stilzwijgend overgeslagen: een niet te lezen instelling mag nooit de
    upload van een gast tegenhouden.
    """
    env = (os.environ.get("BOOTHAROO_GALLERY_URL") or "").strip()
    if env:
        return env

    try:
        from booth_settings import BoothSettings
        if BoothSettings.exists():
            stored = (BoothSettings.load().gallery_url_template or "").strip()
            if stored:
                return stored
    except Exception as e:
        print(f"[CLOUD] Kon gallery-URL uit booth_settings niet lezen: {e}")

    return (getattr(config, "CLOUD_GALLERY_URL_TEMPLATE", "") or "").strip()


def gallery_url_for(session_id: str) -> str:
    """Bouw de URL die in de QR-code komt voor deze sessie.

    Zonder instelling levert dit exact dezelfde URL als vóór deze wijziging:
    {CLOUD_WORKER_URL}/gallery/{session_id}. Een booth die niet is bijgewerkt
    of niets heeft ingesteld, blijft dus werken zoals hij nu werkt.
    """
    try:
        template = _gallery_url_template()
    except Exception as e:
        print(f"[CLOUD] Gallery-URL instelling onleesbaar ({e}) — terugval")
        template = ""

    if not template:
        worker = (getattr(config, "CLOUD_WORKER_URL", "") or "").strip()
        worker = worker or _DEFAULT_WORKER_URL
        return f"{worker.rstrip('/')}/gallery/{session_id}"

    if "{session_id}" in template:
        return template.replace("{session_id}", session_id)

    # Sjabloon zonder plaatshouder = alleen een basis-URL.
    return f"{template.rstrip('/')}/{session_id}"


def _get_r2_client():
    """Create a boto3 S3 client configured for Cloudflare R2."""
    if not _BOTO3_AVAILABLE:
        raise ImportError("boto3 is niet beschikbaar")

    endpoint = getattr(config, 'R2_ENDPOINT_URL', '')
    if not endpoint:
        account_id = getattr(config, 'R2_ACCOUNT_ID', '')
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=getattr(config, 'R2_ACCESS_KEY_ID', ''),
        aws_secret_access_key=getattr(config, 'R2_SECRET_ACCESS_KEY', ''),
        config=BotoConfig(
            retries={'max_attempts': 2, 'mode': 'standard'},
            connect_timeout=10,
            read_timeout=30,
        ),
        region_name='auto',
    )


def compress_photo(file_path: str, max_width: int = 2000, quality: int = 80) -> str:
    """Compress a JPEG photo for sharing. Returns path to compressed temp file.

    Resizes to max_width (maintaining aspect ratio) and saves at given quality.
    If already small enough, returns original path.
    """
    try:
        from PIL import Image
        import tempfile

        size = os.path.getsize(file_path)
        if size < 300_000:  # Already under 300KB, skip
            return file_path

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png'):
            return file_path

        with Image.open(file_path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                new_h = int(img.height * ratio)
                img = img.resize((max_width, new_h), Image.LANCZOS)

            # Save to temp file
            tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False,
                                              dir=os.path.dirname(file_path))
            img.save(tmp.name, "JPEG", quality=quality, optimize=True)
            tmp.close()

            orig_kb = size // 1024
            new_kb = os.path.getsize(tmp.name) // 1024
            print(f"[COMPRESS] {os.path.basename(file_path)}: {orig_kb}KB -> {new_kb}KB")
            return tmp.name
    except Exception as e:
        print(f"[COMPRESS] Fout: {e}")
        return file_path


def compress_files_for_sharing(file_paths: list, compress: bool = False) -> list:
    """Compress a list of files if compression is enabled.

    Returns list of (possibly compressed) file paths.
    Caller should clean up temp files after use.
    """
    if not compress:
        return file_paths, []

    compressed = []
    temp_files = []
    for fp in file_paths:
        if not fp or not os.path.exists(fp):
            compressed.append(fp)
            continue
        new_path = compress_photo(fp)
        compressed.append(new_path)
        if new_path != fp:
            temp_files.append(new_path)
    return compressed, temp_files


def upload_to_r2_direct(file_path: str, key: str, content_type: str = 'image/jpeg'):
    """Upload a file to R2 with a specific key (no random name).

    Used for uploading boomerang GIFs after the main session upload.
    """
    bucket = getattr(config, 'R2_BUCKET_NAME', 'photobooth-photos')
    uploaded_at = str(int(time.time()))

    client = _get_r2_client()
    client.upload_file(
        file_path, bucket, key,
        ExtraArgs={
            'ContentType': content_type,
            'Metadata': {'uploaded_at': uploaded_at},
        }
    )
    print(f"[CLOUD] Direct upload: {key} ({os.path.getsize(file_path) // 1024} KB)")


def upload_to_r2(file_path: str) -> str:
    """Upload a photo to Cloudflare R2 with a random filename.

    Args:
        file_path: Path to the JPEG file to upload.

    Returns:
        The public download URL (via Cloudflare Worker).

    Raises:
        Exception on upload failure.
    """
    worker_url = getattr(config, 'CLOUD_WORKER_URL', '')
    bucket = getattr(config, 'R2_BUCKET_NAME', 'photobooth-photos')

    # Generate unguessable filename
    photo_id = uuid.uuid4().hex
    ext = os.path.splitext(file_path)[1].lower() or '.jpg'
    key = f"{photo_id}{ext}"

    # Upload timestamp for expiry check
    uploaded_at = str(int(time.time()))

    client = _get_r2_client()
    client.upload_file(
        file_path,
        bucket,
        key,
        ExtraArgs={
            'ContentType': 'image/jpeg',
            'Metadata': {
                'uploaded_at': uploaded_at,
            },
        },
    )

    url = f"{worker_url.rstrip('/')}/{key}"
    print(f"[CLOUD] Geupload: {key} ({os.path.getsize(file_path) / 1024:.0f} KB)")
    return url


def upload_session_to_r2(session_id: str, strip_path: str,
                         photo_paths: list, boomerang_path: str = None,
                         compress: bool = False,
                         branding_text: str = "") -> str:
    """Upload all photos from a session to R2 under a session prefix.

    Uploads the strip, individual photos, and optional boomerang GIF,
    each under ``{session_id}/`` so the gallery worker can serve them.

    Args:
        session_id:    Unique session identifier (used as R2 key prefix).
        strip_path:    Path to the assembled photo-strip JPEG.
        photo_paths:   List of paths to individual photo JPEGs.
        boomerang_path: Optional path to a boomerang GIF.
        compress:      If True, compress photos before uploading.
        branding_text: Optionele multi-line tekst die de Cloudflare worker
                       toont onderaan de gallery-pagina (vervangt
                       "Powered by Bootharoo"). Leeg = default-fallback.

    Returns:
        De fotopagina-URL voor de gast, zie ``gallery_url_for()``. Zonder
        instelling is dat ``{CLOUD_WORKER_URL}/gallery/{session_id}``.

    Raises:
        Exception on upload failure.
    """
    bucket = getattr(config, 'R2_BUCKET_NAME', 'photobooth-photos')
    uploaded_at = str(int(time.time()))
    client = _get_r2_client()

    # Compress files if enabled
    temp_files = []
    if compress:
        actual_strip = compress_photo(strip_path, max_width=1200, quality=80)
        if actual_strip != strip_path:
            temp_files.append(actual_strip)
        actual_photos = []
        for p in photo_paths:
            cp = compress_photo(p, max_width=2000, quality=80)
            actual_photos.append(cp)
            if cp != p:
                temp_files.append(cp)
    else:
        actual_strip = strip_path
        actual_photos = photo_paths

    # Build list of (local_path, r2_key, content_type)
    files_to_upload = []

    # Strip
    files_to_upload.append((actual_strip, f"{session_id}/strip.jpg", 'image/jpeg'))

    # Individual photos
    for i, photo_path in enumerate(actual_photos, start=1):
        files_to_upload.append(
            (photo_path, f"{session_id}/photo_{i}.jpg", 'image/jpeg')
        )

    # Boomerang (optional)
    if boomerang_path and os.path.exists(boomerang_path):
        files_to_upload.append(
            (boomerang_path, f"{session_id}/boomerang.gif", 'image/gif')
        )

    total = len(files_to_upload)
    for idx, (local_path, key, content_type) in enumerate(files_to_upload, start=1):
        size_kb = os.path.getsize(local_path) / 1024
        short_name = key.split('/')[-1]
        print(f"[CLOUD] Sessie upload: {idx}/{total} {short_name} ({size_kb:.0f} KB)")

        # Bouw metadata-dict — branding_text gaat alleen mee als hij niet leeg
        # is. S3-metadata gaat als HTTP header → newlines en non-ASCII chars
        # zijn niet toegestaan ("Invalid header value"). Daarom URL-encoden we
        # de hele string (RFC 3986 percent-encoding). De Cloudflare worker doet
        # decodeURIComponent() om de echte tekst (incl. newlines, é, emoji's)
        # terug te krijgen voor de gallery-footer.
        metadata = {'uploaded_at': uploaded_at}
        if branding_text:
            from urllib.parse import quote
            # Trim tot 1024 chars vóór encoding (na encoding kan hij groter
            # worden, maar HTTP-headers tot ~8KB blijven prima).
            safe_text = quote(branding_text[:1024], safe='')
            metadata['branding_text'] = safe_text

        client.upload_file(
            local_path,
            bucket,
            key,
            ExtraArgs={
                'ContentType': content_type,
                'Metadata': metadata,
            },
        )

    # Clean up temp compressed files
    for tmp in temp_files:
        try:
            os.unlink(tmp)
        except Exception:
            pass

    gallery_url = gallery_url_for(session_id)
    print(f"[CLOUD] Sessie upload voltooid: {gallery_url}")
    return gallery_url


def delete_from_r2(key: str):
    """Delete a single object from R2."""
    bucket = getattr(config, 'R2_BUCKET_NAME', 'photobooth-photos')
    client = _get_r2_client()
    client.delete_object(Bucket=bucket, Key=key)
    print(f"[CLOUD] Verwijderd: {key}")


class CloudUploadThread(QThread):
    """Background thread for uploading photos to R2.

    Signals:
        upload_complete(str): Emitted with the cloud URL on success,
                              or empty string on failure.
    """

    upload_complete = pyqtSignal(str)

    def __init__(self, file_path: str, photo_paths=None,
                 boomerang_path=None, session_id=None,
                 compress=False, branding_text: str = "",
                 parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.photo_paths = photo_paths
        self.boomerang_path = boomerang_path
        self.session_id = session_id
        self.compress = compress
        self.branding_text = branding_text or ""

    def run(self):
        try:
            if self.photo_paths and self.session_id:
                url = upload_session_to_r2(
                    self.session_id,
                    self.file_path,
                    self.photo_paths,
                    self.boomerang_path,
                    compress=self.compress,
                    branding_text=self.branding_text,
                )
            else:
                url = upload_to_r2(self.file_path)
            self.upload_complete.emit(url)
        except Exception as e:
            error_msg = f"[CLOUD] Upload mislukt: {e}"
            print(error_msg)
            # Log to file so errors can be checked later on headless devices
            try:
                log_path = os.path.join(config.DATA_DIR, "cloud_errors.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    from datetime import datetime
                    f.write(f"{datetime.now().isoformat()} - {error_msg}\n")
            except Exception:
                pass
            self.upload_complete.emit("")
