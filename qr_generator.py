"""
QR code generator for photobooth sessions.

Maakt de QR-code die naar de fotopagina in de cloud wijst.

Hier stonden ook get_local_ip() en generate_session_url(), voor een QR naar
de booth zelf op het plaatselijke netwerk. Die terugval is verwijderd: de
gast zit op een feest zelden op hetzelfde wifi, scande zo'n code en kreeg een
foutmelding.

De URL die hier in gaat komt uit cloud_storage.gallery_url_for(): sjabloon
plus sessie-id, en dus zonder internet te bouwen. De gast krijgt daarom altijd
dezelfde werkende code, of het uploaden nu gelukt is of niet. Zie
photobooth._toon_qr().
"""

import io
import qrcode
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QImage


def generate_qr_pixmap(url, size=400, smooth=True):
    """Generate a QR code as a QPixmap.

    Args:
        url: The URL to encode in the QR code
        size: Desired size in pixels
        smooth: True vervaagt de randen bij het schalen. Dat staat mooier op
            een deelscherm dat van dichtbij bekeken wordt, maar het maakt de
            code slechter leesbaar: een camera moet zwart van wit kunnen
            scheiden, en een grijze overgang helpt daar niet bij. Zet hem op
            False waar de code gescand moet worden van een afstand — zoals de
            QR op het startscherm.

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
        pixmap = pixmap.scaled(
            size, size, Qt.KeepAspectRatio,
            Qt.SmoothTransformation if smooth else Qt.FastTransformation)

    return pixmap
