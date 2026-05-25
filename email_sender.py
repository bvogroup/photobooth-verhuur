"""
Email sender for photobooth sessions.

Sends photo strip, original photos, and/or boomerang GIF to guests via SMTP.
Works with Gmail (app password), Outlook, or any SMTP provider.

Setup (Gmail):
  1. Enable 2-staps verificatie on your Google account
  2. Go to myaccount.google.com → Beveiliging → App-wachtwoorden
  3. Generate an app password (16 characters)
  4. Enter your Gmail address + app password in settings

Email content settings (subject, body, attachments) are per-event.
"""

import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from PyQt5.QtCore import QThread, pyqtSignal

import config

# Config file location
SMTP_CONFIG_FILE = os.path.join(config.DATA_DIR, "smtp_config.json")


def save_smtp_config(email, app_password, smtp_server="smtp.gmail.com", smtp_port=587):
    """Save SMTP credentials."""
    os.makedirs(os.path.dirname(SMTP_CONFIG_FILE), exist_ok=True)
    data = {
        "email": email,
        "app_password": app_password,
        "smtp_server": smtp_server,
        "smtp_port": smtp_port,
    }
    with open(SMTP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[EMAIL] SMTP config opgeslagen voor {email} ({smtp_server}:{smtp_port})")


def load_gmail_config():
    """Load SMTP config. Returns dict with 'gmail_address' or None."""
    if not os.path.isfile(SMTP_CONFIG_FILE):
        return None
    try:
        with open(SMTP_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        email = data.get("email", "")
        pw = data.get("app_password", "")
        if email and pw:
            return {"gmail_address": email}
        return None
    except Exception:
        return None


def _load_credentials():
    """Load email + password + server. Returns (email, password, server, port)."""
    if not os.path.isfile(SMTP_CONFIG_FILE):
        return None, None, "smtp.gmail.com", 587
    try:
        with open(SMTP_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (
            data.get("email", ""),
            data.get("app_password", ""),
            data.get("smtp_server", "smtp.gmail.com"),
            data.get("smtp_port", 587),
        )
    except Exception:
        return None, None, "smtp.gmail.com", 587


def remove_gmail_config():
    """Remove SMTP credentials."""
    if os.path.isfile(SMTP_CONFIG_FILE):
        try:
            os.remove(SMTP_CONFIG_FILE)
            print("[EMAIL] SMTP config verwijderd")
        except Exception as e:
            print(f"[EMAIL] Kan config niet verwijderen: {e}")


def test_smtp_connection():
    """Test SMTP connection.

    Returns (success: bool, error_message: str)
    """
    email, password, smtp_server, smtp_port = _load_credentials()
    if not email or not password:
        return False, "Geen e-mail of app-wachtwoord ingesteld."

    try:
        print(f"[EMAIL] SMTP test starten voor {email} ({smtp_server}:{smtp_port})...", flush=True)
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=8)
        server.ehlo()
        server.starttls()
        server.login(email, password)
        server.quit()
        print(f"[EMAIL] SMTP verbinding OK — {email}", flush=True)
        return True, ""
    except smtplib.SMTPAuthenticationError as e:
        print(f"[EMAIL] Auth fout: {e}", flush=True)
        return False, "Inloggen mislukt. Controleer je app-wachtwoord."
    except (TimeoutError, OSError) as e:
        print(f"[EMAIL] Timeout/netwerk fout: {e}", flush=True)
        return False, "Kan geen verbinding maken met smtp.gmail.com. Controleer je internetverbinding."
    except Exception as e:
        print(f"[EMAIL] SMTP fout: {e}", flush=True)
        return False, f"SMTP fout: {e}"


def send_photo_email(to_address, attachment_paths,
                     subject=None, body=None, smtp_config=None):
    """Send an email with photo attachments via SMTP.

    Args:
        to_address: Recipient email address.
        attachment_paths: List of file paths to attach.
        subject: Custom subject line.
        body: Custom body text.
        smtp_config: Ignored (kept for backward compatibility).
    """
    email, password, smtp_server, smtp_port = _load_credentials()
    if not email or not password:
        raise Exception("E-mail niet ingesteld. Stel je Gmail + app-wachtwoord in via de instellingen.")

    # Build the email message
    msg = MIMEMultipart()
    msg["From"] = email
    msg["To"] = to_address
    msg["Subject"] = subject or "Jouw Photobooth Foto's!"

    email_body = body or (
        "Bedankt voor je bezoek aan de photobooth!\n\n"
        "In de bijlage vind je jouw foto's.\n\n"
        "Groetjes,\nDe Photobooth"
    )
    msg.attach(MIMEText(email_body, "plain", "utf-8"))

    # Attach files — compress images to stay under Gmail 25MB limit
    MAX_TOTAL_MB = 20  # Stay well under Gmail's 25MB limit
    total_bytes = 0
    for filepath in attachment_paths:
        if not filepath or not os.path.exists(filepath):
            continue
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1].lower()

        # Compress JPEG/PNG images if they're too large
        file_data = None
        if ext in ('.jpg', '.jpeg', '.png') and os.path.getsize(filepath) > 500_000:
            try:
                from PIL import Image
                import io
                with Image.open(filepath) as img:
                    img = img.convert("RGB")
                    # Resize if very large (>3000px on any side)
                    max_dim = 2400
                    if img.width > max_dim or img.height > max_dim:
                        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=80, optimize=True)
                    file_data = buf.getvalue()
                    if filename.lower().endswith('.png'):
                        filename = filename[:-4] + '.jpg'
                    print(f"[EMAIL] {filename}: {os.path.getsize(filepath)//1024}KB -> {len(file_data)//1024}KB")
            except Exception:
                pass  # Fall back to original file

        if file_data is None:
            with open(filepath, "rb") as f:
                file_data = f.read()

        # Check total size limit
        total_bytes += len(file_data)
        if total_bytes > MAX_TOTAL_MB * 1024 * 1024:
            print(f"[EMAIL] Bijlage overgeslagen (totaal > {MAX_TOTAL_MB}MB): {filename}")
            continue

        part = MIMEBase("application", "octet-stream")
        part.set_payload(file_data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

    # Send via SMTP (with proper cleanup on error)
    server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
    try:
        server.ehlo()
        server.starttls()
        server.login(email, password)
        server.send_message(msg)
        print(f"[EMAIL] Verzonden naar {to_address} ({len(attachment_paths)} bijlagen)")
    finally:
        try:
            server.quit()
        except Exception:
            server.close()


# Legacy compatibility
def load_smtp_config():
    """Legacy compatibility."""
    cfg = load_gmail_config()
    if cfg:
        return {
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_username": cfg["gmail_address"],
            "smtp_from_address": cfg["gmail_address"],
        }
    return None


class EmailThread(QThread):
    """Background thread for sending email without blocking the UI."""

    email_sent = pyqtSignal()
    email_failed = pyqtSignal(str)

    def __init__(self, to_address, attachment_paths, smtp_config=None,
                 subject=None, body=None):
        super().__init__()
        self._to = to_address
        self._attachments = attachment_paths
        self._config = smtp_config
        self._subject = subject
        self._body = body

    def run(self):
        try:
            send_photo_email(
                self._to, self._attachments,
                subject=self._subject, body=self._body,
            )
            self.email_sent.emit()
        except Exception as e:
            print(f"[EMAIL] FOUT: {e}")
            self.email_failed.emit(str(e))
