"""
QR code generator for photobooth sessions.

Maakt de QR-code die naar de fotopagina in de cloud wijst.

Hier stonden ook get_local_ip() en generate_session_url(), voor een QR naar
de booth zelf op het plaatselijke netwerk. Die terugval is verwijderd: de
gast zit op een feest zelden op hetzelfde wifi, scande zo'n code en kreeg een
foutmelding. Lukt het delen niet, dan tonen we nu geen QR maar een korte
mededeling (zie photobooth._toon_delen_mislukt).
"""

import io
import qrcode
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QImage


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
