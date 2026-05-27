"""Event-koppel modal: live webcam QR-scan + handmatige token-invoer.

QR-codes uit Clixibo bevatten ofwel een raw token, ofwel een URL
zoals `/offerte/<token>` of `https://clixibo.com/offerte/<token>`.
We extracten in alle gevallen de 40-tekens token zelf.
"""

import re
from typing import Optional, Tuple

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap, QImage, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSizePolicy, QMessageBox, QWidget, QProgressBar,
)

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

import config


# QR-token regex: minstens 20 chars, alleen [A-Za-z0-9_-]
TOKEN_RE = re.compile(r"([A-Za-z0-9_-]{20,})")


def extract_token(raw: str) -> Optional[str]:
    """Extract booking-token uit raw QR-string of URL.

    Handelt af:
        abc123def...              (raw token)
        /offerte/abc123def...     (relative path)
        https://x.com/offerte/abc123def...
        https://qr.bootharoo.com/q/abc123def...
    """
    if not raw:
        return None
    raw = raw.strip()
    # Probeer offerte/ of /q/ pattern eerst
    m = re.search(r"(?:offerte|/q)/([A-Za-z0-9_-]{20,})", raw)
    if m:
        return m.group(1)
    # Anders: laatste segment dat een token lijkt
    m = TOKEN_RE.search(raw)
    if m:
        return m.group(1)
    return None


# ── Webcam QR-scan worker ────────────────────────────────────────────

class QRScanWorker(QThread):
    """Background-thread die webcam frames leest en QR-codes detecteert.

    frame_ready: nieuwe frame voor UI preview (JPEG bytes)
    qr_detected: gedetecteerde token-string
    """

    frame_ready = pyqtSignal(bytes)
    qr_detected = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, device_index: int = 0, parent=None):
        super().__init__(parent)
        self.device_index = device_index
        self._running = False
        self._cap = None
        self._detector = None

    def run(self):
        if not _CV2:
            self.error.emit("OpenCV niet beschikbaar voor QR-scan")
            return
        try:
            self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self.error.emit(f"Kan webcam niet openen (index {self.device_index})")
                return
            # Bescheiden resolutie voor snelle detection
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self._detector = cv2.QRCodeDetector()
        except Exception as e:
            self.error.emit(f"Webcam init fout: {e}")
            return

        self._running = True
        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                self.msleep(30)
                continue

            # Detect QR
            try:
                data, points, _ = self._detector.detectAndDecode(frame)
            except Exception:
                data = ""

            # Encode frame voor UI
            try:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.frame_ready.emit(buf.tobytes())
            except Exception:
                pass

            if data:
                token = extract_token(data)
                if token:
                    self.qr_detected.emit(token)
                    self._running = False
                    break

            self.msleep(50)  # ~20fps

        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def stop(self):
        self._running = False
        self.wait(2000)


# ── Modal dialog ──────────────────────────────────────────────────────

class CoupleEventDialog(QDialog):
    """Modal voor event-koppeling.

    Resultaat: na .exec_() bevat .selected_token de gevonden token, of "" bij cancel.
    """

    def __init__(self, parent=None, webcam_index: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Event koppelen")
        self.setMinimumSize(720, 600)
        self._worker: Optional[QRScanWorker] = None
        self._webcam_index = webcam_index
        self.selected_token: str = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(20, 20, 20, 20)

        # Titel
        title = QLabel("Scan de QR-code van het event")
        title.setFont(QFont("DM Sans", 16, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        # Webcam preview
        self._preview = QLabel("Webcam wordt gestart...")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumSize(640, 380)
        self._preview.setStyleSheet(
            "background: #000000; color: #888888; border-radius: 12px;"
        )
        self._preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self._preview)

        # Status
        self._status = QLabel("Houd de QR-code voor de camera")
        self._status.setFont(QFont("DM Sans", 12))
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(f"color: {config.COLOR_TEXT};")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # Knoppen
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._manual_btn = QPushButton("Het lukt niet — typ token")
        self._manual_btn.setFont(QFont("DM Sans", 12))
        self._manual_btn.setCursor(Qt.PointingHandCursor)
        self._manual_btn.setMinimumHeight(44)
        self._manual_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: white; "
            f"border: none; border-radius: 8px; padding: 10px 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._manual_btn.clicked.connect(self._open_manual_entry)
        btn_row.addWidget(self._manual_btn)

        btn_row.addStretch()

        self._cancel_btn = QPushButton("Annuleren")
        self._cancel_btn.setFont(QFont("DM Sans", 12))
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setMinimumHeight(44)
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 10px 18px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_DANGER}; border-color: {config.COLOR_DANGER}; }}"
        )
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        lay.addLayout(btn_row)

        # Start scanner zodra dialog is getoond
        QTimer.singleShot(100, self._start_scanner)

    def _start_scanner(self):
        if not _CV2:
            self._status.setText("OpenCV niet beschikbaar — gebruik handmatige invoer.")
            self._preview.setText("Webcam niet beschikbaar")
            return
        self._worker = QRScanWorker(self._webcam_index)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.qr_detected.connect(self._on_qr_detected)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_frame(self, jpeg_bytes: bytes):
        img = QImage.fromData(jpeg_bytes, "JPEG")
        if img.isNull():
            return
        scaled = QPixmap.fromImage(img).scaled(
            self._preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._preview.setPixmap(scaled)

    def _on_qr_detected(self, token: str):
        self.selected_token = token
        self._status.setText(f"✓ Token gedetecteerd: {token[:20]}...")
        self._stop_scanner()
        QTimer.singleShot(400, self.accept)

    def _on_error(self, msg: str):
        self._status.setText(f"Fout: {msg}")
        self._preview.setText("Geen webcam — gebruik handmatige invoer")

    def _open_manual_entry(self):
        self._stop_scanner()
        token = ManualTokenDialog.get_token(self)
        if token:
            self.selected_token = token
            self.accept()
        else:
            # User cancelled manual entry — restart scanner
            self._start_scanner()

    def _stop_scanner(self):
        if self._worker:
            try:
                self._worker.stop()
            except Exception:
                pass
        self._worker = None

    def closeEvent(self, event):
        self._stop_scanner()
        super().closeEvent(event)

    def reject(self):
        self._stop_scanner()
        super().reject()


# ── Handmatige token-invoer ──────────────────────────────────────────

class ManualTokenDialog(QDialog):
    """Apart modal om token handmatig in te typen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Handmatige token-invoer")
        self.setMinimumWidth(500)
        self._token: str = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Plak of typ de event-token")
        title.setFont(QFont("DM Sans", 14, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        lay.addWidget(title)

        hint = QLabel(
            "Open de offerte-link op je telefoon. De token staat in de URL na "
            "/offerte/ (40 tekens, letters en cijfers)."
        )
        hint.setFont(QFont("DM Sans", 11))
        hint.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._input = QLineEdit()
        self._input.setFont(QFont("DM Sans", 14))
        self._input.setMinimumHeight(44)
        self._input.setPlaceholderText("plak hier de token of volledige URL")
        self._input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 8px; padding: 8px 14px; color: {config.COLOR_TEXT}; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._input.returnPressed.connect(self._submit)
        lay.addWidget(self._input)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Annuleren")
        cancel.setMinimumHeight(40)
        cancel.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px 18px; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()

        ok = QPushButton("Koppel")
        ok.setMinimumHeight(40)
        ok.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        ok.clicked.connect(self._submit)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def _submit(self):
        raw = self._input.text().strip()
        token = extract_token(raw)
        if not token:
            QMessageBox.warning(self, "Ongeldige token",
                "Geen geldige token gevonden in deze input.")
            return
        self._token = token
        self.accept()

    @staticmethod
    def get_token(parent=None) -> str:
        d = ManualTokenDialog(parent)
        if d.exec_() == QDialog.Accepted:
            return d._token
        return ""


# ── Background worker voor de cloud-calls + loading-dialoog ────────

class CouplingWorker(QThread):
    """Background-thread die de booking-metadata en design ophaalt.

    Houdt de UI responsive tijdens de (mogelijk trage) HTTP-calls.
    Resultaat wordt teruggegeven via 'finished' signal.
    """

    progress = pyqtSignal(str)
    done = pyqtSignal(object, str, str)  # (booking_data dict|None, design_local_path, error_msg)

    def __init__(self, token: str, parent=None):
        super().__init__(parent)
        self.token = token

    def run(self):
        from cloud_booking import fetch_booking, fetch_design

        self.progress.emit("Event ophalen uit Clixibo…")
        b, err = fetch_booking(self.token, use_cache_on_offline=False)
        if not b:
            self.done.emit(None, "", err or "Booking niet gevonden")
            return

        booking = b.get("booking", {}) or {}
        booking_id = booking.get("id", "")
        design_path = booking.get("photostrip_design_url", "") or ""

        if not design_path:
            self.done.emit(b, "", "geen design")  # niet-fataal — booking is wel OK
            return

        self.progress.emit("Strip-design downloaden…")
        local, derr = fetch_design(self.token, design_path, booking_id)
        if not local:
            self.done.emit(b, "", derr or "Design download mislukt")
            return

        self.done.emit(b, local, "")


class CouplingLoadingDialog(QDialog):
    """Modal 'Bezig met laden…' dialoog tijdens de cloud-calls.

    Heeft alleen status-tekst + indeterminate progress bar. Geen sluitknop
    omdat de worker zelf het dialoog sluit zodra klaar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bezig met koppelen")
        self.setMinimumSize(420, 160)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint
                            & ~Qt.WindowContextHelpButtonHint)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(28, 24, 28, 24)

        title = QLabel("Event wordt gekoppeld")
        title.setFont(QFont("DM Sans", 14, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        self._status = QLabel("Bezig met laden…")
        self._status.setFont(QFont("DM Sans", 11))
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # Indeterminate progress bar — toont aan dat-ie bezig is
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setMinimumHeight(8)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 4px; "
            f"background: {config.COLOR_BORDER}; }}"
            f"QProgressBar::chunk {{ background: {config.COLOR_PRIMARY}; "
            "border-radius: 4px; }"
        )
        lay.addWidget(bar)

    def set_status(self, msg: str):
        self._status.setText(msg)
