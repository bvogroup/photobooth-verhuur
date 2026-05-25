"""
QR code generator for photobooth sessions.

Generates a QR code that links to the local web server
where guests can download their photos.
"""

import io
import socket
import qrcode
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QImage


def get_local_ip():
    """Get the local network IP address of this machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def generate_session_url(session_id, port=8080):
    """Generate the URL for a session's download page."""
    ip = get_local_ip()
    return f"http://{ip}:{port}/session/{session_id}"


def generate_qr_pixmap(url, size=400):
    """Generate a QR code as a QPixmap.

    Args:
        url: The URL to encode in the QR code
        size: Desired size in pixels

    Returns:
        QPixmap with the QR code
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert PIL image to QPixmap
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qimage = QImage()
    qimage.loadFromData(buf.read())

    pixmap = QPixmap.fromImage(qimage)
    if size:
        pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    return pixmap
