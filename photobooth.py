"""
Photobooth main application - Photo Strip Edition.

Captures photos and combines them into a photo strip using templates.
Templates define background image and photo frame positions.
Flow: IDLE -> SELECT TEMPLATE -> PREVIEW -> [COUNTDOWN -> CAPTURE] x N -> PRINT -> QR -> DONE
"""

import gc
import os
import sys
import shutil
import threading
from datetime import datetime
from enum import Enum, auto

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QSizePolicy, QGraphicsDropShadowEffect,
    QScrollArea, QGridLayout, QLineEdit, QComboBox, QInputDialog, QFrame,
    QCheckBox, QSpinBox, QTextEdit, QDialog, QProgressBar, QRadioButton,
    QToolButton
)
from PyQt5.QtCore import Qt, QTimer, QSize, QEventLoop, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QPixmapCache, QImage, QFont, QPainter, QColor, QCursor, QBitmap, QIcon

import config
from camera import (Camera, CaptureThread, EDSDKWorker,
                     get_search_folders, snapshot_files,
                     ensure_digicam_running, stop_digicam)
from printer import get_available_printers, select_printer_dialog, PrinterError
from template_model import Template, list_templates, get_preset_layouts
from event_model import Event, list_events
from countdown_ring import CountdownRingWidget
import auth
from translations import t, set_language, get_language, save_language, load_language
from toggle_switch import ToggleSwitch
from led_relay import LedRelay


class State(Enum):
    IDLE = auto()
    SELECT_TEMPLATE = auto()
    PREVIEW = auto()
    COUNTDOWN = auto()
    CAPTURE = auto()
    REVIEW = auto()
    PRINTING = auto()
    QR_CODE = auto()
    EMAIL_INPUT = auto()
    DATA_COLLECTION = auto()
    DONE = auto()
    ERROR = auto()
    SETTINGS = auto()
    PAYMENT = auto()
    CUSTOM_CHOICE = auto()
    CUSTOM_PAYMENT = auto()
    FILTER = auto()


STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {config.COLOR_BG};
    color: {config.COLOR_TEXT};
    font-family: "DM Sans";
}}
QLabel {{
    color: {config.COLOR_TEXT};
    background: transparent;
    font-family: "DM Sans";
}}
QPushButton {{
    background-color: {config.COLOR_SECONDARY};
    color: #ffffff;
    border: none;
    border-radius: 12px;
    padding: 20px 50px;
    font-size: 28px;
    font-weight: bold;
    font-family: "DM Sans";
    min-height: 60px;
}}
QPushButton:hover {{ background-color: {config.COLOR_SECONDARY_HOVER}; }}
QPushButton:pressed {{ background-color: #444749; }}
QPushButton#primaryBtn {{
    background-color: {config.COLOR_PRIMARY};
    color: {config.COLOR_TEXT_ON_PRIMARY};
}}
QPushButton#primaryBtn:hover {{ background-color: {config.COLOR_PRIMARY_HOVER}; }}
QPushButton#primaryBtn:pressed {{ background-color: {config.COLOR_PRIMARY_PRESSED}; }}
QPushButton#secondaryBtn {{
    background-color: {config.COLOR_SECONDARY};
    color: #ffffff;
}}
QPushButton#secondaryBtn:hover {{ background-color: {config.COLOR_SECONDARY_HOVER}; }}
QPushButton#successBtn {{
    background-color: {config.COLOR_SUCCESS};
    color: #ffffff;
}}
QPushButton#successBtn:hover {{ background-color: {config.COLOR_SUCCESS_HOVER}; }}
QLineEdit, QTextEdit, QComboBox, QSpinBox {{
    font-family: "DM Sans";
}}
/* Touch-friendly: bigger dropdowns and spin boxes */
QComboBox {{
    min-height: 40px;
    padding: 6px 12px;
}}
QComboBox QAbstractItemView {{
    min-height: 36px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 36px;
    padding: 6px 10px;
}}
QSpinBox {{
    min-height: 40px;
    padding: 4px 10px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 30px;
    height: 20px;
}}
/* Touch-friendly scrollbars */
QScrollBar:vertical {{
    width: 14px;
    border-radius: 7px;
}}
QScrollBar::handle:vertical {{
    min-height: 50px;
    border-radius: 7px;
}}
QCheckBox::indicator {{
    width: 44px;
    height: 24px;
}}
"""


class _BgWidget(QWidget):
    """QWidget that paints a background image scaled to fill."""

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap(image_path)

    def paintEvent(self, event):
        if not self._pixmap.isNull():
            from PyQt5.QtGui import QPainter
            painter = QPainter(self)
            scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()
        else:
            super().paintEvent(event)


class _FitLabel(QLabel):
    """QLabel die zijn bron-pixmap ALTIJD binnen de toegewezen ruimte schaalt
    (KeepAspectRatio) en bij resize meeschaalt. SizePolicy = Ignored zodat de
    pixmapgrootte de layout niet opblaast (anders wordt een grote foto op ware
    grootte getoond en valt 'ie buiten beeld)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._src = None
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def setSourcePixmap(self, pm):
        self._src = pm
        self._rescale()

    def clearSource(self):
        self._src = None
        super().setPixmap(QPixmap())

    def _rescale(self):
        if self._src is None or self._src.isNull():
            return
        if self.width() < 4 or self.height() < 4:
            return
        dpr = self.devicePixelRatioF()
        scaled = self._src.scaled(int(self.width() * dpr), int(self.height() * dpr),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        super().setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()


class PinDialog(QDialog):
    """Fullscreen touchscreen-friendly PIN entry dialog."""

    def __init__(self, parent=None, title="Toegangscode"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet(f"background: {config.COLOR_BG};")
        self._entered = ""
        self._pin_result = None

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(20)
        lay.setContentsMargins(40, 40, 40, 40)

        # Title
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("DM Sans", 24, QFont.Bold))
        title_label.setStyleSheet(f"color: {config.COLOR_TEXT};")
        lay.addWidget(title_label)

        # PIN display
        self._pin_display = QLabel("")
        self._pin_display.setAlignment(Qt.AlignCenter)
        self._pin_display.setFont(QFont("DM Sans", 36, QFont.Bold))
        self._pin_display.setFixedHeight(70)
        self._pin_display.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: {config.COLOR_INPUT_BG}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 10px; "
            f"letter-spacing: 12px;"
        )
        lay.addWidget(self._pin_display)

        # Number pad (3x4 grid + bottom row)
        grid = QGridLayout()
        grid.setSpacing(10)
        btn_style = (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 12px; "
            f"font-size: 28px; font-weight: bold; min-height: 64px; min-width: 85px; "
            f"max-width: 110px; padding: 0; font-family: 'DM Sans'; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        for i, num in enumerate(["1","2","3","4","5","6","7","8","9"]):
            btn = QPushButton(num)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda _, n=num: self._on_key(n))
            grid.addWidget(btn, i // 3, i % 3)

        # Bottom row: cancel, 0, backspace
        cancel_btn = QPushButton("\u2715")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: #ffffff; "
            f"border: none; border-radius: 12px; "
            f"font-size: 28px; font-weight: bold; min-height: 70px; min-width: 90px; padding: 0; }}"
            f"QPushButton:pressed {{ background: #A93226; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        grid.addWidget(cancel_btn, 3, 0)

        zero_btn = QPushButton("0")
        zero_btn.setCursor(Qt.PointingHandCursor)
        zero_btn.setStyleSheet(btn_style)
        zero_btn.clicked.connect(lambda: self._on_key("0"))
        grid.addWidget(zero_btn, 3, 1)

        back_btn = QPushButton("\u232b")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(btn_style)
        back_btn.clicked.connect(self._on_backspace)
        grid.addWidget(back_btn, 3, 2)

        lay.addLayout(grid)

        # OK button
        ok_btn = QPushButton("OK")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        ok_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 12px; padding: 15px 60px; "
            f"font-size: 22px; min-height: 50px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        ok_btn.clicked.connect(self._on_ok)
        lay.addWidget(ok_btn)

        # Hint voor verhuurders — alleen de huurder kent de code
        hint = QLabel(
            "De huurder van de photobooth kan deze code vinden\nin zijn/haar boekingsportaal!"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setFont(QFont("DM Sans", 11))
        hint.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; background: transparent; "
            f"padding-top: 8px;"
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def _on_key(self, key):
        if len(self._entered) < 8:
            self._entered += key
            self._pin_display.setText("\u2022" * len(self._entered))

    def _on_backspace(self):
        self._entered = self._entered[:-1]
        self._pin_display.setText("\u2022" * len(self._entered))

    def keyPressEvent(self, event):
        """Block Escape from propagating to parent — just reject the dialog."""
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _on_ok(self):
        self._pin_result = self._entered
        print(f"[PIN-DIALOG] OK gedrukt, entered='{self._entered}'")
        self.accept()

    @staticmethod
    def get_pin(parent, title="Toegangscode"):
        """Show PIN dialog and return (entered_pin, ok). Blocks until closed."""
        dialog = PinDialog(parent, title)
        dialog.setFixedSize(440, 740)
        # Center on screen
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            dialog.move(
                sg.x() + (sg.width() - 440) // 2,
                sg.y() + (sg.height() - 740) // 2,
            )
        dialog.raise_()
        dialog.activateWindow()
        result_code = dialog.exec_()
        print(f"[PIN-DIALOG] exec result={result_code}", flush=True)
        pin = dialog._pin_result if result_code == QDialog.Accepted else None
        ok = result_code == QDialog.Accepted
        dialog.deleteLater()
        # Restore focus to parent
        if parent:
            parent.activateWindow()
            parent.raise_()
            QApplication.processEvents()
        return pin, ok


class TextInputDialog(QDialog):
    """Fullscreen touchscreen-friendly text input dialog with on-screen keyboard."""

    def __init__(self, parent=None, title="Invoer", label="", text=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(f"background: {config.COLOR_BG};")
        self._result = None

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(15)
        lay.setContentsMargins(30, 30, 30, 30)

        # Title
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("DM Sans", 22, QFont.Bold))
        title_label.setStyleSheet(f"color: {config.COLOR_TEXT};")
        lay.addWidget(title_label)

        if label:
            sub = QLabel(label)
            sub.setAlignment(Qt.AlignCenter)
            sub.setFont(QFont("DM Sans", 14))
            sub.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            lay.addWidget(sub)

        # Text display
        self._text_input = QLineEdit(text)
        self._text_input.setAlignment(Qt.AlignCenter)
        self._text_input.setFont(QFont("DM Sans", 22))
        self._text_input.setFixedHeight(55)
        self._text_input.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: {config.COLOR_INPUT_BG}; "
            f"border: 2px solid {config.COLOR_SECONDARY}; border-radius: 8px; padding: 8px;"
        )
        lay.addWidget(self._text_input)

        # Simple keyboard rows
        kb_style = (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; "
            f"font-size: 18px; font-weight: bold; min-height: 50px; min-width: 40px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        # QWERTY layout met echte toetsenbord-uitlijning:
        # Rij 1 (10 letters): geen offset
        # Rij 2 (9 letters):  halve toets ingerukt aan elke kant
        # Rij 3 (7 letters):  1.5 toets ingerukt aan elke kant
        # Elke letter krijgt stretch=4 zodat alle toetsen exact dezelfde
        # breedte hebben, ongeacht hoeveel letters in de rij staan.
        KEY_STRETCH = 4
        rows = [
            ("QWERTYUIOP", 0, 0),
            ("ASDFGHJKL",  2, 2),
            ("ZXCVBNM",    6, 6),
        ]
        for row_chars, left_pad, right_pad in rows:
            row_lay = QHBoxLayout()
            row_lay.setSpacing(4)
            if left_pad:
                row_lay.addStretch(left_pad)
            for ch in row_chars:
                btn = QPushButton(ch)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(kb_style)
                btn.clicked.connect(lambda _, c=ch: self._type_char(c))
                row_lay.addWidget(btn, KEY_STRETCH)
            if right_pad:
                row_lay.addStretch(right_pad)
            lay.addLayout(row_lay)

        # Cijferrij — uitgelijnd onder rij 1 (10 cijfers, zelfde stretch)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        for ch in "0123456789":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._type_char(c))
            bottom.addWidget(btn, KEY_STRETCH)
        lay.addLayout(bottom)

        special_row = QHBoxLayout()
        special_row.setSpacing(6)
        space_btn = QPushButton(t("key_space").upper())
        space_btn.setCursor(Qt.PointingHandCursor)
        space_btn.setStyleSheet(kb_style)
        space_btn.clicked.connect(lambda: self._type_char(" "))
        special_row.addWidget(space_btn, stretch=3)
        back_btn = QPushButton("\u232b")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(kb_style)
        back_btn.clicked.connect(self._on_backspace)
        special_row.addWidget(back_btn, stretch=1)
        lay.addLayout(special_row)

        # OK / Cancel buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)
        cancel_btn = QPushButton(t("cancel").upper())
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 10px; padding: 12px 30px; font-size: 16px; min-height: 40px; }}"
        )
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        ok_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 10px; padding: 12px 30px; font-size: 16px; min-height: 40px; }}"
        )
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

    def _type_char(self, ch):
        self._text_input.setText(self._text_input.text() + ch)

    def _on_backspace(self):
        self._text_input.setText(self._text_input.text()[:-1])

    def _on_cancel(self):
        self._result = None
        self.reject()

    def _on_ok(self):
        self._result = self._text_input.text()
        self.accept()

    @staticmethod
    def get_text(parent, title="Invoer", label="", text=""):
        """Show text input dialog and return (text, ok). Blocks until closed."""
        dialog = TextInputDialog(parent, title, label, text)
        # Full-width, centered on parent
        if parent:
            geo = parent.geometry()
            dlg_w = max(900, geo.width() - 40)
            dlg_h = max(550, geo.height() - 60)
            dialog.setFixedSize(dlg_w, dlg_h)
            dialog.move(
                geo.x() + (geo.width() - dlg_w) // 2,
                geo.y() + (geo.height() - dlg_h) // 2,
            )
        else:
            dialog.setFixedSize(900, 600)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        dialog.exec_()
        result = dialog._result
        dialog.deleteLater()
        return result, result is not None


def _layout_display_rotation(layout) -> int:
    """Returnt 0, 90 of 270 — identiek aan PhotoboothWindow._template_display_rotation
    maar als pure helper-functie die op een willekeurig template-object werkt.

    Wordt gebruikt door de template-preview in instellingen + de editor om
    de canvas in dezelfde oriëntatie te tonen als de uiteindelijke share-output.
    """
    if not layout or not getattr(layout, 'frames', None):
        return 0
    rots = {int(getattr(f, 'rotation', 0) or 0) % 360 for f in layout.frames}
    if rots == {90}:
        return 90
    if rots == {270}:
        return 270
    return 0


class LayoutEditorCanvas(QWidget):
    """Interactive canvas for editing layout frames (resize/move)."""

    from PyQt5.QtCore import pyqtSignal
    frameChanged = pyqtSignal()

    HANDLE_SIZE = 14  # px, size of corner resize handles
    MIN_FRAME = 80    # minimum frame dimension in canvas px

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template = None
        self.selected_frame = -1
        self._drag_mode = "none"  # "none" / "move" / "resize_tl/tr/bl/br"
        self._drag_start = None
        self._drag_frame_orig = None
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._bg_pixmap = None  # Background image for the layout
        self._event_bg_pixmap = None  # Event-level background image
        self.setMinimumSize(300, 400)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #1a1a1a;")

    def set_template(self, template):
        from copy import deepcopy
        self.template = deepcopy(template)
        self.selected_frame = -1
        self._load_background()
        self.update()

    def _load_background(self):
        """Load background image from template if available."""
        self._bg_pixmap = None
        if self.template and self.template.background_path:
            bg = QPixmap(self.template.background_path)
            if not bg.isNull():
                self._bg_pixmap = bg

    def set_background(self, path):
        """Set a new background image for the layout."""
        if self.template:
            self.template.background_path = path
        bg = QPixmap(path)
        if not bg.isNull():
            self._bg_pixmap = bg
        self.update()

    def set_event_background(self, path):
        """Set an event-level background image (takes priority over template bg)."""
        self._event_bg_pixmap = None
        if path and os.path.isfile(path):
            bg = QPixmap(path)
            if not bg.isNull():
                self._event_bg_pixmap = bg
        self.update()

    def _display_rotation(self):
        """Returnt 0, 90 of 270 — display-rotatie voor deze template.

        Zie _layout_display_rotation (module-level). Alle frames moeten dezelfde
        90/270 hebben, anders geeft het 0 terug. Voor de editor zorgt dit dat de
        canvas in dezelfde oriëntatie wordt bewerkt als waarin de strip
        uiteindelijk wordt gedeeld/getoond.
        """
        return _layout_display_rotation(self.template)

    def _canvas_size(self):
        """Canvas dimensies op basis van template type + frame-extents.

        triple_strip → 600x1200  (DNP 5x10cm strip)
        4x3_strip    → 1200x900  (4x3 paper, landscape)
        landscape    → 1800x1200 (cloud '4 foto's op een vel')
        anders       → 1200x1800 (4x6 paper, portrait)
        """
        if self.template and getattr(self.template, 'is_triple_strip', False):
            return 600, 1200
        if self.template and getattr(self.template, 'is_4x3_strip', False):
            return 1200, 900
        # Landscape-detectie via pure frame-positie. Als frames buiten 1200px
        # portrait-breedte vallen, moet canvas wel landscape zijn (1800×1200).
        if self.template and self.template.frames:
            _max_x = max(f.x + f.width for f in self.template.frames)
            _max_y = max(f.y + f.height for f in self.template.frames)
            if _max_x > 1200 and _max_x > _max_y:
                return 1800, 1200
        return 1200, 1800

    def _calc_transform(self):
        """Calculate scale/offset to fit canvas in widget.

        Canvas-grootte hangt af van template type:
          triple_strip (DNP) → 600x1200 portrait
          anders             → 1200x1800

        Voor display-rotatie 90/270 worden de effectieve widget-dimensies
        gewisseld (W↔H) bij het bepalen van scale.
        """
        rot = self._display_rotation()
        if rot in (90, 270):
            w, h = self.height(), self.width()
        else:
            w, h = self.width(), self.height()
        canvas_w, canvas_h = self._canvas_size()
        sx = w / canvas_w
        sy = h / canvas_h
        self._scale = min(sx, sy) * 0.94
        self._offset_x = (self.width() - canvas_w * self._scale) / 2
        self._offset_y = (self.height() - canvas_h * self._scale) / 2

    def _to_widget(self, cx, cy):
        return (self._offset_x + cx * self._scale, self._offset_y + cy * self._scale)

    def _to_canvas(self, wx, wy):
        return ((wx - self._offset_x) / self._scale, (wy - self._offset_y) / self._scale)

    def _transform_mouse_pos(self, pos):
        """Map mouse-coords (zoals Qt levert ze in widget-coords) naar de
        PRE-rotation widget-coords zodat hit-tests in _hit_frame/_hit_handle
        gewoon werken alsof er geen rotatie is.

        Voor templates zonder display-rotatie returneert het pos onaangepast.
        """
        rot = self._display_rotation()
        if rot == 0:
            return pos
        from PyQt5.QtGui import QTransform
        cx, cy = self.width() / 2.0, self.height() / 2.0
        # Identieke transform als paintEvent toepast op de painter:
        # translate(cx,cy); rotate(rot); translate(-cx,-cy)
        t = QTransform()
        t.translate(cx, cy)
        t.rotate(rot)
        t.translate(-cx, -cy)
        inv, ok = t.inverted()
        if not ok:
            return pos
        return inv.map(pos)

    def paintEvent(self, event):
        from PyQt5.QtGui import QPainter, QPen, QBrush
        if not self.template:
            return
        self._calc_transform()
        s = self._scale
        ox, oy = self._offset_x, self._offset_y

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Display-rotatie: roteer de hele painter rond het widget-centrum
        # zodat de portrait canvas visueel gedraaid wordt weergegeven. Alle
        # draw-operaties hieronder blijven in PORTRAIT coordinates werken —
        # Qt past de rotatie automatisch toe. Mouse-events worden in de
        # handlers via _transform_mouse_pos terug naar portrait gemapped.
        # NB: zelfde richting als _render_layout_preview (consistent met
        # silhouette-conventie van de thumbnails, omgekeerd t.o.v. share-flow).
        rot = self._display_rotation()
        if rot:
            cx, cy = self.width() / 2.0, self.height() / 2.0
            painter.translate(cx, cy)
            painter.rotate(rot)
            painter.translate(-cx, -cy)

        # Page background - prefer event bg, then template bg, otherwise white
        canvas_w, canvas_h = self._canvas_size()
        is_triple = getattr(self.template, 'is_triple_strip', False)
        page_rect_x, page_rect_y = int(ox), int(oy)
        page_rect_w, page_rect_h = int(canvas_w * s), int(canvas_h * s)
        _active_bg = self._event_bg_pixmap if self._event_bg_pixmap else self._bg_pixmap
        if _active_bg:
            scaled_bg = _active_bg.scaled(
                page_rect_w, page_rect_h,
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            # Center-crop if needed
            crop_x = (scaled_bg.width() - page_rect_w) // 2
            crop_y = (scaled_bg.height() - page_rect_h) // 2
            cropped = scaled_bg.copy(crop_x, crop_y, page_rect_w, page_rect_h)
            painter.drawPixmap(page_rect_x, page_rect_y, cropped)
            painter.setPen(QPen(QColor("#cccccc"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(page_rect_x, page_rect_y, page_rect_w, page_rect_h)
        else:
            painter.setPen(QPen(QColor("#444444"), 1))
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawRect(page_rect_x, page_rect_y, page_rect_w, page_rect_h)

        # Cut line voor single-strip (klassieke Canon dubbele strip):
        # Triple strip heeft GEEN cut-lijn op x=600 (canvas is maar 600 breed),
        # double strip heeft 'm ook niet (de cut zit alleen in single).
        if not self.template.is_double_strip and not is_triple:
            cut_x = int(ox + 600 * s)
            painter.setPen(QPen(QColor("#cc8888"), 2, Qt.DashLine))
            painter.drawLine(cut_x, int(oy), cut_x, int(oy + canvas_h * s))

        # Draw frames
        for i, frame in enumerate(self.template.frames):
            is_sel = (i == self.selected_frame)
            pen_color = "#3399ff" if is_sel else "#44aa44"
            pen_width = 3 if is_sel else 1
            painter.setPen(QPen(QColor(pen_color), pen_width))
            painter.setBrush(QBrush(QColor("#90ee90")))

            fx, fy = self._to_widget(frame.x, frame.y)
            fw, fh = frame.width * s, frame.height * s
            painter.drawRect(int(fx), int(fy), int(fw), int(fh))

            # Mirror only voor single-strip (Canon dubbele). Triple = geen mirror.
            if not self.template.is_double_strip and not is_triple:
                fx2 = ox + (frame.x + 600) * s
                painter.drawRect(int(fx2), int(fy), int(fw), int(fh))

            # Frame number
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.setFont(QFont("DM Sans", max(10, int(28 * s)), QFont.Bold))
            painter.drawText(int(fx), int(fy), int(fw), int(fh),
                             Qt.AlignCenter, str(i + 1))

            # Draw X on diagonals (like the screenshot)
            painter.setPen(QPen(QColor("#888888"), 1))
            painter.drawLine(int(fx), int(fy), int(fx + fw), int(fy + fh))
            painter.drawLine(int(fx + fw), int(fy), int(fx), int(fy + fh))
            # Mirror-X alleen tekenen voor klassieke single-strip Canon — NIET
            # voor triple_strip (daar bestaat fx2 niet) of double_strip.
            if not self.template.is_double_strip and not is_triple:
                painter.drawLine(int(fx2), int(fy), int(fx2 + fw), int(fy + fh))
                painter.drawLine(int(fx2 + fw), int(fy), int(fx2), int(fy + fh))

            # Resize handles for selected frame
            if is_sel:
                hs = self.HANDLE_SIZE
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.setBrush(QBrush(QColor("#3399ff")))
                for hx, hy in [(fx, fy), (fx + fw - hs, fy),
                                (fx, fy + fh - hs), (fx + fw - hs, fy + fh - hs)]:
                    painter.drawRect(int(hx), int(hy), hs, hs)

        painter.end()

    def _hit_handle(self, pos):
        """Return handle name if pos is on a resize handle of selected frame."""
        if self.selected_frame < 0 or not self.template:
            return None
        frame = self.template.frames[self.selected_frame]
        fx, fy = self._to_widget(frame.x, frame.y)
        fw, fh = frame.width * self._scale, frame.height * self._scale
        hs = self.HANDLE_SIZE
        mx, my = pos.x(), pos.y()
        corners = {
            "resize_tl": (fx, fy),
            "resize_tr": (fx + fw - hs, fy),
            "resize_bl": (fx, fy + fh - hs),
            "resize_br": (fx + fw - hs, fy + fh - hs),
        }
        for name, (hx, hy) in corners.items():
            if hx <= mx <= hx + hs and hy <= my <= hy + hs:
                return name
        return None

    def _hit_frame(self, pos):
        """Return frame index if pos is inside a frame, -1 otherwise."""
        if not self.template:
            return -1
        mx, my = pos.x(), pos.y()
        for i, frame in enumerate(self.template.frames):
            fx, fy = self._to_widget(frame.x, frame.y)
            fw, fh = frame.width * self._scale, frame.height * self._scale
            if fx <= mx <= fx + fw and fy <= my <= fy + fh:
                return i
        return -1

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.template:
            return
        # Transform mouse pos voor template-met-display-rotation, anders identiek
        pos = self._transform_mouse_pos(event.pos())
        # Check handle first
        handle = self._hit_handle(pos)
        if handle:
            self._drag_mode = handle
            frame = self.template.frames[self.selected_frame]
            self._drag_start = pos
            self._drag_frame_orig = (frame.x, frame.y, frame.width, frame.height)
            return
        # Check frame
        idx = self._hit_frame(pos)
        if idx >= 0:
            self.selected_frame = idx
            self._drag_mode = "move"
            frame = self.template.frames[idx]
            self._drag_start = pos
            self._drag_frame_orig = (frame.x, frame.y, frame.width, frame.height)
            self.update()
        else:
            self.selected_frame = -1
            self.update()

    def mouseMoveEvent(self, event):
        # Transform mouse pos eerst voor consistent gebruik in hit-tests + delta
        pos = self._transform_mouse_pos(event.pos())
        if not self.template or self._drag_mode == "none" or not self._drag_start:
            # Update cursor
            handle = self._hit_handle(pos)
            if handle:
                self.setCursor(Qt.SizeFDiagCursor if handle in ("resize_tl", "resize_br")
                               else Qt.SizeBDiagCursor)
            elif self._hit_frame(pos) >= 0:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        dx_px = pos.x() - self._drag_start.x()
        dy_px = pos.y() - self._drag_start.y()
        dx = int(dx_px / self._scale)
        dy = int(dy_px / self._scale)
        ox, oy, ow, oh = self._drag_frame_orig
        frame = self.template.frames[self.selected_frame]
        canvas_w, canvas_h = self._canvas_size()

        if self._drag_mode == "move":
            new_x = max(0, min(canvas_w - frame.width, ox + dx))
            new_y = max(0, min(canvas_h - frame.height, oy + dy))
            frame.x, frame.y = new_x, new_y
        elif self._drag_mode == "resize_br":
            frame.width = max(self.MIN_FRAME, min(canvas_w - ox, ow + dx))
            frame.height = max(self.MIN_FRAME, min(canvas_h - oy, oh + dy))
        elif self._drag_mode == "resize_tl":
            new_w = max(self.MIN_FRAME, min(ox + ow, ow - dx))
            new_h = max(self.MIN_FRAME, min(oy + oh, oh - dy))
            frame.x = max(0, ox + ow - new_w)
            frame.y = max(0, oy + oh - new_h)
            frame.width = new_w
            frame.height = new_h
        elif self._drag_mode == "resize_tr":
            frame.width = max(self.MIN_FRAME, min(canvas_w - ox, ow + dx))
            new_h = max(self.MIN_FRAME, min(oy + oh, oh - dy))
            frame.y = max(0, oy + oh - new_h)
            frame.height = new_h
        elif self._drag_mode == "resize_bl":
            new_w = max(self.MIN_FRAME, min(ox + ow, ow - dx))
            frame.x = max(0, ox + ow - new_w)
            frame.width = new_w
            frame.height = max(self.MIN_FRAME, min(canvas_h - oy, oh + dy))

        self.update()
        self.frameChanged.emit()  # Update XY fields in real-time while dragging

    def mouseReleaseEvent(self, event):
        if self._drag_mode != "none":
            self._drag_mode = "none"
            self._drag_start = None
            self._drag_frame_orig = None
            self.frameChanged.emit()


class SubprocessPrintThread(QThread):
    """Print in a separate process to avoid GDI corrupting the main HWND.

    Windows GDI print calls (CreateDC, StretchDIBits) in a QThread corrupt
    the fullscreen geometry when AA_EnableHighDpiScaling is enabled.
    Running in a subprocess eliminates this entirely because the GDI calls
    happen in a separate process with its own HWND table.
    """

    print_complete = pyqtSignal()
    print_failed = pyqtSignal(str)
    print_status = pyqtSignal(str)

    def __init__(self, image_path, printer_name, copies=1, profile_key=None,
                 skip_status_check=False):
        super().__init__()
        self.image_path = image_path
        self.printer_name = printer_name
        self.copies = copies
        self.profile_key = profile_key
        self.skip_status_check = skip_status_check

    def run(self):
        import subprocess

        self.print_status.emit("Bezig met printen...")

        # DNP-profiel als 5e CLI arg ('-' = geen profiel = legacy HiTi pad)
        profile_arg = self.profile_key or "-"
        # 6e CLI arg: '1' = statuscheck + profiel-vereiste overslaan (werkt
        # met elke printer; gezet als de storingsmeldingen uit staan).
        skip_arg = "1" if self.skip_status_check else "0"

        # Determine Python executable and worker script path
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--print-worker",
                   self.image_path, self.printer_name,
                   str(self.copies), config.DATA_DIR, profile_arg, skip_arg]
        else:
            worker_script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "print_worker.py"
            )
            cmd = [sys.executable, worker_script,
                   self.image_path, self.printer_name,
                   str(self.copies), config.DATA_DIR, profile_arg, skip_arg]

        try:
            print(f"[PRINTER] Subprocess: {' '.join(str(c) for c in cmd[:4])}...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.stdout.strip():
                print(f"[PRINTER] {result.stdout.strip()}")
            if result.stderr.strip():
                print(f"[PRINTER] STDERR: {result.stderr.strip()}")
            if result.returncode != 0:
                print(f"[PRINTER] Subprocess exitcode {result.returncode} — print MISLUKT")
                err = (result.stderr or result.stdout or "").strip()[-300:]
                self.print_failed.emit(
                    err or f"Print-worker exitcode {result.returncode}"
                )
                return
            print("[PRINTER] Subprocess OK (exitcode 0)")

        except subprocess.TimeoutExpired:
            print("[PRINTER] Subprocess timeout (120s)")
            self.print_failed.emit("Print-timeout (120s) — spooler hangt mogelijk")
            return
        except Exception as e:
            print(f"[PRINTER] Subprocess fout: {e}")
            self.print_failed.emit(str(e))
            return

        # Alleen bij ECHT succes — anders telt het print-quotum mee voor
        # prints die nooit uit de printer kwamen (paper jam, geen printer).
        self.print_complete.emit()


class PhotoboothWindow(QMainWindow):
    # Cross-thread signals for SumUp payment loop (daemon thread → main thread)
    _sumup_payment_signal = pyqtSignal()
    _sumup_status_signal = pyqtSignal(str)
    # Welcome connectivity check (bg-thread → main thread)
    _welcome_connectivity_signal = pyqtSignal(bool)
    # Periodic booking-refresh result (bg-thread → main thread)
    _periodic_refresh_signal = pyqtSignal(object, str)
    # DNP QW410 status update (bg-thread → main thread). Carries dnp_status.DNPStatus.
    _dnp_status_signal = pyqtSignal(object)
    # Idle-page wifi check (bg-thread → main thread) — toont/verbergt wifi-tip
    _idle_wifi_tip_signal = pyqtSignal(bool)
    # Auto-updater (bg-thread → main thread): check-resultaat + download-voortgang
    _update_check_signal = pyqtSignal(object)   # dict van updater.check_for_update
    _update_progress_signal = pyqtSignal(int)   # download-percentage
    _update_done_signal = pyqtSignal(bool, str) # (gestart?, foutmelding)
    # Filterscherm: thumbnails + preview klaar (bg-thread → main thread)
    _filter_ready_signal = pyqtSignal(object)   # dict met token/idx/base/preview/thumbs

    def __init__(self):
        super().__init__()
        # FramelessWindowHint + WindowStaysOnTopHint are REQUIRED on Surface.
        #
        # Problem: Windows tablet-mode "swipe to close" gesture physically drags
        # the fullscreen window downward on screen and it never comes back up.
        # This happens even with FramelessWindowHint alone because Windows DWM
        # still applies the drag animation to fullscreen windows that aren't
        # truly top-most.
        #
        # Fix: WindowStaysOnTopHint makes the window a WS_EX_TOPMOST window.
        # Windows DWM does NOT apply the swipe-to-close gesture to TOPMOST
        # windows — they are treated as system-level overlays (like the OSK,
        # task manager) and are exempt from tablet-mode gestures.
        self.setWindowFlags(
            self.windowFlags()
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowTitle("Photobooth")
        self.setStyleSheet(STYLESHEET)

        # Booth-wide backend-brand cache — bron voor self.backend_brand
        # wanneer er (nog) geen actief event is (welcome-page). Wordt
        # bijgewerkt in _on_backend_brand_changed.
        self._booth_brand_cache = 'hippe'
        _bs_boot = None
        try:
            from booth_settings import BoothSettings as _BS
            if _BS.exists():
                _bs_boot = _BS.load()
                self._booth_brand_cache = _bs_boot.backend_brand
                print(f"[SETTINGS] Booth-brand cache: {self._booth_brand_cache}")
        except Exception as _bb_ex:
            print(f"[SETTINGS] Booth-brand cache laden mislukt: {_bb_ex}")

        # Connect camera on main thread (EDSDK requires single-thread access)
        # Determine camera mode from saved active event JSON.
        # Default: booth-wide instellingen — zo werkt de camera-keuze ook
        # zonder actief event (welcome-page). Het event-JSON wint hieronder.
        _cam_mode = _bs_boot.camera_mode if _bs_boot else "dslr"
        _wc_idx = _bs_boot.webcam_index if _bs_boot else 0
        _wc_res = _bs_boot.webcam_resolution if _bs_boot else ""
        try:
            import json as _json
            _active_id = self._load_app_setting("active_event_id")
            if _active_id and os.path.isdir(config.EVENTS_DIR):
                for _ef in os.listdir(config.EVENTS_DIR):
                    if _ef.endswith('.json'):
                        _ef_path = os.path.join(config.EVENTS_DIR, _ef)
                        try:
                            with open(_ef_path, 'r', encoding='utf-8') as _f:
                                _ev_data = _json.load(_f)
                            if _ev_data.get('id') == _active_id:
                                _cam_mode = _ev_data.get('camera_mode', 'dslr')
                                _wc_idx = _ev_data.get('webcam_index', 0)
                                _wc_res = _ev_data.get('webcam_resolution', '')
                                print(f"[CAMERA] Event '{_ev_data.get('name')}': mode={_cam_mode}, wc_idx={_wc_idx}")
                                break
                        except Exception:
                            continue
        except Exception as _e:
            print(f"[CAMERA] Event detectie mislukt: {_e}")

        self._digicam_ready = False
        print(f"[CAMERA] Init camera modus: {_cam_mode}")
        if _cam_mode == "webcam":
            # Webcam mode — do NOT load EDSDK DLL
            try:
                from webcam import WebcamCamera
                self.camera = WebcamCamera()
                _wc_name = ""
                try:
                    _wc_name = _ev_data.get('webcam_name', '')
                except NameError:
                    pass
                print(f"[CAMERA] Webcam verbinden: index={_wc_idx}, res={_wc_res}, naam={_wc_name}")
                if self.camera.connect(_wc_idx, _wc_res, _wc_name):
                    self._digicam_ready = True
                    print(f"[CAMERA] Webcam verbonden OK")
                else:
                    print("[CAMERA] Webcam connect mislukt bij init (wordt later opnieuw geprobeerd)")
            except ImportError:
                from camera import Camera
                self.camera = Camera()
                print("[CAMERA] Webcam module niet beschikbaar, fallback naar DSLR")
        else:
            # DSLR mode
            from camera import Camera
            self.camera = Camera()
            ensure_digicam_running()
            if self.camera.connect():
                self._digicam_ready = True
                self.camera.get_camera_properties()
                print("[CAMERA] DSLR verbonden")
        # Capture completion is handled by polling timer (_check_capture_inline)
        # NOT by signal connection (which has 13s queue delay)
        self._lv_connected = False
        self.capture_thread = None
        self.print_thread = None
        self._last_live_pixmap = None

        if getattr(config, 'LED_RELAY_ENABLED', False):
            self.led = LedRelay(config.LED_RELAY_PORT)
        else:
            self.led = None

        # LED-veiligheidssweep: forceert de flits-LED uit zodra we niet meer
        # in een actieve opname-state (COUNTDOWN/CAPTURE) zitten. Vangnet
        # tegen "LED blijft hangen" wanneer de normale off() niet bereikt
        # wordt — bv. gast klikt tijdens de flits op het kruisje, of er
        # treedt een fout/timeout op. Draait altijd, kost vrijwel niets
        # (ensure_off schrijft alleen als de LED echt nog aan staat).
        if self.led:
            self._led_safety_timer = QTimer(self)
            self._led_safety_timer.setInterval(300)
            self._led_safety_timer.timeout.connect(self._led_safety_tick)
            self._led_safety_timer.start()

            # COB-LED reconnect-sweep: probeer de relay elke 10s (her)te
            # verbinden zodat een los, laat-ingeplugd of op een andere
            # COM-poort belanden board alsnog wordt opgepakt. Draait op een
            # bg-thread (serial-open kan even duren) zodat de UI nooit hapert.
            self._led_reconnect_pending = False
            self._led_reconnect_timer = QTimer(self)
            self._led_reconnect_timer.setInterval(10000)  # elke 10 sec
            self._led_reconnect_timer.timeout.connect(self._led_reconnect_tick)
            self._led_reconnect_timer.start()

        self.state = State.IDLE
        # Connect cross-thread SumUp signals (daemon thread → main thread)
        self._sumup_payment_signal.connect(self._sumup_auto_start_session)
        self._sumup_status_signal.connect(self._sumup_update_idle)
        # Welcome connectivity signal (ping bg-thread → main thread)
        self._welcome_connectivity_signal.connect(self._welcome_apply_connectivity)
        # Periodic refresh result (bg-thread → main thread)
        self._periodic_refresh_signal.connect(self._periodic_refresh_apply)
        # DNP QW410 status (libusb-route) — wordt later .start()'d in _do_startup_auth
        self._dnp_poller = None
        self._dnp_last_status = None
        self._dnp_error_overlay = None
        self._dnp_status_signal.connect(self._on_dnp_status_change_main)
        # Idle-page wifi-tip signal (bg-thread → main thread)
        self._idle_wifi_tip_signal.connect(self._on_idle_wifi_state)
        # Auto-updater signals (bg-thread → main thread)
        self._update_check_signal.connect(self._on_update_check_result)
        self._update_progress_signal.connect(self._on_update_progress)
        self._update_done_signal.connect(self._on_update_done)
        self._filter_ready_signal.connect(self._on_filter_thumbs_ready)
        # Timer voor 2-sec wifi-check op idle scherm (zelf gestart in _go_idle)
        self._idle_wifi_check_timer = QTimer(self)
        self._idle_wifi_check_timer.setInterval(2000)
        self._idle_wifi_check_timer.timeout.connect(self._idle_wifi_check_tick)
        self.photos = []           # List of captured photo paths
        self.current_photo_num = 0
        self.strip_path = None
        self._single_strip_path = None
        # Display-versies (eventueel 90/270° gedraaid wanneer de template alle
        # frames met die rotatie heeft). PRINT gebruikt altijd self.strip_path
        # direct; display/share gebruikt deze paden via _display_strip /
        # _display_single_strip properties hieronder.
        self._display_strip_path = None
        self._display_single_strip_path = None
        self.selected_template = None  # Template object
        self._strip_bg = None      # Pre-loaded PIL background image
        self._processed_photos = []
        self._processed_lock = threading.Lock()
        # Filterscherm (na elke foto): gekozen filter per frame-index +
        # invalidatie-token voor async thumbnail-builds + context van de
        # huidige filter-foto.
        self._photo_filters = {}
        self._filter_token = 0
        self._filter_ctx = None
        self._filter_thumb_btns = {}
        self.countdown_value = 0
        self.session_id = None     # Timestamp ID for this session
        self._settings_template_widgets = {}
        self.active_event = None  # Currently active Event
        self._advanced_unlocked = False  # Geavanceerd-tab ontgrendeld deze sessie?
        self._app_start_ts = __import__('time').time()  # voor uptime in status
        self._auth_plan = ""     # Current subscription plan (starter/professional)
        self._cached_user = {}   # Cached user session data (avoids repeated settings.json reads)
        self._auth_lock = threading.Lock()  # Lock for thread-safe _auth_plan updates
        self._gdrive_uploader = None
        self._gdrive_thread = None
        self._capture_search_folders = None
        self._capture_existing_files = None

        # Boomerang GIF
        self._frame_buffer = None
        self._boomerang_thread = None
        self._boomerang_path = None
        self._boomerang_frames = None
        if getattr(config, 'BOOMERANG_ENABLED', False):
            from boomerang import FrameBuffer
            self._frame_buffer = FrameBuffer(
                max_frames=getattr(config, 'BOOMERANG_BUFFER_FRAMES', 30)
            )

        # Email
        self._email_thread = None

        # Sharing screen state
        self._session_prints_used = 0
        self._qr_ready = False
        self._cloud_url = ''

        self._countdown_phase = None  # "intro" or "counting"

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)
        self.review_timer = QTimer(self)
        self.review_timer.setSingleShot(True)
        self.review_timer.timeout.connect(self._start_printing)
        self.done_timer = QTimer(self)
        self.done_timer.setSingleShot(True)
        self.done_timer.timeout.connect(self._go_idle)

        # Page index dictionary (filled during _build_ui)
        self.pages = {}
        # Debounced event save timer
        self._event_save_timer = QTimer(self)
        self._event_save_timer.setSingleShot(True)
        self._event_save_timer.setInterval(500)
        self._event_save_timer.timeout.connect(self._do_debounced_event_save)

        # Load language BEFORE building UI so all t() calls use correct language
        lang = load_language()
        if lang:
            set_language(lang)
            print(f"[I18N] Taal geladen: {lang}")

        self._build_ui()
        self._apply_custom_cursor()

        # Auth check on startup
        self._do_startup_auth()

    # ── Helpers ────────────────────────────────

    def _apply_custom_cursor(self):
        """Create a large, bright cursor that is always visible on dark backgrounds.

        Uses QApplication.setOverrideCursor so the cursor is visible everywhere,
        even over child widgets that might otherwise override it.
        """
        size = 48
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        from PyQt5.QtGui import QPen, QBrush, QPolygonF
        from PyQt5.QtCore import QPointF
        arrow = QPolygonF([
            QPointF(4, 4),
            QPointF(4, 38),
            QPointF(14, 28),
            QPointF(24, 42),
            QPointF(30, 38),
            QPointF(20, 24),
            QPointF(32, 22),
        ])
        # Dark outline
        painter.setPen(QPen(QColor("#000000"), 3))
        painter.setBrush(QBrush(Qt.NoBrush))
        painter.drawPolygon(arrow)
        # White fill
        painter.setPen(QPen(QColor("#000000"), 1.5))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawPolygon(arrow)
        painter.end()
        cursor = QCursor(pixmap, 4, 4)
        # setOverrideCursor overrides ALL widgets including children
        QApplication.setOverrideCursor(cursor)

    @property
    def num_photos(self):
        """Number of photos based on selected template."""
        if self.selected_template:
            return self.selected_template.num_photos
        return 3  # default

    # ── UI Construction ───────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        # Ignored: stack never requests size based on child content — stays fullscreen
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.stack)

        self._build_login_page()
        self.pages["login"] = 0
        self.stack.addWidget(self._build_idle_page())
        self.pages["idle"] = 1
        self._build_template_select_page()
        self.pages["select_template"] = 2
        self._build_preview_page()
        self.pages["preview"] = 3
        self._build_countdown_page()  # No separate page — overlays on preview
        # pages["countdown"] is set inside _build_countdown_page to equal preview
        self._build_review_page()
        self.pages["review"] = 4
        self._build_printing_page()
        self.pages["printing"] = 5
        self._build_qr_page()
        self.pages["qr_code"] = 6
        self._build_email_page()
        self.pages["email_input"] = 7
        self._build_data_collection_page()
        self.pages["data_collection"] = 8
        self._build_done_page()
        self.pages["done"] = 9
        self._build_error_page()
        self.pages["error"] = 10
        self._build_settings_page()
        self.pages["settings"] = 11
        self._build_layout_editor_page()
        self.pages["layout_editor"] = 12
        self._build_voucher_input_page()
        self.pages["voucher_input"] = 13
        self._build_custom_choice_page()
        self.pages["custom_choice"] = 14
        self._build_custom_payment_page()
        self.pages["custom_payment"] = 15
        self._build_welcome_page()
        self.pages["welcome"] = 16
        self._build_qr_scan_page()
        self.pages["scan_qr"] = 17
        self._build_filter_page()
        self.pages["filter"] = 18

    # ── Auth / Login ──────────────────────────

    def _show_language_selection(self):
        """Show a fullscreen language selection dialog at first boot."""
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dlg.setModal(True)
        dlg.setStyleSheet(f"background: {config.COLOR_BG};")
        # Make it fill the screen
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            dlg.setFixedSize(sg.width(), sg.height())
            dlg.move(sg.x(), sg.y())
        else:
            dlg.setFixedSize(1920, 1080)

        lay = QVBoxLayout(dlg)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(30)

        # Title in multiple languages
        title = QLabel("Select your language\nKies je taal\nWähle deine Sprache")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 32, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        lay.addWidget(title)

        lay.addSpacing(20)

        # 2x3 grid of language buttons
        grid = QGridLayout()
        grid.setSpacing(20)

        languages = [
            ("\U0001f1f3\U0001f1f1", "Nederlands", "nl"),
            ("\U0001f1ec\U0001f1e7", "English", "en"),
            ("\U0001f1e9\U0001f1ea", "Deutsch", "de"),
            ("\U0001f1eb\U0001f1f7", "Fran\u00e7ais", "fr"),
            ("\U0001f1ea\U0001f1f8", "Espa\u00f1ol", "es"),
            ("\U0001f1ee\U0001f1f9", "Italiano", "it"),
        ]

        def _on_lang_selected(lang_code):
            save_language(lang_code)
            set_language(lang_code)
            dlg.accept()

        for i, (flag, name, code) in enumerate(languages):
            btn = QPushButton(f"{flag}  {name}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", 22, QFont.Bold))
            btn.setMinimumSize(320, 100)
            btn.setStyleSheet(
                f"QPushButton {{ background: {config.COLOR_CARD_BG}; color: {config.COLOR_TEXT}; "
                f"border: 2px solid {config.COLOR_BORDER}; border-radius: 16px; "
                f"padding: 20px 30px; font-size: 22px; }}"
                f"QPushButton:hover {{ border-color: {config.COLOR_PRIMARY}; background: {config.COLOR_ACCENT}; }}"
                f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; }}"
            )
            btn.clicked.connect(lambda _, c=code: _on_lang_selected(c))
            grid.addWidget(btn, i // 2, i % 2)

        lay.addLayout(grid)
        dlg.exec_()
        # After language is selected, continue with startup auth
        self._do_startup_auth()

    def _do_startup_auth(self):
        """Verhuur-versie: licentie is altijd geldig, plan = professional.

        Geen login-flow, geen banner, geen online verificatie.
        Triggert ook auto-couple bij Linked-modus als event al gekoppeld is.
        """
        lang = load_language()
        if not lang:
            self._show_language_selection()
            return

        with self._auth_lock:
            self._auth_plan = "professional"
            self._cached_user = {"name": "Photobooth Verhuur", "plan": "professional"}
        print("[AUTH] Verhuur-versie — licentie altijd geldig (professional)")
        self._load_active_event()
        self._apply_live_view_alignment()
        self._rebuild_idle_page()
        self._go_idle()

        # Cloud-logs: uploader starten + initiële context (serienummer +
        # event + klant + brand) doorgeven. Vanaf nu syncen alle logs.
        try:
            import log_uploader
            log_uploader.start()
            self._update_log_context()
        except Exception as _lu_ex:
            print(f"[LOG-UPLOAD] Start mislukt: {_lu_ex}")

        # Status-heartbeat: elke 20s een rijke snapshot (scherm, prints,
        # verbindingen camera/COB/printer, internet, uploads, ...) naar de
        # uploader pushen. Op de main thread (veilig Qt-state lezen).
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(
            max(5, int(getattr(config, 'CLOUD_LOG_INTERVAL_SEC', 20))) * 1000)
        self._status_timer.timeout.connect(self._push_status_snapshot)
        self._status_timer.start()
        QTimer.singleShot(2000, self._push_status_snapshot)  # eerste meteen

        # Auto-couple bij Linked-modus
        QTimer.singleShot(500, self._auto_recouple_on_startup)

        # Periodic refresh (60s): re-fetch booking + apply templates wanneer
        # internet beschikbaar en event gekoppeld. Vangt portal-wijzigingen op
        # zonder dat operator op Ververs hoeft te klikken.
        self._periodic_refresh_timer = QTimer(self)
        self._periodic_refresh_timer.setInterval(60_000)  # 60 sec
        self._periodic_refresh_timer.timeout.connect(self._periodic_refresh_tick)
        self._periodic_refresh_timer.start()

        # Cloud-watchdog — altijd-actieve uploader die ALLE pending queues
        # in de gaten houdt, ongeacht event-coupling status. Maakt zeker
        # dat oude foto's geüpload worden zodra wifi terugkomt en/of het
        # event opnieuw gekoppeld wordt.
        try:
            from cloud_uploader import start_watchdog
            self._cloud_watchdog = start_watchdog(config.EVENTS_DIR)
        except Exception as e:
            print(f"[WATCHDOG] Niet gestart: {e}")
            self._cloud_watchdog = None

        # DNP QW410 status-poller — leest via UI Automation. Start via een
        # helper zodat de schakelaar 'Printer-storingsmeldingen' (Geavanceerd
        # → Printen) 'm ook runtime aan/uit kan zetten, met de keuze onthouden
        # in settings.json. Wordt overgeslagen in Verhuurophalen-modus of als
        # de storingsmeldingen uit staan.
        self._dnp_poller = None
        self._start_printer_status_poller()

        # Wifi-monitor uitgeschakeld — gebruiker wil deze flow niet meer zien.
        # Methodes blijven bestaan voor backwards compat maar starten niet.

    def _start_wifi_monitor(self):
        """Start een achtergrond-poll naar internet-connectiviteit (1.1.1.1:53).

        Update self._wifi_connected en triggert UI-refresh bij state-verandering.
        Voorkomt dat de operator een event probeert te koppelen zonder wifi.
        """
        self._wifi_connected = False
        self._wifi_monitor_timer = QTimer(self)
        self._wifi_monitor_timer.timeout.connect(self._poll_wifi_async)
        self._wifi_monitor_timer.start(3000)  # elke 3 sec
        # Eerste check direct
        QTimer.singleShot(100, self._poll_wifi_async)

    def _poll_wifi_async(self):
        """Trigger een background-check (non-blocking)."""
        if getattr(self, '_wifi_poll_pending', False):
            return  # vorige check loopt nog
        self._wifi_poll_pending = True
        threading.Thread(target=self._poll_wifi_worker, daemon=True).start()

    def _poll_wifi_worker(self):
        """Achtergrond-thread: probeer TCP-verbinding naar Cloudflare DNS (1.1.1.1:53).

        Snel, betrouwbaar, geen DNS-lookup nodig. Faalt binnen 2s als geen wifi.
        """
        import socket
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=2):
                connected = True
        except OSError:
            connected = False
        # UI-update op main thread
        QTimer.singleShot(0, lambda c=connected: self._on_wifi_state(c))

    def _on_wifi_state(self, connected: bool):
        """Update interne state + UI bij wijziging."""
        self._wifi_poll_pending = False
        if connected != getattr(self, '_wifi_connected', None):
            self._wifi_connected = connected
            print(f"[WIFI] Status: {'verbonden' if connected else 'geen verbinding'}")
            try:
                self._update_linked_card_visibility()
            except Exception as e:
                print(f"[WIFI] UI-update fout: {e}")

    def _auto_recouple_on_startup(self):
        """Bij opstart: als Linked-modus actief met booking_id → re-verify + start uploader.

        Async via QTimer zodat UI eerst toont. Faalt graceful bij offline.
        Forceert booth_mode='linked' want verhuur-versie kent geen Standalone.
        """
        ev = self.active_event
        if not ev:
            return

        # Verhuur is ALTIJD Linked. Forceer + save als nog niet zo.
        if getattr(ev, 'booth_mode', '') != 'linked':
            ev.booth_mode = 'linked'
            ev.save(config.EVENTS_DIR)
            print("[LINKED] booth_mode geforceerd naar 'linked' (verhuur)")

        if getattr(ev, 'booth_mode', 'standalone') != 'linked':
            return
        booking_id = getattr(ev, 'linked_booking_id', '')
        token = getattr(ev, 'linked_token', '')
        if not booking_id or not token:
            print("[LINKED] Auto-couple overgeslagen — geen booking opgeslagen")
            return

        print(f"[LINKED] Auto-couple voor booking {booking_id}")

        # Re-verify via cloud (met cache fallback)
        from cloud_booking import fetch_booking
        b, err = fetch_booking(token, use_cache_on_offline=True,
                               brand=self.backend_brand)
        if b:
            self._apply_linked_booking(b)
            if err:
                print(f"[LINKED] Re-verify: {err}")
        else:
            print(f"[LINKED] Re-verify mislukt: {err}")
            # Geen halt — uploader kan nog wel runnen voor pending uploads

        # Design re-fetchen (gebruikt cache als offline)
        if ev.linked_design_path:
            ok, ferr = self._fetch_and_apply_linked_design()
            if not ok:
                print(f"[LINKED] Design re-fetch: {ferr}")

        # Uploader starten voor pending queue (alleen als token + booking_id geldig)
        self._start_linked_uploader()
        self._update_linked_card_visibility()

    def _periodic_refresh_tick(self):
        """Background tick — re-fetch booking + apply templates als gekoppeld
        en online. Stilte: geen UI-dialog, geen interruptions. Skip tijdens
        actieve sessie (state != IDLE) zodat de gast niet wordt onderbroken.
        """
        ev = self.active_event
        if not ev:
            return
        token = getattr(ev, 'linked_token', '') or ''
        booking_id = getattr(ev, 'linked_booking_id', '') or ''
        if not token or not booking_id:
            return  # geen gekoppeld event
        # Skip tijdens actieve sessie / settings / etc — alleen IDLE
        if hasattr(self, 'state') and self.state != State.IDLE:
            return
        if not getattr(self, '_has_internet', False):
            return  # offline: skip

        # Async fetch op background-thread zodat UI niet bevriest
        import threading
        def _bg():
            try:
                from cloud_booking import fetch_booking, fetch_design
                b, err = fetch_booking(token, use_cache_on_offline=False,
                                       brand=self.backend_brand)
                if not b:
                    print(f"[PERIODIC-REFRESH] Booking fetch fout: {err}")
                    return
                # Apply op main thread via singleShot
                local_path = ""
                design_path = b.get("booking", {}).get("photostrip_design_url", "") or ""
                if design_path:
                    local, _derr = fetch_design(token, design_path, booking_id)
                    if local:
                        local_path = local
                # Cross-thread via pyqtSignal (singleShot in bg-thread werkt niet)
                self._periodic_refresh_signal.emit(b, local_path)
            except Exception as e:
                print(f"[PERIODIC-REFRESH] Achtergrond-fout: {e}")
        threading.Thread(target=_bg, daemon=True).start()

    def _periodic_refresh_apply(self, booking_data, design_local_path):
        """Op main thread aangeroepen na periodic fetch. Stil toepassen."""
        if not self.active_event:
            return
        # Skip als de gast intussen aan het werk is
        if hasattr(self, 'state') and self.state != State.IDLE:
            return
        try:
            self._apply_linked_booking(booking_data)
            # Cloud-templates auto-toepassen (force_regen=False = respect user-edits)
            self._apply_design_to_template(design_local_path or "", force_regen=False)
            print("[PERIODIC-REFRESH] Booking + templates ververst")
        except Exception as e:
            print(f"[PERIODIC-REFRESH] Apply-fout: {e}")

    def _on_dnp_status_change_main(self, status):
        """Op main thread aangeroepen bij status-verandering van de QW410.

        Logica:
          - level=ERROR + connected=True  → toon fullscreen overlay met code+label
          - level=ERROR + connected=False → toon overlay 'printer niet bereikbaar'
          - level=WARNING                 → niets blokkeren, alleen loggen
          - level=OK/INFO                 → verberg overlay als die open staat
          - level=UNKNOWN                 → niks (filter niet geinstalleerd)
        """
        self._dnp_last_status = status
        print(f"[DNP-STATUS] level={status.level.value} code={status.code} "
              f"label={status.label!r} connected={status.connected} "
              f"method={status.error_method}")

        # Storingsmeldingen uitgezet door de operator (bv. tijdelijke
        # niet-DNP printer zoals een Canon CP1500): nooit een overlay tonen.
        if not self._printer_status_enabled():
            if getattr(self, '_dnp_error_overlay', None) is not None:
                self._hide_dnp_error_overlay()
            return

        # In settings NOOIT een fout-overlay — de operator moet bij de
        # instellingen kunnen (printer kiezen, profiel vastleggen) juist
        # wanneer er een printerprobleem is. Sluit ook een eventueel nog
        # openstaande overlay (vangnet voor elke settings-route).
        if hasattr(self, 'state') and self.state == State.SETTINGS:
            if self._dnp_error_overlay is not None:
                self._hide_dnp_error_overlay()
            return

        # Geen event gekoppeld (welcome/QR-scan scherm) OF printen staat
        # uit: GEEN printer-meldingen. Zonder koppeling is de huurder nog
        # bezig met de setup; met printen uit is de printer simpelweg
        # niet relevant. Meldingen verschijnen vanzelf zodra gekoppeld én
        # printen aan staat én de fout nog bestaat.
        ev = self.active_event
        coupled = bool(ev and getattr(ev, 'linked_booking_id', ''))
        if not coupled or not self.effective_print_enabled:
            if self._dnp_error_overlay is not None:
                self._hide_dnp_error_overlay()
            if hasattr(self, '_welcome_printer_banner'):
                try:
                    self._welcome_printer_banner.hide()
                except Exception:
                    pass
            return

        # Skip overlay tijdens actieve sessie — onderbreekt de gast
        in_session = hasattr(self, 'state') and self.state != State.IDLE

        from dnp_status import StatusLevel
        # Toon overlay bij ERROR-status OF wanneer de printer offline is.
        # 'is_blocking()' dekt beide (ERROR of niet-connected), maar we
        # filtren UNKNOWN+connected uit (dat is "weet niet" — geen reden
        # voor een rood scherm).
        # Overlay bij ERROR-level of écht offline (level != UNKNOWN sluit
        # de 'libusb-backend mist' situatie uit — daar weten we niks en
        # zou een permanent vals "niet aangesloten" scherm verschijnen).
        should_show = (
            status.level == StatusLevel.ERROR
            or (not status.connected and status.level != StatusLevel.UNKNOWN)
        )
        if should_show and not in_session:
            self._show_dnp_error_overlay(status)
        elif not should_show and self._dnp_error_overlay is not None:
            self._hide_dnp_error_overlay()
            # Pending print? Automatisch alsnog versturen zodra fout op is.
            pending = getattr(self, '_pending_print_copies', None)
            if pending is not None:
                self._pending_print_copies = None
                print(f"[PRINT-PRECHECK] Fout opgelost — pending print "
                      f"({pending} kopie(ën)) wordt nu verstuurd")
                # Korte delay om de driver tijd te geven na recovery
                QTimer.singleShot(800, lambda c=pending: self._do_print_job(copies=c))

        # Welcome-page printer-banner update
        if hasattr(self, '_welcome_printer_banner'):
            try:
                if status.is_blocking():
                    code_str = f" (code {status.code})" if status.code else ""
                    label = status.label or "Printer-fout"
                    advice = self._dnp_advice_for(status)
                    self._welcome_printer_banner.setText(
                        f"⚠  Printer: {label}{code_str}\n{advice}"
                    )
                    self._welcome_printer_banner.show()
                else:
                    self._welcome_printer_banner.hide()
            except Exception:
                pass

    def _show_dnp_error_overlay(self, status):
        """Toon fullscreen rode overlay met printer-fout. Idempotent."""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton

        if self._dnp_error_overlay is not None:
            # Update tekst maar laat overlay staan
            self._update_dnp_overlay_content(status)
            return

        overlay = QWidget(self)
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setStyleSheet(
            "background: rgba(180,30,30,0.96);"
        )
        lay = QVBoxLayout(overlay)
        lay.setContentsMargins(80, 80, 80, 80)
        lay.setSpacing(24)
        lay.addStretch()

        icon = QLabel("⚠")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFont(QFont("DM Sans", 96, QFont.Bold))
        icon.setStyleSheet("color: white; background: transparent;")
        lay.addWidget(icon)

        title = QLabel("Printer-fout")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 44, QFont.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        lay.addWidget(title)

        msg = QLabel()
        msg.setAlignment(Qt.AlignCenter)
        msg.setFont(QFont("DM Sans", 22))
        msg.setWordWrap(True)
        msg.setStyleSheet("color: white; background: transparent; padding: 0 40px;")
        lay.addWidget(msg)

        detail = QLabel()
        detail.setAlignment(Qt.AlignCenter)
        detail.setFont(QFont("DM Sans", 16))
        detail.setWordWrap(True)
        detail.setStyleSheet(
            "color: rgba(255,255,255,0.75); background: transparent;"
        )
        lay.addWidget(detail)

        lay.addStretch()

        # 'Opnieuw checken' knop
        retry = QPushButton("Opnieuw checken")
        retry.setCursor(Qt.PointingHandCursor)
        retry.setFont(QFont("DM Sans", 18, QFont.Bold))
        retry.setFixedHeight(64)
        retry.setStyleSheet(
            "QPushButton { background: white; color: #b01e1e; "
            "border-radius: 14px; padding: 8px 48px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.85); }"
        )
        retry.clicked.connect(self._on_dnp_retry_clicked)
        lay.addWidget(retry, alignment=Qt.AlignCenter)

        # 'Stappenplan met plaatjes' knop — visuele uitleg uit DNP-handleiding
        help_btn = QPushButton("📖  Stappenplan met plaatjes")
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        help_btn.setFixedHeight(60)
        help_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.92); color: #b01e1e; "
            "border: none; border-radius: 14px; padding: 8px 36px; }"
            "QPushButton:hover { background: white; }"
        )
        help_btn.clicked.connect(self._on_dnp_help_clicked)
        lay.addWidget(help_btn, alignment=Qt.AlignCenter)
        self._dnp_overlay_help_btn = help_btn

        # 'Annuleer print' knop — alleen zichtbaar bij actieve pending print
        cancel_print = QPushButton("✕  Annuleer print")
        cancel_print.setCursor(Qt.PointingHandCursor)
        cancel_print.setFont(QFont("DM Sans", 15, QFont.Bold))
        cancel_print.setFixedHeight(56)
        cancel_print.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.18); color: white; "
            "border: 1px solid rgba(255,255,255,0.35); border-radius: 12px; "
            "padding: 8px 36px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.28); }"
        )
        cancel_print.clicked.connect(self._on_dnp_overlay_cancel_print)
        cancel_print.setVisible(
            getattr(self, '_pending_print_copies', None) is not None
        )
        lay.addWidget(cancel_print, alignment=Qt.AlignCenter)
        self._dnp_overlay_cancel_print_btn = cancel_print

        lay.addStretch()

        # ── Onderste rij: [info-knop  ◇  slotje] — altijd bereikbaar ─
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)

        info_btn = QPushButton("ℹ  Printer-info")
        info_btn.setFixedHeight(48)
        info_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        info_btn.setCursor(Qt.PointingHandCursor)
        info_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.18); color: white; "
            "border: 1px solid rgba(255,255,255,0.35); border-radius: 10px; "
            "padding: 6px 22px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.3); }"
        )
        info_btn.clicked.connect(self._show_event_info_dialog)
        bottom_row.addWidget(info_btn)

        bottom_row.addStretch()

        lock_btn = QPushButton("🔒")
        lock_btn.setFixedSize(60, 60)
        lock_btn.setCursor(Qt.PointingHandCursor)
        lock_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.18); "
            "border: 1px solid rgba(255,255,255,0.35); "
            "border-radius: 30px; font-size: 24px; color: white; }"
            "QPushButton:hover { background: rgba(255,255,255,0.3); }"
        )
        lock_btn.clicked.connect(self._on_lock_clicked)
        bottom_row.addWidget(lock_btn)

        lay.addLayout(bottom_row)

        overlay.show()
        overlay.raise_()
        self._dnp_error_overlay = overlay
        # Stash widget-refs voor latere updates
        self._dnp_overlay_msg = msg
        self._dnp_overlay_detail = detail
        self._dnp_overlay_lock_btn = lock_btn
        self._dnp_overlay_info_btn = info_btn

        # QR-code rechtsboven: "Bekijk uitlegvideo's" naar fotoboothje.nl/videoretos
        try:
            self._build_dnp_overlay_qr(overlay)
        except Exception as e:
            print(f"[DNP-OVERLAY] QR-code generatie fout: {e}")

        self._update_dnp_overlay_content(status)

    def _build_dnp_overlay_qr(self, overlay):
        """Render QR-code + label rechtsboven op de printer-fout overlay.
        Floating child (geen layout), positioneert mee bij resize."""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
        import qrcode
        from io import BytesIO

        url = "https://www.fotoboothje.nl/videos"

        qr_box = QWidget(overlay)
        qr_box.setStyleSheet(
            "background: white; border-radius: 14px;"
        )
        ql = QVBoxLayout(qr_box)
        ql.setContentsMargins(12, 12, 12, 10)
        ql.setSpacing(6)

        # Titel
        head = QLabel("📺  Bekijk uitlegvideo's")
        head.setAlignment(Qt.AlignCenter)
        head.setFont(QFont("DM Sans", 12, QFont.Bold))
        head.setStyleSheet("color: #b01e1e; background: transparent;")
        ql.addWidget(head)

        # QR-image
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        qr = qrcode.QRCode(version=1, box_size=6, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)
        pixmap = QPixmap()
        pixmap.loadFromData(buf.read())
        scaled = pixmap.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        qr_label.setPixmap(scaled)
        qr_label.setFixedSize(170, 170)
        ql.addWidget(qr_label, alignment=Qt.AlignCenter)

        # Sub-label met URL
        sub = QLabel("fotoboothje.nl/videos")
        sub.setAlignment(Qt.AlignCenter)
        sub.setFont(QFont("DM Sans", 10))
        sub.setStyleSheet("color: #555; background: transparent;")
        ql.addWidget(sub)

        qr_box.adjustSize()
        qr_box.setFixedSize(qr_box.sizeHint())
        # Positie rechtsboven met marge
        ow = overlay.width()
        margin = 40
        qr_box.move(ow - qr_box.width() - margin, margin)
        qr_box.show()
        qr_box.raise_()
        self._dnp_overlay_qr_box = qr_box

    def _update_dnp_overlay_content(self, status):
        if not hasattr(self, '_dnp_overlay_msg') or self._dnp_overlay_msg is None:
            return
        if not status.connected:
            self._dnp_overlay_msg.setText("Printer niet aangesloten")
            base_detail = (
                "👉  Wat moet je doen?\n"
                "1. Controleer of de USB-kabel goed in de printer én in de tablet zit\n"
                "2. Controleer of de printer aan staat (groen lampje voor)\n"
                "3. Zet 'm zonodig uit en weer aan\n\n"
                "✓  De foto's gaan niet verloren — de printer probeert het opnieuw "
                "zodra de verbinding terug is.\n"
                "📺  Bekijk de uitlegvideo via de QR-code rechtsboven."
            )
        else:
            code_str = f"  (code {status.code})" if status.code else ""
            # Label-override: bij "Papier op"/"Lint op" maar nog veel
            # prints over → noem 't een 'fout' zodat klanten niet denken
            # dat de rol echt leeg is (anti-verspilling).
            label = status.label
            prints_left = getattr(status, 'prints_remaining', None)
            thr = self._DNP_REPLACE_THRESHOLD
            if prints_left is not None and prints_left > thr:
                if status.code == 1100:
                    label = "Papier-fout (rol niet leeg!)"
                elif status.code == 1200:
                    label = "Lint-fout (rol niet leeg!)"
            self._dnp_overlay_msg.setText(f"{label}{code_str}")
            base_detail = self._dnp_advice_for(status)
        # Hint over pending print toevoegen als er nu een wacht
        is_pending = getattr(self, '_pending_print_copies', None) is not None
        if is_pending:
            base_detail += "\n\n✓  De print wordt automatisch verstuurd zodra dit opgelost is."
        self._dnp_overlay_detail.setText(base_detail)
        # Annuleer-print knop alleen zichtbaar bij pending
        if hasattr(self, '_dnp_overlay_cancel_print_btn') \
                and self._dnp_overlay_cancel_print_btn is not None:
            try:
                self._dnp_overlay_cancel_print_btn.setVisible(is_pending)
            except Exception:
                pass
        # Help-knop alleen tonen als er visuele uitleg is voor deze code
        if hasattr(self, '_dnp_overlay_help_btn') \
                and self._dnp_overlay_help_btn is not None:
            try:
                import dnp_help
                code_for_help = status.code
                # Bij 'connected=False' is er geen specifieke code, gebruik 9999
                if not status.connected and not code_for_help:
                    code_for_help = 9999
                has_help = dnp_help.get_help(code_for_help) is not None
                self._dnp_overlay_help_btn.setVisible(has_help)
                self._dnp_overlay_help_code = code_for_help if has_help else None
            except Exception as e:
                print(f"[DNP-HELP] visibility-check fout: {e}")
                self._dnp_overlay_help_btn.setVisible(False)

    def _on_dnp_help_clicked(self):
        """Open dialog met visuele DNP-handleiding-stappen voor de huidige fout."""
        code = getattr(self, '_dnp_overlay_help_code', None)
        if code is None and self._dnp_last_status is not None:
            code = self._dnp_last_status.code
        if code is None:
            return
        self._show_dnp_help_dialog(code)

    def _show_dnp_help_dialog(self, code: int):
        """Toon fullscreen dialog met vertaalde handleiding-stappen + plaatjes."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QPushButton, QScrollArea, QWidget, QFrame)
        import dnp_help

        help_data = dnp_help.get_help(code)
        if not help_data:
            return
        steps = dnp_help.steps_with_existing_images(code)

        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dlg.setModal(True)
        dlg.setStyleSheet(f"background: {config.COLOR_BG};")
        # Fullscreen
        sw, sh = self.width(), self.height()
        dlg.setFixedSize(int(sw * 0.92), int(sh * 0.92))

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QFrame()
        header.setStyleSheet(
            f"background: #b01e1e; border-top-left-radius: 14px; "
            f"border-top-right-radius: 14px;"
        )
        header.setFixedHeight(80)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(28, 12, 18, 12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        tl = QLabel(f"📖  {help_data['title']}")
        tl.setFont(QFont("DM Sans", 22, QFont.Bold))
        tl.setStyleSheet("color: white; background: transparent;")
        title_box.addWidget(tl)
        sub = QLabel(f"Foutcode {code} — bron: DNP QW410 handleiding")
        sub.setFont(QFont("DM Sans", 11))
        sub.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent;")
        title_box.addWidget(sub)
        hl.addLayout(title_box, stretch=1)

        close_btn = QPushButton("✕  Sluiten")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        close_btn.setFixedSize(150, 50)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.18); color: white; "
            "border: 1px solid rgba(255,255,255,0.4); border-radius: 10px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.3); }"
        )
        close_btn.clicked.connect(dlg.accept)
        hl.addWidget(close_btn)
        root.addWidget(header)

        # Scrollable stappen
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            f"QScrollBar:vertical {{ background: {config.COLOR_BG}; width: 16px; }}"
            f"QScrollBar::handle:vertical {{ background: {config.COLOR_BORDER}; "
            f"border-radius: 8px; min-height: 60px; }}"
        )
        from PyQt5.QtWidgets import QScroller
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(40, 30, 40, 30)
        cl.setSpacing(28)

        if not steps:
            empty = QLabel("Geen stappen beschikbaar voor deze code.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setFont(QFont("DM Sans", 16))
            cl.addWidget(empty)

        max_img_w = int(sw * 0.55)
        for idx, (img_path, txt) in enumerate(steps, 1):
            step_frame = QFrame()
            step_frame.setStyleSheet(
                f"background: {config.COLOR_CARD_BG}; "
                f"border-radius: 14px;"
            )
            sl = QVBoxLayout(step_frame)
            sl.setContentsMargins(24, 20, 24, 20)
            sl.setSpacing(14)

            # Step number
            num = QLabel(f"Stap {idx} van {len(steps)}")
            num.setFont(QFont("DM Sans", 11, QFont.Bold))
            num.setStyleSheet(f"color: {config.COLOR_PRIMARY_HOVER}; background: transparent;")
            sl.addWidget(num)

            # Image (indien aanwezig)
            if img_path:
                pix = QPixmap(img_path)
                if not pix.isNull():
                    pix = pix.scaled(max_img_w, int(sh * 0.55),
                                     Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    img_lbl = QLabel()
                    img_lbl.setPixmap(pix)
                    img_lbl.setAlignment(Qt.AlignCenter)
                    img_lbl.setStyleSheet("background: white; border-radius: 10px; padding: 8px;")
                    sl.addWidget(img_lbl, alignment=Qt.AlignCenter)

            # Tekst
            tlbl = QLabel(txt)
            tlbl.setFont(QFont("DM Sans", 14))
            tlbl.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            tlbl.setWordWrap(True)
            sl.addWidget(tlbl)

            cl.addWidget(step_frame)

        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # Bottom: nog een sluit-knop voor lange dialogen
        bottom = QFrame()
        bottom.setStyleSheet(f"background: {config.COLOR_CARD_BG}; "
                             f"border-bottom-left-radius: 14px; "
                             f"border-bottom-right-radius: 14px;")
        bottom.setFixedHeight(80)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(28, 14, 28, 14)

        done_btn = QPushButton("✓  Klaar — terug naar foutmelding")
        done_btn.setCursor(Qt.PointingHandCursor)
        done_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        done_btn.setFixedHeight(52)
        done_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 12px; padding: 8px 40px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        done_btn.clicked.connect(dlg.accept)
        bl.addStretch()
        bl.addWidget(done_btn)
        bl.addStretch()
        root.addWidget(bottom)

        # Center on screen
        dlg.move(
            self.x() + (self.width() - dlg.width()) // 2,
            self.y() + (self.height() - dlg.height()) // 2,
        )
        dlg.exec_()

    # Drempel: bij meer dan dit aantal prints over is "Paper end" / "Ribbon end"
    # vrijwel zeker GEEN echte lege rol, maar een jam / scheur / verkeerd
    # geladen media. NIET vervangen — eerst checken.
    _DNP_REPLACE_THRESHOLD = 10

    def _dnp_advice_for(self, status):
        """Specifiek advies per QW410-foutcode.

        Voor 'op'-meldingen (papier/lint) wordt prints_remaining meegewogen:
        meer dan 10 prints over = waarschijnlijk een jam of verkeerde plaatsing
        (NIET vervangen). 10 of minder = echt leeg (wel vervangen, beide samen).
        Teksten zijn bewust kort gehouden voor leesbaarheid op het scherm.
        """
        c = status.code or 0
        prints_left = getattr(status, 'prints_remaining', None)
        thr = self._DNP_REPLACE_THRESHOLD

        def _fix_not_replace(media_label: str, jam_hint: str) -> str:
            return (
                f"⚠️  Nog {prints_left} prints over — NIET vervangen!\n"
                f"{jam_hint}\n\n"
                "1. Open de klep\n"
                f"2. Check of {media_label} recht en strak zit\n"
                "3. Plaats terug + sluit klep\n\n"
                "❌  Weggooien = kosten voor jou\n"
                "📺  QR rechtsboven: uitlegvideo\n"
                "📞  Lukt 't niet? Bel ons."
            )

        def _replace_both(media_label: str) -> str:
            return (
                f"De {media_label} is op.\n\n"
                "⚠️  Vervang BEIDE samen:\n"
                "• Papierrol  • Kleurlint\n"
                "(gebruikte set terug in de doos)\n\n"
                "📺  QR rechtsboven: uitlegvideo"
            )

        if c == 1000:
            return "Sluit de klep en wacht 5 sec."
        if c == 1010:
            return "Plaats de opvangbak terug onderin de printer."
        if c == 1100:  # Paper end
            if prints_left is not None and prints_left > thr:
                return _fix_not_replace(
                    "het papier",
                    "Papier is waarschijnlijk gescheurd, scheef of vastgelopen."
                )
            return _replace_both("papierrol")
        if c == 1200:  # Ribbon end
            if prints_left is not None and prints_left > thr:
                return _fix_not_replace(
                    "het lint",
                    "Lint is waarschijnlijk gescheurd, slap of scheef geladen."
                )
            return _replace_both("ribbon")
        if c == 1300:  # Paper jam
            return (
                "Papier vastgelopen.\n\n"
                "1. Open de klep\n"
                "2. Verwijder voorzichtig het papier\n"
                "3. Sluit de klep\n\n"
                "❌  Vervang alleen bij echte schade\n"
                "📺  QR rechtsboven: uitlegvideo"
            )
        if c == 1400:  # Ribbon error
            if prints_left is not None and prints_left > thr:
                return _fix_not_replace(
                    "het lint",
                    "Het lint zit niet goed gespannen of scheef."
                )
            return (
                "Lint-fout — check spanning + scheuren.\n"
                "Bij echte schade: vervang BEIDE.\n\n"
                "📺  QR rechtsboven: uitlegvideo"
            )
        if c == 1500:
            return "Verkeerd papierformaat voor dit ontwerp."
        if c in (2000, 2100, 2200, 2300, 2400, 2700, 3000, 3010):
            return ("Hardware-fout. Printer uit, 10 sec wachten, weer aan.\n"
                    "📞  Bij herhaling: bel support.")
        if c in (2500, 2600):
            return "Printer te heet — wacht 1 min, probeert vanzelf opnieuw."
        if c == 9999:
            return ("Communicatie-fout. Check USB-kabel + herstart printer.\n"
                    "📺  QR rechtsboven: uitlegvideo")
        return status.detail or "Volg de instructies op de printer."

    def _hide_dnp_error_overlay(self):
        if self._dnp_error_overlay is not None:
            try:
                self._dnp_error_overlay.hide()
                self._dnp_error_overlay.deleteLater()
            except Exception:
                pass
            self._dnp_error_overlay = None
            self._dnp_overlay_msg = None
            self._dnp_overlay_detail = None
            self._dnp_overlay_qr_box = None

    def _refresh_cloud_uploads_ui(self):
        """Herschrijf de per-booking lijst onder de Cloud-uploads card."""
        if not hasattr(self, '_cloud_uploads_list') or self._cloud_uploads_list is None:
            return
        # Skip refresh als het settings-scherm niet open is (geen verspilling)
        if getattr(self, 'state', None) != State.SETTINGS:
            return
        try:
            from cloud_uploader import discover_pending_uploads
            snapshots = discover_pending_uploads(config.EVENTS_DIR)
        except Exception as e:
            print(f"[CLOUD-UI] discover faalde: {e}")
            return

        # Clear oude widgets
        lay = self._cloud_uploads_list.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not snapshots:
            empty = QLabel("Geen upload-queues gevonden — alle foto's zijn al weg of er zijn nog geen events geweest.")
            empty.setWordWrap(True)
            empty.setFont(QFont("DM Sans", 11))
            empty.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; padding: 10px;")
            lay.addWidget(empty)
            return

        for booking_id, info in snapshots.items():
            row = QFrame()
            row.setStyleSheet(
                f"QFrame {{ background: {config.COLOR_INPUT_BG}; "
                f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 10px; }}"
            )
            row_lay = QVBoxLayout(row)
            row_lay.setSpacing(4)

            has_token = bool(info.get("token"))
            token_marker = ("✓ token" if has_token
                            else "⚠ GEEN token (scan QR van deze booking 1x opnieuw)")
            token_color = config.COLOR_SUCCESS if has_token else config.COLOR_DANGER

            label_part = info.get("booking_label") or ""
            name_part = (f"<b>{label_part}</b>  ·  {booking_id[:8]}"
                         if label_part else f"<b>{booking_id[:8]}</b>")
            header = QLabel(f"{name_part}  ·  {token_marker}")
            header.setFont(QFont("DM Sans", 12, QFont.Bold))
            header.setStyleSheet(f"color: {token_color}; background: transparent;")
            row_lay.addWidget(header)

            total = info.get("total", 0)
            uploaded = info.get("uploaded", 0)
            pending = info.get("pending", 0)
            failed = info.get("failed", 0)
            uploading = info.get("uploading", 0)
            pct = int(100 * uploaded / max(1, total))
            stat_line = (
                f"Geüpload: {uploaded}/{total} ({pct}%)  ·  "
                f"Wachtend: {pending}  ·  Bezig: {uploading}  ·  "
                f"Mislukt: {failed}"
            )
            stat_lbl = QLabel(stat_line)
            stat_lbl.setFont(QFont("DM Sans", 11))
            stat_lbl.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            row_lay.addWidget(stat_lbl)

            # Per-booking actie-knop voor force-retry
            btn = QPushButton("🔁 Opnieuw")
            btn.setFont(QFont("DM Sans", 10, QFont.Bold))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{ background: {config.COLOR_ACCENT}; "
                f"color: {config.COLOR_TEXT}; border: 1px solid {config.COLOR_BORDER}; "
                f"border-radius: 6px; padding: 4px 14px; }}"
                f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; "
                f"color: {config.COLOR_TEXT_ON_PRIMARY}; }}"
            )
            btn.clicked.connect(lambda _c, bid=booking_id: self._on_cloud_retry_booking(bid))
            row_lay.addWidget(btn, alignment=Qt.AlignRight)

            lay.addWidget(row)

    def _on_cloud_retry_booking(self, booking_id: str):
        """Force-retry alle pending+failed van 1 booking."""
        try:
            from cloud_uploader import force_retry_all
            res = force_retry_all(booking_id)
            print(f"[CLOUD-UI] Force-retry {booking_id}: {res}")
        except Exception as e:
            print(f"[CLOUD-UI] retry fout: {e}")
        self._refresh_cloud_uploads_ui()

    def _on_cloud_retry_all_clicked(self):
        """Force-retry voor ALLE gevonden bookings."""
        try:
            from cloud_uploader import discover_pending_uploads, force_retry_all
            snapshots = discover_pending_uploads(config.EVENTS_DIR)
            for bid in snapshots.keys():
                res = force_retry_all(bid)
                print(f"[CLOUD-UI] Retry-all {bid}: {res}")
        except Exception as e:
            print(f"[CLOUD-UI] retry-all fout: {e}")
        self._refresh_cloud_uploads_ui()

    def _on_cloud_clear_done_clicked(self):
        """Voltooide uploads (state=uploaded) uit alle queues verwijderen."""
        try:
            from cloud_uploader import discover_pending_uploads, clear_uploaded
            snapshots = discover_pending_uploads(config.EVENTS_DIR)
            total = 0
            for bid in snapshots.keys():
                n = clear_uploaded(bid)
                total += n
                print(f"[CLOUD-UI] Cleared {bid}: {n}")
            print(f"[CLOUD-UI] Totaal verwijderd: {total}")
        except Exception as e:
            print(f"[CLOUD-UI] clear-done fout: {e}")
        self._refresh_cloud_uploads_ui()

    def _pause_dnp_poll(self, paused: bool):
        """Pauzeer/hervat de DNP-poller. Veilig om vaker te roepen.

        Reden: de UI Automation poll-cyclus kan keyboard-focus stelen
        wanneer Windows tussen actieve windows wisselt. Touch input
        gebruikt een andere driver-pad (WM_POINTER) dus blijft werken,
        maar pc-toetsenbord/muis kan onderbroken raken. We pauzeren
        dus alles wat niet IDLE is.

        Printen uitgeschakeld → poller blijft ALTIJD gepauzeerd: er valt
        niks te bewaken en de poll-cyclus kost onnodig resources.
        """
        if getattr(self, '_dnp_poller', None) is None:
            return
        if not paused and (not self.effective_print_enabled
                           or self.backend_brand == 'huren'):
            paused = True
        try:
            self._dnp_poller.pause(paused)
        except Exception:
            pass

    def _on_dnp_overlay_cancel_print(self):
        """User klikt 'Annuleer print' op de fout-overlay → vergeet de
        pending print. Overlay blijft staan tot de fout opgelost is
        (operator wil 'm fixen, maar er hoeft geen print meer te volgen)."""
        self._pending_print_copies = None
        if hasattr(self, '_dnp_overlay_cancel_print_btn') \
                and self._dnp_overlay_cancel_print_btn is not None:
            try:
                self._dnp_overlay_cancel_print_btn.setVisible(False)
            except Exception:
                pass
        # Update detail-tekst (pending-hint moet weg)
        st = getattr(self, '_dnp_last_status', None)
        if st:
            self._update_dnp_overlay_content(st)
        print("[PRINT-PRECHECK] Pending print geannuleerd door operator")

    def _on_dnp_retry_clicked(self):
        """User klikt 'Opnieuw checken' — forceer een poller-refresh.

        Gebruikt de poller (juiste printernaam uit config + USB cross-check)
        in een bg-thread zodat de UI niet blokkeert. De poller vuurt zelf
        de status-callback → _dnp_status_signal → overlay update.
        Voorheen: losse read_qw410_status() met hardcoded default
        "DP-QW410 (Kopie 2)" — pollde op andere machines de verkeerde
        printer en sloot de overlay onterecht.
        """
        if self._dnp_poller is None:
            return
        threading.Thread(
            target=self._dnp_poller.force_refresh, daemon=True
        ).start()

    def _background_auth_verify(self):
        """Background thread: verify session online, update plan if needed.

        If the license is expired online, deactivate immediately:
        clear session, reset plan, and rebuild idle page with banner.
        """
        try:
            success, user, error = auth.verify_session_online()
            if success and user:
                self._auth_plan = user.get("plan", self._auth_plan)
                print(f"[AUTH] Achtergrond verificatie OK — plan: {self._auth_plan}")
            elif error == "device_mismatch":
                # License activated on another device
                print("[AUTH] Licentie actief op ander apparaat — uitgelogd")
                self._auth_plan = ""
                QTimer.singleShot(0, lambda: self._on_device_mismatch())
            elif error and error != "offline":
                # License expired or invalid — deactivate
                print(f"[AUTH] Licentie verlopen/ongeldig: {error}")
                self._auth_plan = ""
                auth.clear_session()
                # Update banner on main thread (no full rebuild → no layout shift)
                QTimer.singleShot(0, self._update_idle_license_banner)
        except Exception as e:
            print(f"[AUTH] Achtergrond verificatie fout: {e}")

    def _on_device_mismatch(self):
        """Handle license activated on another device."""
        self._update_idle_license_banner()
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Licentie",
            "Deze licentie is geactiveerd op een ander apparaat.\n\n"
            "Elke licentie kan maar op 1 apparaat tegelijk actief zijn.\n"
            "Log opnieuw in om dit apparaat te activeren.")

    def _show_login(self, error=""):
        """Show the login page."""
        self._login_error_label.setText(error)
        if error:
            self._login_error_label.setStyleSheet(
                f"color: {config.COLOR_DANGER}; font-size: 14px; background: transparent;"
            )
        else:
            self._login_error_label.setText("")
        self._login_name_input.clear()
        self._login_key_input.clear()
        self._login_btn.setEnabled(True)
        self._login_btn.setText(t("activate"))
        self.stack.setCurrentIndex(self.pages["login"])

    def _on_login_clicked(self):
        """Handle license activation button click."""
        name = self._login_name_input.text().strip()
        key = self._login_key_input.text().strip()

        if not name or not key:
            self._login_error_label.setText(t("fill_name_and_code"))
            self._login_error_label.setStyleSheet(
                f"color: {config.COLOR_DANGER}; font-size: 14px; background: transparent;"
            )
            return

        self._login_btn.setEnabled(False)
        self._login_btn.setText(t("activating"))
        self._login_error_label.setText("")
        QApplication.processEvents()

        # Run license check synchronously — 15s timeout
        try:
            success, user, error = auth.login(name, key)
        except Exception as e:
            success, user, error = False, None, str(e)
        self._on_login_result(success, user, error)

    def _on_login_result(self, success, user, error):
        """Handle login result on the main thread."""
        if success:
            self._auth_plan = user.get("plan", "starter")
            plan_display = "Professional" if self._auth_plan == "professional" else "Starter"
            print(f"[AUTH] Ingelogd als {user.get('email')} — {plan_display}")
            self._load_active_event()
            self._rebuild_idle_page()  # Remove license banner
            self._go_idle()
        else:
            self._login_btn.setEnabled(True)
            self._login_btn.setText(t("activate"))
            self._login_error_label.setText(error)
            self._login_error_label.setStyleSheet(
                f"color: {config.COLOR_DANGER}; font-size: 14px; background: transparent;"
            )

    def _on_logout(self):
        """Logout and return to login screen."""
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(t("logout"))
        msg.setText(t("confirm_logout"))
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText(t("logout"))
        msg.button(QMessageBox.No).setText(t("cancel"))
        msg.setStyleSheet(
            f"QMessageBox {{ background: {config.COLOR_BG}; }}"
            f"QLabel {{ color: {config.COLOR_TEXT}; font-size: 14px; font-family: 'DM Sans'; }}"
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 10px 30px; font-size: 14px; "
            f"font-family: 'DM Sans'; font-weight: bold; min-height: 40px; min-width: 100px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
        )
        if msg.exec_() != QMessageBox.Yes:
            return
        auth.clear_session()
        self._auth_plan = ""
        print("[AUTH] Uitgelogd — watermerk geactiveerd")
        # Update settings UI immediately (account card)
        self._update_account_info()
        # Rebuild idle page with license banner
        self._rebuild_idle_page()
        # Go to idle (shows banner + watermerk actief)
        self._go_idle()

    def _is_pro_feature(self, feature):
        """Verhuur-versie: alle features altijd beschikbaar."""
        return True

    def _is_logged_in(self):
        """Check if user is logged in with a valid plan."""
        return bool(self._auth_plan)

    def _build_login_page(self):
        """Build the login / authentication page."""
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(60, 60, 60, 60)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignCenter)

        # Container card
        card = QWidget()
        card.setMaximumWidth(500)
        card.setMinimumWidth(300)
        card.setStyleSheet(
            f"background: {config.COLOR_INPUT_BG}; border-radius: 20px;"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(30, 40, 30, 40)
        card_lay.setSpacing(18)
        card_lay.setAlignment(Qt.AlignCenter)

        # Logo — camera icon (no text, no whitespace)
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("background: transparent;")
        _logo_path = os.path.join(config.BUNDLE_DIR, "bootharoo-camera.png")
        if not os.path.exists(_logo_path):
            _logo_path = os.path.join(config.BASE_DIR, "bootharoo-camera.png")
        _logo_pix = QPixmap(_logo_path)
        if not _logo_pix.isNull():
            logo_label.setPixmap(_logo_pix.scaledToHeight(160, Qt.SmoothTransformation))
        else:
            logo_label.setText("📷")
            logo_label.setFont(QFont("DM Sans", 48))
        card_lay.addWidget(logo_label)

        subtitle = QLabel(t("license_title"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 14))
        subtitle.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        card_lay.addWidget(subtitle)

        card_lay.addSpacing(10)

        # Name input
        name_label = QLabel(t("license_name"))
        name_label.setFont(QFont("DM Sans", 13))
        name_label.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        card_lay.addWidget(name_label)

        self._login_name_input = QLineEdit()
        self._login_name_input.setPlaceholderText(t("license_name_placeholder"))
        self._login_name_input.setFont(QFont("DM Sans", 16))
        self._login_name_input.setMinimumHeight(52)
        self._login_name_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 10px; padding: 10px 16px; color: {config.COLOR_TEXT}; font-size: 16px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        card_lay.addWidget(self._login_name_input)

        # License key input
        key_label = QLabel(t("license_code"))
        key_label.setFont(QFont("DM Sans", 13))
        key_label.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        card_lay.addWidget(key_label)

        self._login_key_input = QLineEdit()
        self._login_key_input.setPlaceholderText(t("license_code_placeholder"))
        self._login_key_input.setFont(QFont("DM Sans", 16))
        self._login_key_input.setMinimumHeight(52)
        self._login_key_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 10px; padding: 10px 16px; color: {config.COLOR_TEXT}; font-size: 16px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._login_key_input.returnPressed.connect(self._on_login_clicked)
        card_lay.addWidget(self._login_key_input)

        card_lay.addSpacing(6)

        # Error label
        self._login_error_label = QLabel("")
        self._login_error_label.setAlignment(Qt.AlignCenter)
        self._login_error_label.setFont(QFont("DM Sans", 13))
        self._login_error_label.setWordWrap(True)
        self._login_error_label.setStyleSheet(
            f"color: {config.COLOR_DANGER}; background: transparent;"
        )
        card_lay.addWidget(self._login_error_label)

        # Activate button
        self._login_btn = QPushButton(t("activate"))
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._login_btn.setMinimumHeight(58)
        self._login_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 12px; padding: 14px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
            f"QPushButton:disabled {{ background: {config.COLOR_BORDER}; color: {config.COLOR_TEXT_DIM}; }}"
        )
        self._login_btn.clicked.connect(self._on_login_clicked)
        card_lay.addWidget(self._login_btn)

        # Back button
        back_btn = QPushButton("\u2190  Terug")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setFont(QFont("DM Sans", 13))
        back_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: none; padding: 8px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_TEXT}; }}"
        )
        back_btn.clicked.connect(lambda: self._go_idle())
        card_lay.addWidget(back_btn)

        # "Nog geen licentie?" link
        no_license_btn = QPushButton(t("no_license_link"))
        no_license_btn.setCursor(Qt.PointingHandCursor)
        no_license_btn.setFont(QFont("DM Sans", 10))
        no_license_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_PRIMARY}; "
            f"border: none; padding: 6px; font-size: 10px; text-decoration: underline; }}"
            f"QPushButton:hover {{ color: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        no_license_btn.clicked.connect(lambda: __import__('PyQt5.QtGui', fromlist=['QDesktopServices']).QDesktopServices.openUrl(
            __import__('PyQt5.QtCore', fromlist=['QUrl']).QUrl("https://bootharoo.com")
        ))
        card_lay.addWidget(no_license_btn)

        lay.addWidget(card)

        # Version info — dynamisch vanuit config.VERSION zodat hij altijd actueel is
        version_label = QLabel(t("version", version=config.VERSION))
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setFont(QFont("DM Sans", 11))
        version_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        lay.addSpacing(20)
        lay.addWidget(version_label)

        self.stack.addWidget(page)

    def _make_title(self, text, size=64):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFont(QFont("DM Sans", size, QFont.Bold))
        return lbl

    def _make_subtitle(self, text, size=24):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFont(QFont("DM Sans", size))
        lbl.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        return lbl

    def _make_button(self, text, callback, obj_name=None):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(callback)
        btn.setFont(QFont("DM Sans", 28, QFont.Bold))
        if obj_name:
            btn.setObjectName(obj_name)
        return btn

    def _make_touch_spin(self, min_val, max_val, default, suffix="", on_change=None, step=1):
        """Create a touchscreen-friendly spinner with large - and + buttons.

        Returns (container_widget, value_label) so the value can be read/set.
        """
        container = QWidget()
        container.setFixedHeight(50)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; font-size: 22px; font-weight: bold; "
            f"min-width: 50px; min-height: 46px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )

        minus_btn = QPushButton("\u2212")
        minus_btn.setCursor(Qt.PointingHandCursor)
        minus_btn.setStyleSheet(btn_style)
        lay.addWidget(minus_btn)

        val_label = QLabel(f"{default}{suffix}")
        val_label.setAlignment(Qt.AlignCenter)
        val_label.setFont(QFont("DM Sans", 14, QFont.Bold))
        val_label.setMinimumWidth(90)
        val_label.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: {config.COLOR_INPUT_BG}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; "
            f"padding: 2px 6px;"
        )
        lay.addWidget(val_label)

        plus_btn = QPushButton("+")
        plus_btn.setCursor(Qt.PointingHandCursor)
        plus_btn.setStyleSheet(btn_style)
        lay.addWidget(plus_btn)

        # Store state on the container
        container._value = default
        container._min = min_val
        container._max = max_val
        container._suffix = suffix
        container._val_label = val_label

        def _change(delta):
            new_val = max(min_val, min(max_val, container._value + delta))
            if new_val != container._value:
                container._value = new_val
                val_label.setText(f"{new_val}{suffix}")
                if on_change:
                    on_change(new_val)

        minus_btn.clicked.connect(lambda: _change(-step))
        plus_btn.clicked.connect(lambda: _change(step))

        return container

    @staticmethod
    def _touch_spin_set(container, value):
        """Set value on a touch spin widget."""
        container._value = value
        container._val_label.setText(f"{value}{container._suffix}")

    def _make_step_spin(self, steps, default_idx=0, suffix="", on_change=None):
        """Touch-spinner die door een vaste lijst stappen heen springt.

        Voor 'aantal vouchers': [10, 50, 100, 200, 500, 1000, 5000, 10000].
        Container heeft .value() (huidige int) en .step_idx (lijstindex).
        """
        container = QWidget()
        container.setFixedHeight(50)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; font-size: 22px; font-weight: bold; "
            f"min-width: 50px; min-height: 46px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        minus_btn = QPushButton("−")
        minus_btn.setCursor(Qt.PointingHandCursor)
        minus_btn.setStyleSheet(btn_style)
        lay.addWidget(minus_btn)

        val_label = QLabel(f"{steps[default_idx]}{suffix}")
        val_label.setAlignment(Qt.AlignCenter)
        val_label.setFont(QFont("DM Sans", 14, QFont.Bold))
        val_label.setMinimumWidth(110)
        val_label.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: {config.COLOR_INPUT_BG}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 2px 6px;"
        )
        lay.addWidget(val_label)

        plus_btn = QPushButton("+")
        plus_btn.setCursor(Qt.PointingHandCursor)
        plus_btn.setStyleSheet(btn_style)
        lay.addWidget(plus_btn)

        container._steps = list(steps)
        container.step_idx = default_idx
        container._suffix = suffix
        container._val_label = val_label

        def _value():
            return container._steps[container.step_idx]
        container.value = _value

        def _change(delta):
            new_idx = max(0, min(len(container._steps) - 1, container.step_idx + delta))
            if new_idx != container.step_idx:
                container.step_idx = new_idx
                val_label.setText(f"{container._steps[new_idx]}{suffix}")
                if on_change:
                    on_change(container._steps[new_idx])

        minus_btn.clicked.connect(lambda: _change(-1))
        plus_btn.clicked.connect(lambda: _change(1))
        return container

    # --- EVENT DASHBOARD ---
    # ── Event persistence ──────────────────────

    def _load_active_event(self):
        """Load last active event and printer name from settings.json on startup."""
        import json
        # Load saved printer name
        if os.path.isfile(config.SETTINGS_FILE):
            try:
                with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                saved_printer = settings.get("printer_name", "")
                if saved_printer:
                    config.PRINTER_NAME = saved_printer
                    print(f"[PRINTER] Hersteld: {saved_printer}")
                    # Direct label updaten — _printer_name_label is op dit moment
                    # al gebouwd in __init__ met de oude hardcoded default.
                    if hasattr(self, '_printer_name_label'):
                        try:
                            self._printer_name_label.setText(saved_printer)
                        except Exception:
                            pass
            except Exception:
                pass

        # Try to load last active event
        if os.path.isfile(config.SETTINGS_FILE):
            try:
                with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                event_id = settings.get("active_event_id", "")
                if event_id:
                    path = os.path.join(config.EVENTS_DIR, f"{event_id}.json")
                    if os.path.isfile(path):
                        self.active_event = Event.load(path)
                        print(f"[EVENT] Hersteld: {self.active_event.name}")
            except Exception as ex:
                print(f"[EVENT] Fout bij herstellen: {ex}")

        # If no active event was loaded, try first available or create default
        if not self.active_event:
            existing = list_events(config.EVENTS_DIR)
            if existing:
                self.active_event = existing[0]
                print(f"[EVENT] Eerste event geladen: {self.active_event.name}")
            else:
                self.active_event = Event.create_new(t("default_text"))
                self.active_event.save(config.EVENTS_DIR)
                print(f"[EVENT] Nieuw standaard event aangemaakt")
            self._save_active_event_id()

        # First-run migratie: bij eerste opstart na v2.27 upgrade kopiëer de
        # booth-wide instellingen van het actieve event naar booth_settings.json.
        # Daarna is booth_settings.json de gedeelde bron voor alle events.
        # Idempotent — doet niks als booth_settings.json al bestaat.
        if self.active_event:
            try:
                from event_model import migrate_from_event
                migrate_from_event(self.active_event)
            except Exception as ex:
                print(f"[EVENT] Migratie call mislukt: {ex}")

    def _save_event_debounced(self):
        """Schedule a debounced event save (avoids writing JSON on every slider tick)."""
        if self._event_save_timer.isActive():
            self._event_save_timer.stop()
        self._event_save_timer.start()

    def _do_debounced_event_save(self):
        """Actually save the event (called by debounce timer)."""
        if self.active_event:
            self.active_event.save(config.EVENTS_DIR)

    def _save_active_event_id(self):
        """Persist active event ID to settings.json."""
        self._save_app_setting("active_event_id",
                               self.active_event.id if self.active_event else "")

    def _save_app_setting(self, key, value):
        """Save a single key/value to settings.json."""
        import json
        settings = {}
        if os.path.isfile(config.SETTINGS_FILE):
            try:
                with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception:
                pass
        settings[key] = value
        try:
            os.makedirs(os.path.dirname(config.SETTINGS_FILE), exist_ok=True)
            with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        self._settings_cache = settings  # Update cache after write

    def _load_app_setting(self, key, default=None):
        """Load a single key from settings.json (cached in memory)."""
        import json
        # Use cached settings to avoid repeated disk reads
        cache = getattr(self, '_settings_cache', None)
        if cache is None:
            if os.path.isfile(config.SETTINGS_FILE):
                try:
                    with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                        cache = json.load(f)
                except Exception:
                    cache = {}
            else:
                cache = {}
            self._settings_cache = cache
        return cache.get(key, default)

    @property
    def effective_print_copies(self):
        """Get auto-print copies from active event or global config."""
        if self.active_event:
            return max(self.active_event.auto_print_copies, self.active_event.print_copies)
        return config.PRINT_COPIES

    @property
    def effective_print_enabled(self):
        """Get print enabled from active event or True."""
        if self.active_event:
            return self.active_event.print_enabled
        return True

    @property
    def backend_brand(self) -> str:
        """'hippe' (default, DNP QW410) of 'huren' (Verhuurophalen,
        HiTi P525L). Booth-wide instelling via Geavanceerd.

        Zonder actief event (welcome-page) geldt de booth-wide waarde
        uit booth_settings.json (cache geladen bij start) — anders zou
        de switch terugvallen op 'hippe' tot er een event gekoppeld is."""
        ev = self.active_event
        if ev:
            brand = getattr(ev, 'backend_brand', 'hippe')
        else:
            brand = getattr(self, '_booth_brand_cache', 'hippe')
        return brand if brand in ('hippe', 'huren') else 'hippe'

    @staticmethod
    def _new_session_id() -> str:
        """Uniek sessie-id: timestamp + random suffix.

        Het oude formaat (alleen %Y%m%d_%H%M%S) botste in de gedeelde
        R2-bucket wanneer twee booths in dezelfde seconde een sessie
        startten — gasten kregen dan foto's van iemand anders te zien.
        De 8 hex-tekens (4 miljard combinaties) sluiten dat uit.
        """
        import uuid
        return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    @property
    def effective_email_enabled(self):
        """Get email enabled from active event or global config."""
        if self.active_event:
            return self.active_event.email_enabled
        return getattr(config, 'EMAIL_ENABLED', False)

    # --- IDLE ---
    def _load_idle_background(self):
        """Load idle screen background from active event or settings.json."""
        mode = "default"
        if self.active_event:
            mode = getattr(self.active_event, 'idle_screen_mode', 'default')

        if mode == "default":
            # Auto-detect screen resolution and pick matching default
            default_path, _, _ = self._get_default_idle_path()
            if default_path and os.path.isfile(default_path):
                return default_path
            return ""

        # Custom mode: use event's custom background
        if self.active_event and self.active_event.idle_background:
            if os.path.isfile(self.active_event.idle_background):
                return self.active_event.idle_background
        # No custom background set — fall back to default
        default_path, _, _ = self._get_default_idle_path()
        if default_path and os.path.isfile(default_path):
            return default_path
        return ""
        # Legacy fallback (unused)
        import json
        if not os.path.isfile(config.SETTINGS_FILE):
            return ""
        try:
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
            return settings.get("idle_background", "")
        except Exception:
            return ""

    def _build_welcome_page(self):
        """Welcome / setup-pagina — getoond wanneer geen event gekoppeld is.

        Strakke Clixibo-stijl layout. Toont:
          - Grote titel
          - Taalkeuze (6 vlag-knoppen)
          - EÉN action-card: wifi-instellen OF QR-scan (afhankelijk van internet)
          - Slotje rechts-onder voor PIN → settings
        """
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        self._welcome_page = page

        outer = QVBoxLayout(page)
        outer.setContentsMargins(80, 60, 80, 60)
        outer.setSpacing(28)

        outer.addStretch(1)

        # ── Hero titel ─────────────────────────────────────────────────
        title = QLabel(t("welcome_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 56, QFont.Bold))
        title.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: transparent; "
            f"letter-spacing: -1px;"
        )
        title.setWordWrap(True)
        self._welcome_title_label = title
        outer.addWidget(title)

        # ── Subtle subtitel ────────────────────────────────────────────
        subtitle = QLabel(t("welcome_lang_label"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 16))
        subtitle.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; background: transparent;"
        )
        self._welcome_lang_label = subtitle
        outer.addWidget(subtitle)

        # ── Taalkeuze (pill-style knoppen) ─────────────────────────────
        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        lang_row.addStretch()
        self._welcome_lang_btns = {}
        for code, flag, name in [
            ("nl", "🇳🇱", "NL"),
            ("en", "🇬🇧", "EN"),
            ("de", "🇩🇪", "DE"),
            ("fr", "🇫🇷", "FR"),
            ("es", "🇪🇸", "ES"),
            ("it", "🇮🇹", "IT"),
        ]:
            btn = QPushButton(f"{flag}  {name}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", 13, QFont.Bold))
            btn.setFixedHeight(50)
            btn.setMinimumWidth(96)
            btn.setStyleSheet(self._welcome_lang_btn_style(active=False))
            btn.clicked.connect(lambda _c, lc=code: self._on_welcome_lang_pick(lc))
            self._welcome_lang_btns[code] = btn
            lang_row.addWidget(btn)
        lang_row.addStretch()
        outer.addLayout(lang_row)
        outer.addSpacing(12)

        # ── Action stack: 3 cards ──────────────────────────────────────
        # State machine:
        #   'checking' (page 2): 3 pings doen voor commit naar online/offline
        #   'online'   (page 1, qr-card): 5x ping fail op rij → offline
        #   'offline'  (page 0, wifi-card): 1x ping success → online
        self._welcome_action_container = QWidget()
        self._welcome_action_container.setStyleSheet("background: transparent;")
        action_lay = QHBoxLayout(self._welcome_action_container)
        action_lay.setContentsMargins(0, 0, 0, 0)
        action_lay.addStretch()
        self._welcome_action_stack = QStackedWidget()
        self._welcome_action_stack.setStyleSheet("background: transparent;")
        self._welcome_action_stack.setMaximumWidth(640)
        self._welcome_action_stack.addWidget(self._build_welcome_wifi_card())     # 0
        self._welcome_action_stack.addWidget(self._build_welcome_qr_card())       # 1
        self._welcome_action_stack.addWidget(self._build_welcome_checking_card()) # 2
        # Start in checking state
        self._welcome_action_stack.setCurrentIndex(2)
        self._has_internet = None  # nog onbekend
        self._welcome_state = 'checking'
        self._welcome_check_results = []          # eerste 3 ping resultaten
        self._welcome_consecutive_failures = 0    # alleen relevant in 'online' state
        action_lay.addWidget(self._welcome_action_stack)
        action_lay.addStretch()
        outer.addWidget(self._welcome_action_container)

        outer.addStretch(2)

        # ── Printer-status banner (alleen zichtbaar bij fout/warning) ─
        self._welcome_printer_banner = QLabel(page)
        self._welcome_printer_banner.setAlignment(Qt.AlignCenter)
        self._welcome_printer_banner.setFont(QFont("DM Sans", 14, QFont.Bold))
        self._welcome_printer_banner.setWordWrap(True)
        self._welcome_printer_banner.setStyleSheet(
            "QLabel { background: #b01e1e; color: white; padding: 14px 24px; }"
        )
        self._welcome_printer_banner.hide()
        outer.addWidget(self._welcome_printer_banner)

        # ── Slotje rechts-onder ───────────────────────────────────────
        # Maakt direct PIN-prompt en opent settings (geen info-dialog).
        self._welcome_lock_btn = QPushButton("🔒", page)
        self._welcome_lock_btn.setFixedSize(60, 60)
        self._welcome_lock_btn.setCursor(Qt.PointingHandCursor)
        self._welcome_lock_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0,0,0,0.06); color: {config.COLOR_TEXT_DIM}; "
            f"border: none; border-radius: 30px; font-size: 26px; }}"
            f"QPushButton:hover {{ background: rgba(0,0,0,0.12); color: {config.COLOR_TEXT}; }}"
        )
        self._welcome_lock_btn.clicked.connect(self._go_settings)

        # ── Serienummer links-onder (zoals ingesteld in Geavanceerd) ──
        self._welcome_serial_label = QLabel("", page)
        self._welcome_serial_label.setFont(QFont("DM Sans", 14))
        self._welcome_serial_label.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; background: transparent;"
        )
        self._welcome_serial_label.show()

        # ── Periodic wifi/internet check ─────────────────────────────
        # Interval 1.5 sec — 3 pings in ~5 sec, snelle commit naar juiste card.
        self._welcome_wifi_timer = QTimer(self)
        self._welcome_wifi_timer.setInterval(1500)
        self._welcome_wifi_timer.timeout.connect(self._welcome_check_connectivity)
        self._welcome_consecutive_failures = 0
        self._welcome_consecutive_successes = 0

        # Reposition lock button after page is shown
        page.resizeEvent = self._welcome_resize_event

        self.stack.addWidget(page)
        return page

    def _welcome_resize_event(self, event):
        """Houd het slotje rechts-onder + serienummer links-onder bij resize."""
        if hasattr(self, '_welcome_lock_btn'):
            page = self._welcome_page
            self._welcome_lock_btn.move(page.width() - 80, page.height() - 80)
            self._welcome_lock_btn.raise_()
        self._refresh_welcome_serial()

    def _refresh_welcome_serial(self):
        """Zet het serienummer (uit Geavanceerd) links-onder op de welcome-
        pagina en positioneer het."""
        if not hasattr(self, '_welcome_serial_label'):
            return
        self._welcome_serial_label.setText((self.serial_number or "").strip())
        self._welcome_serial_label.adjustSize()
        page = self._welcome_page
        self._welcome_serial_label.move(24, page.height() - 44)
        self._welcome_serial_label.raise_()

    def _build_welcome_wifi_card(self):
        """Card die getoond wordt bij geen internetverbinding."""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_INPUT_BG}; border-radius: 24px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(48, 40, 48, 40)
        lay.setSpacing(18)

        icon = QLabel("📡")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFont(QFont("DM Sans", 56))
        icon.setStyleSheet("background: transparent;")
        lay.addWidget(icon)

        h = QLabel(t("welcome_wifi_disconnected"))
        h.setAlignment(Qt.AlignCenter)
        h.setFont(QFont("DM Sans", 24, QFont.Bold))
        h.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        self._welcome_wifi_h = h
        lay.addWidget(h)

        hint = QLabel(t("welcome_wifi_hint"))
        hint.setAlignment(Qt.AlignCenter)
        hint.setFont(QFont("DM Sans", 14))
        hint.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        hint.setWordWrap(True)
        self._welcome_wifi_hint = hint
        lay.addWidget(hint)
        lay.addSpacing(8)

        btn = QPushButton(t("welcome_wifi_open_btn"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        btn.setMinimumHeight(64)
        btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 16px; padding: 14px 32px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        btn.clicked.connect(self._on_welcome_open_wifi)
        self._welcome_wifi_btn = btn
        lay.addWidget(btn)
        return card

    def _build_welcome_qr_card(self):
        """Card die getoond wordt bij actieve internetverbinding."""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_INPUT_BG}; border-radius: 24px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(48, 40, 48, 40)
        lay.setSpacing(18)

        icon = QLabel("📷")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFont(QFont("DM Sans", 56))
        icon.setStyleSheet("background: transparent;")
        lay.addWidget(icon)

        h = QLabel(t("welcome_couple_title"))
        h.setAlignment(Qt.AlignCenter)
        h.setFont(QFont("DM Sans", 24, QFont.Bold))
        h.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        h.setWordWrap(True)
        self._welcome_qr_title = h
        lay.addWidget(h)

        hint = QLabel(t("welcome_couple_hint"))
        hint.setAlignment(Qt.AlignCenter)
        hint.setFont(QFont("DM Sans", 14))
        hint.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        hint.setWordWrap(True)
        self._welcome_qr_hint = hint
        lay.addWidget(hint)
        lay.addSpacing(8)

        btn = QPushButton(t("welcome_scan_btn"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        btn.setMinimumHeight(64)
        btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 16px; padding: 14px 32px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        btn.clicked.connect(self._on_welcome_scan_qr)
        self._welcome_scan_btn = btn
        lay.addWidget(btn)
        return card

    def _build_welcome_checking_card(self):
        """'Internet controleren...' card met geanimeerde spinner-dots.

        Getoond tijdens eerste 3 ping-checks na openen welcome page.
        """
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_INPUT_BG}; border-radius: 24px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(48, 40, 48, 40)
        lay.setSpacing(14)

        # Geanimeerde spinner via roterend emoji
        self._welcome_spinner_icon = QLabel("⏳")
        self._welcome_spinner_icon.setAlignment(Qt.AlignCenter)
        self._welcome_spinner_icon.setFont(QFont("DM Sans", 56))
        self._welcome_spinner_icon.setStyleSheet("background: transparent;")
        lay.addWidget(self._welcome_spinner_icon)

        h = QLabel(t("welcome_internet_checking"))
        h.setAlignment(Qt.AlignCenter)
        h.setFont(QFont("DM Sans", 22, QFont.Bold))
        h.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        h.setWordWrap(True)
        self._welcome_checking_label = h
        lay.addWidget(h)

        sub = QLabel("Een moment geduld...")
        sub.setAlignment(Qt.AlignCenter)
        sub.setFont(QFont("DM Sans", 13))
        sub.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # Spinner-animatie timer
        self._welcome_spinner_timer = QTimer(self)
        self._welcome_spinner_timer.setInterval(400)
        self._welcome_spinner_states = ["⏳", "⌛", "⏳", "⌛"]
        self._welcome_spinner_idx = 0
        def _tick():
            self._welcome_spinner_idx = (self._welcome_spinner_idx + 1) % len(self._welcome_spinner_states)
            self._welcome_spinner_icon.setText(self._welcome_spinner_states[self._welcome_spinner_idx])
        self._welcome_spinner_timer.timeout.connect(_tick)

        return card

    def _build_qr_scan_page(self):
        """Fullscreen QR-scan page met live webcam-feed + Annuleer-knop.

        Vervangt de modal CoupleEventDialog voor de welcome-flow omdat
        dialogs niet zichtbaar zijn in fullscreen-mode met TopMost flag.
        """
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        self._scan_qr_page = page

        outer = QVBoxLayout(page)
        outer.setContentsMargins(30, 30, 30, 30)
        outer.setSpacing(20)

        # ── Top bar: cancel-knop linksboven + titel ─────────────────
        top_bar = QHBoxLayout()
        cancel_btn = QPushButton("←  Annuleer")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        cancel_btn.setMinimumHeight(48)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0,0,0,0.06); color: {config.COLOR_TEXT}; "
            f"border: none; border-radius: 12px; padding: 8px 22px; }}"
            f"QPushButton:hover {{ background: rgba(0,0,0,0.12); }}"
        )
        cancel_btn.clicked.connect(self._on_scan_qr_cancel)
        top_bar.addWidget(cancel_btn)
        top_bar.addStretch()
        outer.addLayout(top_bar)

        # ── Titel + instructie centraal ──────────────────────────────
        title = QLabel("Scan de QR-code van je booking")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 36, QFont.Bold))
        title.setStyleSheet(
            f"color: {config.COLOR_TEXT}; background: transparent; letter-spacing: -0.5px;"
        )
        title.setWordWrap(True)
        outer.addWidget(title)

        instr = QLabel("Houd de QR-code in beeld — de booking wordt automatisch gekoppeld")
        instr.setAlignment(Qt.AlignCenter)
        instr.setFont(QFont("DM Sans", 14))
        instr.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        instr.setWordWrap(True)
        outer.addWidget(instr)

        # ── Live webcam preview ──────────────────────────────────────
        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumSize(640, 480)
        preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview.setStyleSheet(
            f"background: #000; border-radius: 20px;"
        )
        self._scan_qr_preview_label = preview
        outer.addWidget(preview, 1)

        # ── Status onderaan ──────────────────────────────────────────
        status = QLabel("Wacht op camera...")
        status.setAlignment(Qt.AlignCenter)
        status.setFont(QFont("DM Sans", 14))
        status.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        self._scan_qr_status = status
        outer.addWidget(status)

        self.stack.addWidget(page)
        self._scan_qr_worker = None  # QRScanWorker actief tijdens scan
        return page

    def _on_welcome_scan_qr(self):
        """Open de QR-scan page (fullscreen, niet modal)."""
        if not self._has_internet:
            print("[WELCOME] Scan-knop genegeerd — geen internet")
            return
        # Switch naar scan_qr page + start de worker
        self.stack.setCurrentIndex(self.pages["scan_qr"])
        self._scan_qr_start()

    def _scan_qr_start(self):
        """Start de QRScanWorker voor live preview + detectie."""
        from couple_event_dialog import QRScanWorker
        # Webcam-index uit event of fallback 0
        wc_idx = 0
        if self.active_event:
            wc_idx = getattr(self.active_event, 'webcam_index', 0) or 0
        # Eerst eventueel actieve camera vrijgeven zodat QR-worker hem kan pakken
        try:
            if hasattr(self, 'camera') and self.camera and self.camera.is_connected():
                self.camera.stop_live_view()
                self.camera.disconnect()
                self._scan_qr_camera_was_connected = True
            else:
                self._scan_qr_camera_was_connected = False
        except Exception:
            self._scan_qr_camera_was_connected = False

        self._scan_qr_status.setText("Wacht op camera...")
        self._scan_qr_worker = QRScanWorker(device_index=wc_idx, parent=self)
        self._scan_qr_worker.frame_ready.connect(self._scan_qr_on_frame)
        self._scan_qr_worker.qr_detected.connect(self._scan_qr_on_detected)
        self._scan_qr_worker.error.connect(self._scan_qr_on_error)
        self._scan_qr_worker.start()
        print("[SCAN-QR] Worker gestart")

    def _scan_qr_on_frame(self, jpeg_bytes):
        """Toon nieuwe frame in preview."""
        if not hasattr(self, '_scan_qr_preview_label'):
            return
        pix = QPixmap()
        if pix.loadFromData(jpeg_bytes, "JPEG"):
            scaled = pix.scaled(
                self._scan_qr_preview_label.width(),
                self._scan_qr_preview_label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._scan_qr_preview_label.setPixmap(scaled)
            self._scan_qr_status.setText("Houd de QR-code in beeld...")

    def _scan_qr_on_detected(self, token):
        """QR gedetecteerd → stop worker + start coupling-flow."""
        print(f"[SCAN-QR] Token gedetecteerd: {token[:16]}...")
        self._scan_qr_status.setText("QR gevonden — bezig met koppelen...")
        self._scan_qr_stop_worker()
        # Start coupling flow
        from couple_event_dialog import CouplingWorker, CouplingLoadingDialog
        loading = CouplingLoadingDialog(self)
        worker = CouplingWorker(token, self, brand=self.backend_brand)
        self._coupling_worker = worker

        def _on_progress(msg):
            loading.set_status(msg)

        def _on_done(booking_data, design_local_path, err_msg):
            loading.accept()
            self._on_coupling_finished(token, booking_data, design_local_path, err_msg)
            try:
                worker.deleteLater()
            except Exception:
                pass
            self._coupling_worker = None

        worker.progress.connect(_on_progress)
        worker.done.connect(_on_done)
        worker.start()
        loading.exec_()

    def _scan_qr_on_error(self, msg):
        """Webcam-fout → toon error + ga terug naar welcome."""
        print(f"[SCAN-QR] Error: {msg}")
        self._scan_qr_status.setText(f"Fout: {msg}")
        # Geef gebruiker tijd om de error te lezen voordat we terugkeren
        from PyQt5.QtCore import QTimer as _T
        _T.singleShot(3000, self._on_scan_qr_cancel)

    def _on_scan_qr_cancel(self):
        """Annuleer-knop → stop worker + terug naar welcome."""
        self._scan_qr_stop_worker()
        self.stack.setCurrentIndex(self.pages["welcome"])
        self._refresh_welcome_serial()
        print("[SCAN-QR] Geannuleerd, terug naar welcome")

    def _scan_qr_stop_worker(self):
        """Stop de QRScanWorker netjes."""
        if self._scan_qr_worker is not None:
            try:
                self._scan_qr_worker.stop()
            except Exception as e:
                print(f"[SCAN-QR] Worker stop fout: {e}")
            self._scan_qr_worker = None

    def _welcome_lang_btn_style(self, active: bool) -> str:
        """Style voor de language-knoppen op welcome page."""
        if active:
            return (
                f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
                f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: 2px solid {config.COLOR_PRIMARY_HOVER}; "
                f"border-radius: 12px; padding: 8px 16px; }}"
            )
        return (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; "
            f"color: {config.COLOR_TEXT}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 12px; padding: 8px 16px; }}"
            f"QPushButton:hover {{ border-color: {config.COLOR_PRIMARY}; }}"
        )

    def _on_welcome_lang_pick(self, lang_code: str):
        """Wijzig de UI-taal vanuit het welcome scherm."""
        from translations import set_language, save_language
        set_language(lang_code)
        save_language(lang_code)
        print(f"[WELCOME] Taal gewijzigd naar {lang_code}")
        # Refresh teksten op welcome page
        self._welcome_refresh_translations()
        # Update active-state op de knoppen
        for code, btn in self._welcome_lang_btns.items():
            btn.setStyleSheet(self._welcome_lang_btn_style(active=(code == lang_code)))

    def _welcome_refresh_translations(self):
        """Vertaalde labels op welcome page opnieuw ophalen na taal-wissel."""
        if hasattr(self, '_welcome_title_label'):
            self._welcome_title_label.setText(t("welcome_title"))
        if hasattr(self, '_welcome_lang_label'):
            self._welcome_lang_label.setText(t("welcome_lang_label"))
        # Wifi-card teksten
        if hasattr(self, '_welcome_wifi_h'):
            self._welcome_wifi_h.setText(t("welcome_wifi_disconnected"))
        if hasattr(self, '_welcome_wifi_btn'):
            self._welcome_wifi_btn.setText(t("welcome_wifi_open_btn"))
        if hasattr(self, '_welcome_wifi_hint'):
            self._welcome_wifi_hint.setText(t("welcome_wifi_hint"))
        # QR-card teksten
        if hasattr(self, '_welcome_qr_title'):
            self._welcome_qr_title.setText(t("welcome_couple_title"))
        if hasattr(self, '_welcome_qr_hint'):
            self._welcome_qr_hint.setText(t("welcome_couple_hint"))
        if hasattr(self, '_welcome_scan_btn'):
            self._welcome_scan_btn.setText(t("welcome_scan_btn"))
        # Verifieer connectivity-status na taal-wissel
        self._welcome_check_connectivity()

    def _on_welcome_open_wifi(self):
        """Open Windows WiFi flyout (rechter-onder netwerk-popup)."""
        try:
            os.startfile("ms-availablenetworks:")
            print("[WELCOME] WiFi flyout geopend")
        except OSError as e:
            print(f"[WELCOME] Kon WiFi flyout niet openen: {e}")
            # Fallback: Settings → Network → WiFi
            try:
                os.startfile("ms-settings:network-wifi")
            except OSError as e2:
                print(f"[WELCOME] Settings page ook niet bereikbaar: {e2}")

    def _on_welcome_scan_qr(self):
        """Open de QR-scan dialog voor event-koppeling."""
        # Vereist internet — disable als offline
        if not self._has_internet:
            print("[WELCOME] Scan-knop genegeerd — geen internet")
            return
        self._show_couple_event_dialog()
        # Na coupling: _on_coupling_finished checkt active_event en routeert
        # via _maybe_route_after_coupling naar de juiste idle-modus.

    def _welcome_check_connectivity(self):
        """Check internet via Windows' eigen ping-command. Simpelst en
        meest foolproof: ICMP packet naar 8.8.8.8 met 1 sec timeout.

        Waarom NIET socket of urllib:
        - Sommige firewalls blokkeren outbound TCP naar specifieke poorten
        - urllib DNS-lookup kan minuten hangen op Windows
        - Python socket-module kan door Windows Firewall geblokkeerd worden

        Waarom WEL Windows ping:
        - Built-in command, eigen firewall-exemptie
        - ICMP wordt zelden geblokkeerd
        - Exit code 0 = success, anders fail
        - CREATE_NO_WINDOW voorkomt zwart cmd-venster bij elke check
        """
        import threading
        def _bg():
            import subprocess
            CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

            # ── STAP 1: Check WiFi adapter state via netsh ────────────
            # Voorkomt false-positive bij wifi=uit maar wel ethernet/hotspot
            # actief. Als wifi-adapter expliciet "Disconnected" is, dan is
            # de gast OFFLINE vanuit hun perspectief — ongeacht of een
            # andere adapter de ping kan beantwoorden.
            wifi_connected = None  # None=onbekend, True/False=expliciet
            try:
                result = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True, text=True, timeout=2,
                    creationflags=CREATE_NO_WINDOW
                )
                # Zoek "State : connected" of "State : disconnected" lijn
                for line in result.stdout.splitlines():
                    stripped = line.strip().lower()
                    # NL: "Staat" / EN: "State" — beide kunnen voorkomen
                    if stripped.startswith(("state", "staat")) and ":" in stripped:
                        value = stripped.split(":", 1)[1].strip()
                        wifi_connected = ("connect" in value
                                          and "disconnect" not in value)
                        break
            except Exception as e:
                print(f"[WELCOME] netsh wlan check skip: {type(e).__name__}({e})")

            # ── STAP 2: ping check ────────────────────────────────────
            ping_ok = False
            stderr_msg = ""
            used_ip = ""
            for ip in ("8.8.8.8", "1.1.1.1"):
                try:
                    result = subprocess.run(
                        ["ping", ip, "-n", "1", "-w", "1000"],
                        capture_output=True, text=True, timeout=2,
                        creationflags=CREATE_NO_WINDOW
                    )
                    if result.returncode == 0:
                        ping_ok = True
                        used_ip = ip
                        break
                    else:
                        stderr_msg = f"{ip}: exit={result.returncode}"
                except Exception as e:
                    stderr_msg = f"{ip}: {type(e).__name__}({e})"
                    continue

            # ── STAP 3: combineer signalen ────────────────────────────
            # Wifi expliciet uit → offline (override ping)
            # Wifi aan + ping OK → online
            # Wifi aan + ping fout → offline
            # Wifi onbekend (geen wifi-adapter / netsh fout) → val terug op ping
            if wifi_connected is False:
                online = False
                reason = "wifi-adapter is uit"
            elif wifi_connected is True and ping_ok:
                online = True
                reason = f"wifi aan + ping via {used_ip}"
            elif wifi_connected is True and not ping_ok:
                online = False
                reason = f"wifi aan maar geen internet ({stderr_msg})"
            else:
                # wifi onbekend → ping-only mode
                online = ping_ok
                reason = (f"ping via {used_ip}" if ping_ok
                          else f"ping fout: {stderr_msg}")

            self._welcome_connectivity_signal.emit(online)
            print(f"[WELCOME] Conn check: {'ONLINE' if online else 'OFFLINE'} "
                  f"(wifi={wifi_connected}, {reason})")
        threading.Thread(target=_bg, daemon=True).start()

    def _welcome_apply_connectivity(self, online: bool):
        """State machine voor wifi/qr card switching.

        States:
          - 'checking': eerste 3 pings verzamelen, dan commit
          - 'online' (qr-card):  5x ping-fail op rij → switch naar 'offline'
          - 'offline' (wifi-card): 1x ping-success → meteen 'online'
        """
        if not hasattr(self, '_welcome_action_stack'):
            return

        state = getattr(self, '_welcome_state', 'checking')

        # ── CHECKING: verzamel eerste resultaten, commit na 3 ─────────
        if state == 'checking':
            self._welcome_check_results.append(online)
            print(f"[WELCOME] Check #{len(self._welcome_check_results)}: "
                  f"{'OK' if online else 'FAIL'}")
            # Direct commit als we 1 succesvolle ping hebben
            if online:
                self._welcome_commit_state(True)
                return
            # 3 mislukkingen op rij → commit naar offline
            if len(self._welcome_check_results) >= 3:
                self._welcome_commit_state(False)
                return
            # Anders: blijf op spinner, wacht op volgende tick
            return

        # ── ONLINE: blijf op qr-card, tellen failures voor switch ────
        if state == 'online':
            if online:
                self._welcome_consecutive_failures = 0
                return  # blijf op qr-card, geen UI-update nodig
            self._welcome_consecutive_failures += 1
            print(f"[WELCOME] Online state — failure {self._welcome_consecutive_failures}/5")
            if self._welcome_consecutive_failures >= 5:
                self._welcome_state = 'offline'
                self._has_internet = False
                self._welcome_action_stack.setCurrentIndex(0)
                self._welcome_consecutive_failures = 0
                print(f"[WELCOME] 5× ping mislukt → switch naar wifi-card")
            return

        # ── OFFLINE: blijf op wifi-card, 2x success op rij nodig ────────
        # Voorkomt dat één toevallige geslaagde ping (bv. NCSI cache) ten
        # onrechte naar qr-card switcht terwijl de gast geen wifi heeft.
        if state == 'offline':
            if online:
                self._welcome_consecutive_successes = getattr(
                    self, '_welcome_consecutive_successes', 0
                ) + 1
                if self._welcome_consecutive_successes >= 2:
                    self._welcome_state = 'online'
                    self._has_internet = True
                    self._welcome_action_stack.setCurrentIndex(1)
                    self._welcome_consecutive_failures = 0
                    self._welcome_consecutive_successes = 0
                    print(f"[WELCOME] 2× ping OK op rij → switch naar qr-card")
                else:
                    print(f"[WELCOME] Offline state — success {self._welcome_consecutive_successes}/2")
            else:
                # Reset success counter bij failure
                self._welcome_consecutive_successes = 0

    def _welcome_commit_state(self, online: bool):
        """Initial-state commit na eerste batch checking-pings."""
        if hasattr(self, '_welcome_spinner_timer'):
            try:
                self._welcome_spinner_timer.stop()
            except Exception:
                pass
        if online:
            self._welcome_state = 'online'
            self._has_internet = True
            self._welcome_action_stack.setCurrentIndex(1)
            self._welcome_consecutive_failures = 0
            print(f"[WELCOME] Initial commit → ONLINE (qr-card)")
        else:
            self._welcome_state = 'offline'
            self._has_internet = False
            self._welcome_action_stack.setCurrentIndex(0)
            print(f"[WELCOME] Initial commit → OFFLINE (wifi-card)")

    def _build_idle_page(self):
        """Build clean idle screen - tap anywhere to start, lock icon for operator."""
        idle_bg = self._load_idle_background()
        if idle_bg and os.path.isfile(idle_bg):
            page = _BgWidget(idle_bg)
        else:
            page = QWidget()

        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Check if payment mode is active (Stripe / SumUp / Voucher)
        ev = self.active_event
        method = getattr(ev, 'payment_method', 'none') if ev else 'none'
        payment_on = ev and (ev.payment_enabled or method == "stripe")
        sumup_on = ev and (getattr(ev, 'sumup_enabled', False) or method == "sumup")
        voucher_on = ev and method == "voucher"
        user, _ = auth.load_session()
        payment_url = user.get("payment_link_url", "") if user else ""

        if sumup_on:
            # ── SumUp mode: no tap, payment handled automatically ──
            lay.addStretch(2)

            # Payment status label (shows on idle screen)
            self._sumup_idle_status = QLabel(t("waiting_for_payment"))
            self._sumup_idle_status.setAlignment(Qt.AlignCenter)
            self._sumup_idle_status.setFont(QFont("DM Sans", 16, QFont.Bold))
            self._sumup_idle_status.setStyleSheet(f"color: {config.COLOR_PRIMARY}; background: transparent;")
            lay.addWidget(self._sumup_idle_status, alignment=Qt.AlignCenter)

            lay.addStretch(3)
            # NO tap overlay — session starts via SumUp payment

        elif payment_on and payment_url:
            # ── Payment idle screen: show QR code ──
            # Use custom payment background if set
            pay_bg = getattr(ev, 'payment_bg_path', '') if ev else ''
            if pay_bg and os.path.isfile(pay_bg):
                # Re-create page with payment background
                page.deleteLater()
                page = _BgWidget(pay_bg)
                lay = QVBoxLayout(page)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(0)

            lay.addStretch(2)

            # QR code
            qr_label = QLabel()
            qr_label.setAlignment(Qt.AlignCenter)
            try:
                import qrcode
                from io import BytesIO
                qr = qrcode.QRCode(version=1, box_size=8, border=2)
                qr.add_data(payment_url)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buf = BytesIO()
                qr_img.save(buf, format="PNG")
                buf.seek(0)
                pixmap = QPixmap()
                pixmap.loadFromData(buf.read())
                scaled = pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qr_label.setPixmap(scaled)
            except Exception as e:
                qr_label.setText("QR")
                print(f"[PAYMENT] QR generatie fout: {e}")
            lay.addWidget(qr_label, alignment=Qt.AlignCenter)

            # Payment text
            pay_text = getattr(ev, 'payment_screen_text', t("payment_scan_default")) if ev else t("payment_scan_default")
            pay_label = QLabel(pay_text)
            pay_label.setAlignment(Qt.AlignCenter)
            pay_label.setFont(QFont("DM Sans", 22, QFont.Bold))
            pay_label.setStyleSheet(f"color: {config.COLOR_PRIMARY}; background: transparent; font-size: 22px;")
            pay_label.setWordWrap(True)
            lay.addWidget(pay_label, alignment=Qt.AlignCenter)

            lay.addStretch(3)
        else:
            # ── Normal idle screen: tap to start ──
            tap_overlay = QPushButton("")
            tap_overlay.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            tap_overlay.setStyleSheet(
                "QPushButton { background: transparent; border: none; min-height: 0; }"
            )
            tap_overlay.setCursor(Qt.PointingHandCursor)
            # Bij voucher-modus: ga eerst naar voucher-input scherm
            if voucher_on:
                tap_overlay.clicked.connect(self._show_voucher_input)
            else:
                tap_overlay.clicked.connect(self._go_select_template)
            lay.addWidget(tap_overlay)

        # License banner (always created, shown/hidden based on login state)
        # Creating it always avoids layout shift when visibility changes later
        license_banner = QWidget()
        license_banner.setMaximumWidth(600)
        license_banner.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #c0392b, stop:1 #e74c3c); border-radius: 14px; }"
        )
        banner_lay = QVBoxLayout(license_banner)
        banner_lay.setContentsMargins(24, 16, 24, 16)
        banner_lay.setSpacing(6)

        warn_label = QLabel("\u26a0\ufe0f  " + t("license_not_linked"))
        warn_label.setFont(QFont("DM Sans", 14, QFont.Bold))
        warn_label.setStyleSheet("color: white; background: transparent; font-size: 14px;")
        warn_label.setAlignment(Qt.AlignCenter)
        banner_lay.addWidget(warn_label)

        desc_label = QLabel(t("license_watermark_msg"))
        desc_label.setFont(QFont("DM Sans", 11))
        desc_label.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent; font-size: 11px;")
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)
        banner_lay.addWidget(desc_label)

        login_btn = QPushButton(t("login"))
        login_btn.setCursor(Qt.PointingHandCursor)
        login_btn.setFixedSize(160, 36)
        login_btn.setStyleSheet(
            "QPushButton { background: white; color: #c0392b; border: none; "
            "border-radius: 8px; padding: 6px 20px; font-size: 12px; font-weight: bold; }"
            "QPushButton:pressed { background: #ddd; }"
        )
        login_btn.clicked.connect(lambda: self._show_login())
        banner_lay.addWidget(login_btn, alignment=Qt.AlignCenter)

        # Center the banner horizontally
        banner_row = QWidget()
        # Transparent: QWidget inherits background-color from the global stylesheet
        # which would paint over the _BgWidget background image. Explicitly set
        # transparent so the idle background image shows through behind the banner.
        banner_row.setStyleSheet("background: transparent;")
        banner_row_lay = QHBoxLayout(banner_row)
        banner_row_lay.setContentsMargins(0, 0, 0, 8)
        banner_row_lay.addStretch()
        banner_row_lay.addWidget(license_banner)
        banner_row_lay.addStretch()
        lay.addWidget(banner_row)

        # Show banner only when not logged in; store ref for dynamic toggling
        # retainSizeWhenHidden: layout doesn't shift when banner appears/disappears
        _sp_banner = banner_row.sizePolicy()
        _sp_banner.setRetainSizeWhenHidden(True)
        banner_row.setSizePolicy(_sp_banner)
        banner_row.setVisible(not self._is_logged_in())
        self._idle_license_banner_row = banner_row

        # Bottom bar: status label + lock icon
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(10, 0, 10, 10)
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.status_label.setFont(QFont("DM Sans", 14))
        self.status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; padding: 10px;")
        bottom_bar.addWidget(self.status_label, stretch=1)

        lay.addLayout(bottom_bar)

        # Lock button as direct child of page (not in layout)
        # so it floats ABOVE the tap overlay and receives touch events
        lock_size = self.active_event.lock_icon_size if self.active_event else 60
        font_size = max(14, lock_size // 2)
        self._idle_lock_btn = QPushButton("\U0001f512", page)
        self._idle_lock_btn.setFixedSize(lock_size, lock_size)
        self._idle_lock_btn.setCursor(Qt.PointingHandCursor)
        self._idle_lock_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; font-size: {font_size}px; "
            f"color: {config.COLOR_TEXT_DIM}; min-height: 0; padding: 0; }}"
            f"QPushButton:pressed {{ color: {config.COLOR_PRIMARY}; }}"
        )
        self._idle_lock_btn.clicked.connect(self._on_lock_clicked)
        self._idle_lock_btn.raise_()

        # Wifi-tip popup onderaan (alleen bij geen wifi)
        # Stijl past bij de software-popups (warm gold accent, niet rood)
        self._idle_wifi_tip = QFrame(page)
        self._idle_wifi_tip.setStyleSheet(
            f"QFrame {{ background: {config.COLOR_CARD_BG}; "
            f"border: 1px solid {config.COLOR_BORDER}; "
            f"border-radius: 14px; }}"
        )
        wifi_lay = QHBoxLayout(self._idle_wifi_tip)
        wifi_lay.setContentsMargins(18, 12, 18, 12)
        wifi_lay.setSpacing(14)
        wifi_icon = QLabel("📶")
        wifi_icon.setFont(QFont("DM Sans", 22))
        wifi_icon.setStyleSheet(f"color: {config.COLOR_PRIMARY}; background: transparent;")
        wifi_lay.addWidget(wifi_icon)
        wifi_text = QLabel(
            "<b>TIP</b> — Verbind de photobooth met wifi en download "
            "je foto's direct op je telefoon."
        )
        wifi_text.setFont(QFont("DM Sans", 13))
        wifi_text.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        wifi_text.setWordWrap(True)
        wifi_lay.addWidget(wifi_text, stretch=1)
        wifi_btn = QPushButton("Stel wifi in")
        wifi_btn.setCursor(Qt.PointingHandCursor)
        wifi_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        wifi_btn.setMinimumHeight(42)
        wifi_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 10px; padding: 8px 22px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        wifi_btn.clicked.connect(self._on_idle_wifi_setup_clicked)
        wifi_lay.addWidget(wifi_btn)
        self._idle_wifi_tip.setFixedHeight(70)
        self._idle_wifi_tip.hide()
        self._idle_wifi_tip_page = page  # voor positioning

        return page

    def _position_idle_wifi_tip(self):
        """Positioneer de wifi-tip onderaan, gecentreerd horizontaal."""
        if not hasattr(self, '_idle_wifi_tip') or self._idle_wifi_tip is None:
            return
        page = getattr(self, '_idle_wifi_tip_page', None)
        if not page:
            return
        w = min(700, page.width() - 60)
        h = self._idle_wifi_tip.height()
        x = (page.width() - w) // 2
        # Net boven de onderkant — niet helemaal tegen rand vanwege lock-btn
        y = page.height() - h - 40
        self._idle_wifi_tip.setGeometry(x, y, w, h)
        self._idle_wifi_tip.raise_()

    def _on_idle_wifi_setup_clicked(self):
        """Open de Windows wifi-flyout (zelfde aanpak als de welcome-page).

        BELANGRIJK: gebruik ms-availablenetworks: (het netwerk-popup
        paneeltje van de shell) en NIET ms-settings:network-wifi — de
        Settings-app opent als gewoon venster en belandt ONZICHTBAAR
        achter het fullscreen always-on-top boothscherm, waardoor de
        knop "niks lijkt te doen". De shell-flyout verschijnt wél
        bovenop een topmost venster.
        """
        try:
            os.startfile("ms-availablenetworks:")
            print("[WIFI] WiFi flyout geopend")
        except OSError as e:
            print(f"[WIFI] Kon WiFi flyout niet openen: {e}")
            # Fallback: Settings → Network → WiFi (kan achter het
            # boothscherm belanden, maar beter dan niets)
            try:
                os.startfile("ms-settings:network-wifi")
            except OSError as e2:
                print(f"[WIFI] Settings page ook niet bereikbaar: {e2}")

    def _idle_wifi_check_tick(self):
        """Periodieke check (elke 2s) op de idle-page: probeer een snelle
        TCP-connectie naar 1.1.1.1:53 in een bg-thread; toon/verberg de
        wifi-tip op basis van resultaat."""
        if getattr(self, 'state', None) != State.IDLE:
            # State is veranderd — timer + popup opruimen
            if hasattr(self, '_idle_wifi_check_timer') and self._idle_wifi_check_timer.isActive():
                self._idle_wifi_check_timer.stop()
            if hasattr(self, '_idle_wifi_tip') and self._idle_wifi_tip is not None \
                    and self._idle_wifi_tip.isVisible():
                self._idle_wifi_tip.hide()
            return
        def _bg():
            import socket
            try:
                with socket.create_connection(("1.1.1.1", 53), timeout=2):
                    online = True
            except Exception:
                online = False
            self._idle_wifi_tip_signal.emit(online)
        threading.Thread(target=_bg, daemon=True).start()

    def _on_idle_wifi_state(self, online: bool):
        """Update wifi-tip visibility op de idle-page."""
        self._has_internet = online
        if not hasattr(self, '_idle_wifi_tip') or self._idle_wifi_tip is None:
            return
        if online:
            if self._idle_wifi_tip.isVisible():
                self._idle_wifi_tip.hide()
        else:
            if not self._idle_wifi_tip.isVisible():
                self._position_idle_wifi_tip()
                self._idle_wifi_tip.show()
                self._idle_wifi_tip.raise_()

    # --- TEMPLATE SELECT ---
    def _build_template_select_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(30, 20, 30, 20)
        lay.setSpacing(15)

        lay.addWidget(self._make_title(t("select_language") if False else t("tab_layout"), 36))

        # Scrollable grid of templates with touch kinetic scrolling
        from PyQt5.QtWidgets import QScroller
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            f"QScrollBar:vertical {{ background: {config.COLOR_BG}; width: 14px; border-radius: 7px; }}"
            f"QScrollBar::handle:vertical {{ background: {config.COLOR_BORDER}; border-radius: 7px; min-height: 50px; }}"
        )
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        scroll_widget = QWidget()
        self.template_grid = QGridLayout(scroll_widget)
        self.template_grid.setSpacing(18)
        scroll.setWidget(scroll_widget)
        lay.addWidget(scroll, stretch=1)

        # Buttons
        btn_lay = QHBoxLayout()
        back_btn = self._make_button(t("editor_back"), self._go_idle, "secondaryBtn")
        btn_lay.addWidget(back_btn)
        btn_lay.addStretch()
        lay.addLayout(btn_lay)

        self.stack.addWidget(page)

    def _load_templates(self):
        """Scan templates and backgrounds, populate the grid."""
        # Clear existing items
        while self.template_grid.count():
            item = self.template_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        templates = list_templates(config.TEMPLATES_DIR, config.BACKGROUNDS_DIR)

        col = 0
        row = 0
        # Dynamic columns based on screen width
        screen_w = self.width() if self.width() > 0 else 1920
        if screen_w < 600:
            cols = 1
        elif screen_w < 900:
            cols = 2
        else:
            cols = 3

        for template in templates:
            thumb = self._make_template_thumbnail(template)
            self.template_grid.addWidget(thumb, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def _make_template_thumbnail(self, template):
        """Create a clickable template thumbnail widget (touch-friendly)."""
        container = QWidget()
        # Responsive size — smaller in portrait
        if self._is_portrait():
            container.setFixedSize(220, 330)
        else:
            container.setFixedSize(280, 420)
        container.setCursor(Qt.PointingHandCursor)
        container.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_CARD_BG}; border: 3px solid {config.COLOR_BORDER}; border-radius: 14px; }}"
            f"QWidget:hover {{ border-color: {config.COLOR_PRIMARY}; background: {config.COLOR_ACCENT}; }}"
        )

        lay = QVBoxLayout(container)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignCenter)

        bg_path = template.get_thumbnail_path()
        if bg_path:
            pixmap = QPixmap(bg_path)
            if not pixmap.isNull():
                thumb_w = 196 if self._is_portrait() else 256
                thumb_h = 260 if self._is_portrait() else 340
                scaled = pixmap.scaled(
                    thumb_w, thumb_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                thumb_label.setPixmap(scaled)
            else:
                thumb_label.setText(template.name)
            thumb_w = 196 if self._is_portrait() else 256
            thumb_h = 260 if self._is_portrait() else 340
            thumb_label.setFixedSize(thumb_w, thumb_h)
            lay.addWidget(thumb_label)
            # Name label below
            name_label = QLabel(template.name)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setFont(QFont("DM Sans", 13, QFont.Bold))
            name_label.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent; border: none;")
            lay.addWidget(name_label)
        else:
            # Default/no background template
            thumb_label.setFixedSize(196 if self._is_portrait() else 256,
                                    290 if self._is_portrait() else 370)
            thumb_label.setStyleSheet(
                f"background: white; border-radius: 8px; color: {config.COLOR_TEXT};"
            )
            thumb_label.setText(f"{self._translate_template_name(template.name)}\n({template.num_photos} {t('photos_count')})")
            thumb_label.setFont(QFont("DM Sans", 16, QFont.Bold))
            lay.addWidget(thumb_label)

        # Click handler
        container.mousePressEvent = lambda e, t=template: self._on_template_selected(t)
        return container

    def _on_template_selected(self, template):
        """Handle template selection."""
        self.selected_template = template
        bg = template.background_path or "Geen (wit)"
        print(f"[BG] Achtergrond geselecteerd: {bg}")
        print(f"[TEMPLATE] '{template.name}' - {template.num_photos} foto's")
        self._preload_background()
        self._go_preview()

    # --- PREVIEW ---
    def _build_preview_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Hidden counter label (kept for compatibility with _update_counter)
        self.photo_counter_label = QLabel()
        self.photo_counter_label.hide()

        # Live view — takes full screen
        self.live_view_label = QLabel()
        self.live_view_label.setAlignment(Qt.AlignCenter)
        # CRITICAL: use Ignored so pixmap sizeHint never forces layout to grow.
        # Expanding uses sizeHint (= pixmap size) → pushes everything down when
        # a large frame arrives. Ignored = "take whatever space is given, never ask for more".
        self.live_view_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.live_view_label.setMinimumSize(0, 0)
        self.live_view_label.setStyleSheet(f"background: black;")
        lay.addWidget(self.live_view_label, stretch=1)

        # Cancel button (X) — top-left overlay, created later as child of page
        self.cancel_session_btn = QPushButton("✕", page)
        self.cancel_session_btn.setFixedSize(60, 60)
        self.cancel_session_btn.setFont(QFont("DM Sans", 24, QFont.Bold))
        self.cancel_session_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,0,0,0.5); color: white;
                border: none; border-radius: 30px;
            }}
            QPushButton:hover {{ background: rgba(0,0,0,0.7); }}
        """)
        self.cancel_session_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.cancel_session_btn.clicked.connect(self._cancel_session)
        self.cancel_session_btn.raise_()

        # Photo blocks at bottom — overlay on top of live view
        self.thumbs_widget = QWidget(page)
        self.thumbs_widget.setStyleSheet("background: transparent;")
        self.thumbs_layout = QHBoxLayout(self.thumbs_widget)
        self.thumbs_layout.setSpacing(12)
        self.thumbs_layout.setAlignment(Qt.AlignCenter)
        self.thumbs_layout.setContentsMargins(20, 10, 20, 10)
        self.thumb_labels = []

        # Start button — overlay, shown initially, hidden during countdown
        self.capture_btn = QPushButton(t("photo_make"), page)
        self.capture_btn.setFont(QFont("DM Sans", 22, QFont.Bold))
        self.capture_btn.setMinimumSize(300, 80)
        self.capture_btn.setStyleSheet(f"""
            QPushButton {{
                background: {config.COLOR_SUCCESS}; color: #ffffff;
                border: none; border-radius: 40px; padding: 15px 40px;
                font-size: 22px; min-height: 0;
            }}
            QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}
        """)
        self.capture_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.capture_btn.clicked.connect(self._start_countdown)

        self.stack.addWidget(page)

    def _rebuild_thumbnails(self):
        """Rebuild photo blocks based on current template's num_photos."""
        # Clear existing thumbnails
        for thumb in self.thumb_labels:
            self.thumbs_layout.removeWidget(thumb)
            thumb.deleteLater()
        self.thumb_labels = []

        # Create photo placeholder blocks (responsive size)
        t_w = 70 if self._is_portrait() else 100
        t_h = 52 if self._is_portrait() else 75
        for i in range(self.num_photos):
            thumb = QLabel()
            thumb.setFixedSize(t_w, t_h)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet(
                f"background: rgba(255,255,255,0.15); "
                f"border: 2px dashed rgba(255,255,255,0.4); border-radius: 8px;"
            )
            thumb.setFont(QFont("DM Sans", 16, QFont.Bold))
            thumb.setStyleSheet(
                thumb.styleSheet() + f" color: rgba(255,255,255,0.5);"
            )
            thumb.setText(str(i + 1))
            self.thumb_labels.append(thumb)
            self.thumbs_layout.addWidget(thumb)

    # --- COUNTDOWN ---
    def _build_countdown_page(self):
        """Countdown page shares live view with preview page.

        All overlays (ring, intro, capture screen) are children of
        the preview page so everything happens on one screen.
        """
        preview_page = self.stack.widget(self.pages["preview"])

        # Countdown info label (hidden, kept for compatibility)
        self.countdown_info = QLabel()
        self.countdown_info.hide()

        # Point to same live view label as preview
        self.countdown_live_label = self.live_view_label

        # Animated countdown ring overlay
        self.countdown_ring = CountdownRingWidget(preview_page)
        self.countdown_ring.hide()

        # Intro overlay label ("We gaan X foto's maken")
        self.intro_label = QLabel(preview_page)
        self.intro_label.setAlignment(Qt.AlignCenter)
        self.intro_label.setWordWrap(True)
        self.intro_label.setFont(QFont("DM Sans", 36, QFont.Bold))
        self.intro_label.setStyleSheet(
            "color: white; background: rgba(0,0,0,0.5); border-radius: 20px; "
            "padding: 20px 40px;"
        )
        self.intro_label.hide()

        # Capture screen overlay — shown at the exact moment of capture
        self.capture_screen_label = QLabel(preview_page)
        self.capture_screen_label.setAlignment(Qt.AlignCenter)
        self.capture_screen_label.setWordWrap(True)
        self.capture_screen_label.setFont(QFont("DM Sans", 36, QFont.Bold))
        self.capture_screen_label.setStyleSheet("background: white; color: #333333;")
        self.capture_screen_label.hide()
        self._capture_screen_pixmap = None  # cached QPixmap
        self._live_view_frozen = False

        # No separate stack page needed — countdown happens on preview page
        # But we still need a "countdown" entry in self.pages for state tracking
        # We'll use the same page index as preview
        self.pages["countdown"] = self.pages["preview"]

    # --- REVIEW (strip) ---
    def _build_review_confirm_panel(self) -> "QWidget":
        """Panel 1 op review-pagina rechts: 'Zijn de foto's goed gelukt?'

        Twee knoppen:
          - Ja                → naar print-vraag panel
          - Nee, begin opnieuw → reset sessie + terug naar preview
        """
        panel = QWidget()
        panel.setStyleSheet("QWidget { background: rgba(255,255,255,0.06); }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(14)
        lay.addStretch()

        title = QLabel("Zijn de foto's goed gelukt?")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 22, QFont.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        title.setWordWrap(True)
        lay.addWidget(title)

        lay.addSpacing(16)

        yes_btn = QPushButton("✓  Ja")
        yes_btn.setCursor(Qt.PointingHandCursor)
        yes_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        yes_btn.setMinimumHeight(72)
        yes_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
            f"QPushButton:pressed {{ background: #3A8B5E; }}"
        )
        yes_btn.clicked.connect(self._on_review_photos_ok)
        lay.addWidget(yes_btn)
        self._review_confirm_yes_btn = yes_btn

        no_btn = QPushButton("✗  Nee, begin opnieuw")
        no_btn.setCursor(Qt.PointingHandCursor)
        no_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        no_btn.setMinimumHeight(60)
        no_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #cccccc; "
            "border: 1px solid rgba(255,255,255,0.18); border-radius: 16px; "
            "padding: 14px; font-size: 16px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); color: white; }"
        )
        no_btn.clicked.connect(self._on_review_photos_redo)
        lay.addWidget(no_btn)
        self._review_confirm_no_btn = no_btn

        lay.addStretch()
        return panel

    def _build_review_print_question_panel(self) -> "QWidget":
        """Panel 2 op review-pagina rechts: 'Wil je de foto's geprint hebben?'

        Twee knoppen:
          - Ja      → print + naar standaard action panel (met QR)
          - Nee     → direct naar action panel zonder auto-print
        """
        panel = QWidget()
        panel.setStyleSheet("QWidget { background: rgba(255,255,255,0.06); }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(14)
        lay.addStretch()

        title = QLabel("Wil je de foto's geprint hebben?")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 22, QFont.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        title.setWordWrap(True)
        lay.addWidget(title)

        lay.addSpacing(16)

        yes_btn = QPushButton("🖨  Ja, print")
        yes_btn.setCursor(Qt.PointingHandCursor)
        yes_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        yes_btn.setMinimumHeight(72)
        yes_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
            f"QPushButton:pressed {{ background: #3A8B5E; }}"
        )
        yes_btn.clicked.connect(self._on_review_print_yes)
        lay.addWidget(yes_btn)
        self._review_print_yes_btn = yes_btn

        no_btn = QPushButton("✗  Nee, geen print")
        no_btn.setCursor(Qt.PointingHandCursor)
        no_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        no_btn.setMinimumHeight(60)
        no_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #cccccc; "
            "border: 1px solid rgba(255,255,255,0.18); border-radius: 16px; "
            "padding: 14px; font-size: 16px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); color: white; }"
        )
        no_btn.clicked.connect(self._on_review_print_no)
        lay.addWidget(no_btn)
        self._review_print_no_btn = no_btn

        lay.addStretch()
        return panel

    def _build_review_page(self):
        """Build unified sharing screen: photo + all actions on one page.
        Layout adapts: landscape = side-by-side, portrait = stacked."""
        page = QWidget()
        page.setStyleSheet(f"background: #1a1a1a;")
        self._review_page = page

        # Use a QVBoxLayout as wrapper — actual layout managed by _adapt_review_layout
        main_lay = QVBoxLayout(page)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # === Countdown progress bar — overlay at very top of screen, full width ===
        self._sharing_countdown_bar = QProgressBar(page)
        self._sharing_countdown_bar.setRange(0, 100)
        self._sharing_countdown_bar.setValue(100)
        self._sharing_countdown_bar.setTextVisible(False)
        self._sharing_countdown_bar.setFixedHeight(6)
        self._sharing_countdown_bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(255,255,255,0.15); border: none; }}"
            f"QProgressBar::chunk {{ background: {config.COLOR_PRIMARY}; border: none; }}"
        )
        self._sharing_countdown_bar.raise_()

        # Timer for smooth countdown animation
        self._sharing_countdown_timer = QTimer()
        self._sharing_countdown_timer.setInterval(100)  # Update every 100ms
        self._sharing_countdown_timer.timeout.connect(self._on_sharing_countdown_tick)
        self._sharing_countdown_total_ms = 30000  # 30 seconds
        self._sharing_countdown_elapsed_ms = 0

        # === Photo strip container ===
        self._review_photo_container = QWidget()
        self._review_photo_container.setStyleSheet("background: transparent;")
        photo_lay = QVBoxLayout(self._review_photo_container)
        photo_lay.setContentsMargins(20, 20, 20, 10)
        photo_lay.setSpacing(0)

        self.review_strip_label = QLabel()
        self.review_strip_label.setAlignment(Qt.AlignCenter)
        self.review_strip_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.review_strip_label.setStyleSheet("background: transparent;")
        photo_lay.addWidget(self.review_strip_label)

        # === Action panel (buttons) ===
        self._review_action_panel = QWidget()
        self._review_action_panel.setStyleSheet(
            "QWidget { background: rgba(255,255,255,0.06); }"
        )
        right_lay = QVBoxLayout(self._review_action_panel)
        right_lay.setContentsMargins(28, 20, 28, 20)
        right_lay.setSpacing(14)

        right_lay.addStretch()

        # Print status label (shows when printing)
        self._sharing_print_status = QLabel("")
        self._sharing_print_status.setAlignment(Qt.AlignCenter)
        self._sharing_print_status.setFont(QFont("DM Sans", 14))
        self._sharing_print_status.setStyleSheet("color: #aaaaaa; background: transparent;")
        self._sharing_print_status.setWordWrap(True)
        self._sharing_print_status.setFixedHeight(36)
        # Retain layout space when hidden — prevents buttons from shifting
        _sp = self._sharing_print_status.sizePolicy()
        _sp.setRetainSizeWhenHidden(True)
        self._sharing_print_status.setSizePolicy(_sp)
        self._sharing_print_status.hide()
        right_lay.addWidget(self._sharing_print_status)

        # --- PRINT button ---
        self._sharing_print_btn = QPushButton("🖨  " + t("btn_print"))
        self._sharing_print_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_print_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._sharing_print_btn.setMinimumHeight(72)
        self._sharing_print_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
            f"QPushButton:pressed {{ background: #3A8B5E; }}"
            f"QPushButton:disabled {{ background: #555555; color: #888888; }}"
        )
        self._sharing_print_btn.clicked.connect(self._sharing_do_print)
        right_lay.addWidget(self._sharing_print_btn)

        # ── Inline print-delay knoppen (verborgen tot 'Ja print' klik) ──
        # Tijdens de pakket-afhankelijke afkoelperiode (5s premium / 30s
        # standaard; 0s als storingsmeldingen uit staan) komt hier de print-
        # knop niet meer, maar de mogelijkheid om de pending print alsnog te
        # annuleren of opnieuw te fotograferen.
        self._sharing_cancel_print_btn = QPushButton("✕  Annuleer print")
        self._sharing_cancel_print_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_cancel_print_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        self._sharing_cancel_print_btn.setMinimumHeight(60)
        self._sharing_cancel_print_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: white; "
            f"border: none; border-radius: 14px; padding: 10px; }}"
            f"QPushButton:hover {{ background: #A93223; }}"
        )
        self._sharing_cancel_print_btn.clicked.connect(self._on_inline_print_cancel)
        _spcp = self._sharing_cancel_print_btn.sizePolicy()
        _spcp.setRetainSizeWhenHidden(False)
        self._sharing_cancel_print_btn.setSizePolicy(_spcp)
        self._sharing_cancel_print_btn.hide()
        right_lay.addWidget(self._sharing_cancel_print_btn)

        self._sharing_redo_print_btn = QPushButton("📸  Foto's opnieuw maken")
        self._sharing_redo_print_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_redo_print_btn.setFont(QFont("DM Sans", 15, QFont.Bold))
        self._sharing_redo_print_btn.setMinimumHeight(56)
        self._sharing_redo_print_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.12); color: white; "
            f"border: 1px solid rgba(255,255,255,0.3); border-radius: 14px; "
            f"padding: 8px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.22); }}"
        )
        self._sharing_redo_print_btn.clicked.connect(self._on_inline_print_redo)
        _sprp = self._sharing_redo_print_btn.sizePolicy()
        _sprp.setRetainSizeWhenHidden(False)
        self._sharing_redo_print_btn.setSizePolicy(_sprp)
        self._sharing_redo_print_btn.hide()
        right_lay.addWidget(self._sharing_redo_print_btn)

        # Print remaining indicator
        self._sharing_prints_remaining = QLabel("")
        self._sharing_prints_remaining.setAlignment(Qt.AlignCenter)
        self._sharing_prints_remaining.setFont(QFont("DM Sans", 11))
        self._sharing_prints_remaining.setStyleSheet("color: #888888; background: transparent;")
        self._sharing_prints_remaining.setFixedHeight(24)
        right_lay.addWidget(self._sharing_prints_remaining)

        right_lay.addSpacing(8)

        # ── Inline QR-block: direct zichtbaar op sharing-screen ─────
        # (Vervangt de oude '📱 QR-code' knop die een fullscreen overlay
        # opende. Nu de QR + downloadprompt direct in beeld zodat de
        # gast 'm meteen kan scannen, geen klik nodig.)
        self._inline_qr_box = QWidget()
        self._inline_qr_box.setStyleSheet(
            "QWidget { background: white; border-radius: 16px; padding: 14px; }"
        )
        qr_box_lay = QVBoxLayout(self._inline_qr_box)
        qr_box_lay.setContentsMargins(14, 14, 14, 14)
        qr_box_lay.setSpacing(8)

        self._inline_qr_label = QLabel()
        self._inline_qr_label.setAlignment(Qt.AlignCenter)
        self._inline_qr_label.setMinimumSize(220, 220)
        self._inline_qr_label.setMaximumSize(320, 320)
        self._inline_qr_label.setScaledContents(True)
        self._inline_qr_label.setStyleSheet("background: white;")
        qr_box_lay.addWidget(self._inline_qr_label, alignment=Qt.AlignCenter)

        # Animated 'uploading...' fallback (zichtbaar tot QR-pixmap er is)
        self._inline_qr_loading = QLabel("⏳  " + t("uploading"))
        self._inline_qr_loading.setAlignment(Qt.AlignCenter)
        self._inline_qr_loading.setFont(QFont("DM Sans", 14))
        self._inline_qr_loading.setStyleSheet(
            "color: #555; background: transparent;"
        )
        self._inline_qr_loading.hide()
        qr_box_lay.addWidget(self._inline_qr_loading)

        # Pijl + "Download op telefoon" prompt onder de QR
        self._inline_qr_prompt = QLabel(
            "↓\nDownload foto's op je telefoon"
        )
        self._inline_qr_prompt.setAlignment(Qt.AlignCenter)
        self._inline_qr_prompt.setFont(QFont("DM Sans", 13, QFont.Bold))
        self._inline_qr_prompt.setStyleSheet(
            "color: #1a1a1a; background: transparent;"
        )
        qr_box_lay.addWidget(self._inline_qr_prompt)
        right_lay.addWidget(self._inline_qr_box)

        # Alternatieve TIP-box voor wanneer er geen wifi is. Tonen we
        # ipv de QR-box. Compact paneel met de instructie.
        self._inline_no_wifi_tip = QLabel(
            "💡  TIP\n"
            "Verbind de photobooth met wifi om de foto's\n"
            "via een QR-code op je telefoon te downloaden."
        )
        self._inline_no_wifi_tip.setAlignment(Qt.AlignCenter)
        self._inline_no_wifi_tip.setFont(QFont("DM Sans", 14, QFont.Bold))
        self._inline_no_wifi_tip.setWordWrap(True)
        self._inline_no_wifi_tip.setStyleSheet(
            "QLabel { background: rgba(255,255,255,0.10); color: white; "
            "border: 1px solid rgba(255,255,255,0.18); border-radius: 16px; "
            "padding: 22px 18px; }"
        )
        self._inline_no_wifi_tip.hide()
        right_lay.addWidget(self._inline_no_wifi_tip)

        # Oude '📱 QR-code'-knop is volledig vervangen door de inline QR-box
        # hierboven. We houden de attribute voor backwards-compat van legacy
        # code-paden die hem nog refereren (setVisible/setEnabled in andere
        # branches), maar plaatsen 'm NIET in de layout en geven een fixed
        # size van 0 zodat hij ook bij setVisible(True) niet visueel
        # opduikt. Show wordt monkey-patched naar no-op.
        self._sharing_qr_btn = QPushButton(page)
        self._sharing_qr_btn.setFixedSize(0, 0)
        self._sharing_qr_btn.clicked.connect(self._sharing_show_qr)
        self._sharing_qr_btn.hide()
        self._sharing_qr_btn.show = lambda: None  # no-op show — kan nooit verschijnen
        self._sharing_qr_btn.setVisible = lambda _v=False: None

        right_lay.addSpacing(8)

        # --- EMAIL button ---
        self._sharing_email_btn = QPushButton("📧  " + t("btn_email"))
        self._sharing_email_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_email_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._sharing_email_btn.setMinimumHeight(72)
        self._sharing_email_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.12); color: white; "
            f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.18); }}"
            f"QPushButton:pressed {{ background: rgba(255,255,255,0.25); }}"
        )

        self._sharing_email_btn.clicked.connect(self._go_email_input)
        # Retain layout space when hidden — prevents done button from shifting
        _sp3 = self._sharing_email_btn.sizePolicy()
        _sp3.setRetainSizeWhenHidden(True)
        self._sharing_email_btn.setSizePolicy(_sp3)
        right_lay.addWidget(self._sharing_email_btn)

        # --- No WiFi label (hidden by default) ---
        self._no_wifi_label = QLabel("⚠  " + t("no_internet"))
        self._no_wifi_label.setAlignment(Qt.AlignCenter)
        self._no_wifi_label.setFont(QFont("DM Sans", 12))
        self._no_wifi_label.setStyleSheet("color: #ff6b6b; background: transparent;")
        self._no_wifi_label.setFixedHeight(30)
        # Retain layout space when hidden — prevents done button from shifting
        _sp2 = self._no_wifi_label.sizePolicy()
        _sp2.setRetainSizeWhenHidden(True)
        self._no_wifi_label.setSizePolicy(_sp2)
        self._no_wifi_label.hide()
        right_lay.addWidget(self._no_wifi_label)

        right_lay.addStretch()

        # --- KLAAR button (always at bottom) ---
        self._sharing_done_btn = QPushButton("✓  " + t("btn_done"))
        self._sharing_done_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_done_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._sharing_done_btn.setMinimumHeight(72)
        self._sharing_done_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #cccccc; "
            "border: 1px solid rgba(255,255,255,0.15); border-radius: 16px; "
            "padding: 16px; font-size: 18px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.14); color: white; }"
            "QPushButton:pressed { background: rgba(255,255,255,0.22); color: white; }"
        )
        self._sharing_done_btn.clicked.connect(self._go_done)
        right_lay.addWidget(self._sharing_done_btn)

        # ── Tussen-scherm 1: "Zijn de foto's goed gelukt?" ──
        self._review_confirm_panel = self._build_review_confirm_panel()
        # ── Tussen-scherm 2: "Wil je de foto's geprint hebben?" ──
        self._review_print_question_panel = self._build_review_print_question_panel()

        # QStackedWidget houdt de 3 panelen op de rechterkant (of onderkant
        # in portrait). Page-volgorde matters: confirm → print-vraag → action.
        from PyQt5.QtWidgets import QStackedWidget as _QStackedWidget
        self._review_panel_stack = _QStackedWidget()
        self._review_panel_stack.setStyleSheet("background: transparent;")
        self._review_panel_stack.addWidget(self._review_confirm_panel)         # idx 0
        self._review_panel_stack.addWidget(self._review_print_question_panel)  # idx 1
        self._review_panel_stack.addWidget(self._review_action_panel)          # idx 2
        self._review_panel_stack.setCurrentIndex(0)

        # Build the layout directly — no dynamic wrapper reparenting
        # to avoid Windows fullscreen geometry corruption
        self._review_wrapper = QWidget()
        self._review_wrapper.setStyleSheet("background: transparent;")
        wrap_lay = QVBoxLayout(self._review_wrapper)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.setSpacing(0)
        wrap_lay.addWidget(self._review_photo_container, stretch=3)
        wrap_lay.addWidget(self._review_panel_stack, stretch=0)
        main_lay.addWidget(self._review_wrapper)
        self._review_is_portrait = True

        # === QR overlay (shown when QR button is pressed) ===
        self._qr_overlay = QWidget(page)
        self._qr_overlay.setStyleSheet(
            "QWidget { background: rgba(0,0,0,0.92); border-radius: 20px; }"
        )
        self._qr_overlay.hide()
        qr_ov_lay = QVBoxLayout(self._qr_overlay)
        qr_ov_lay.setContentsMargins(30, 20, 30, 20)
        qr_ov_lay.setSpacing(12)

        qr_title = QLabel(t("scan_for_photo"))
        qr_title.setAlignment(Qt.AlignCenter)
        qr_title.setFont(QFont("DM Sans", 22, QFont.Bold))
        qr_title.setStyleSheet("color: white; background: transparent;")
        qr_ov_lay.addWidget(qr_title)

        # QR loading spinner label (animated dots)
        self._qr_loading_label = QLabel("⏳  " + t("uploading"))
        self._qr_loading_label.setAlignment(Qt.AlignCenter)
        self._qr_loading_label.setFont(QFont("DM Sans", 16))
        self._qr_loading_label.setMinimumSize(200, 200)
        self._qr_loading_label.setStyleSheet("color: #aaaaaa; background: rgba(255,255,255,0.05); border-radius: 16px;")
        self._qr_loading_label.hide()
        qr_ov_lay.addWidget(self._qr_loading_label, alignment=Qt.AlignCenter)

        # Timer for animating the loading dots
        self._qr_spinner_timer = QTimer(self)
        self._qr_spinner_dot_count = 0
        self._qr_spinner_timer.timeout.connect(self._animate_qr_spinner)
        self._qr_spinner_timer.setInterval(400)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(250, 250)
        self.qr_label.setMaximumSize(400, 400)
        self.qr_label.setScaledContents(True)
        self.qr_label.setStyleSheet(
            "background: white; border-radius: 16px; padding: 20px;"
        )
        qr_ov_lay.addWidget(self.qr_label, alignment=Qt.AlignCenter)

        self.qr_url_label = QLabel("")
        self.qr_url_label.setAlignment(Qt.AlignCenter)
        self.qr_url_label.setFont(QFont("DM Sans", 11))
        self.qr_url_label.setWordWrap(True)
        self.qr_url_label.setStyleSheet("color: #888888; background: transparent;")
        qr_ov_lay.addWidget(self.qr_url_label)

        qr_close_btn = QPushButton(t("close").upper())
        qr_close_btn.setCursor(Qt.PointingHandCursor)
        qr_close_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        qr_close_btn.setMinimumHeight(50)
        qr_close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.15); color: white; "
            "border: none; border-radius: 12px; padding: 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )
        qr_close_btn.clicked.connect(lambda: self._qr_overlay.hide())
        qr_ov_lay.addWidget(qr_close_btn)

        self.stack.addWidget(page)

        # Track prints used in this session
        self._session_prints_used = 0

    # --- PRINTING (kept as hidden state, no separate page) ---
    def _build_printing_page(self):
        """Printing is now handled inline on the sharing screen."""
        # Create a dummy page to keep page indices stable
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        self.printing_strip_label = QLabel()  # Keep for compatibility
        self.printing_strip_label.hide()
        lay.addWidget(self.printing_strip_label)
        lay.addWidget(self._make_title(t("printing"), 36))
        self.stack.addWidget(page)

    # --- QR CODE (now integrated into sharing screen) ---
    def _build_qr_page(self):
        """QR is now shown as overlay on sharing screen. Keep dummy page for index stability."""
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._make_title("QR Code", 36))
        self.stack.addWidget(page)

    # --- DATA COLLECTION ---

    def _build_data_collection_page(self):
        """Build the data collection form page with dynamic fields."""
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        main_lay = QVBoxLayout(page)
        main_lay.setContentsMargins(20, 15, 20, 10)
        main_lay.setSpacing(6)

        # Portret: top-aligned (content drukt naar boven). Landscape: gecentreerd.
        # Tijdens __init__ is self.width()/height() nog niet correct (window is
        # nog niet getoond), dus we gebruiken de scherm-geometrie direct.
        _screen = QApplication.primaryScreen().availableGeometry()
        _is_portrait = _screen.height() > _screen.width()
        _top_stretch = 0 if _is_portrait else 1
        main_lay.addStretch(_top_stretch)

        # Title
        title = QLabel(t("dc_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 22, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        main_lay.addWidget(title)

        subtitle = QLabel(t("dc_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 13))
        subtitle.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        main_lay.addWidget(subtitle)

        main_lay.addSpacing(8)

        # Form fields container (will be populated dynamically)
        self._dc_form_container = QWidget()
        self._dc_form_layout = QVBoxLayout(self._dc_form_container)
        self._dc_form_layout.setSpacing(10)
        self._dc_form_layout.setContentsMargins(0, 0, 0, 0)
        main_lay.addWidget(self._dc_form_container)

        # Status label
        self._dc_status = QLabel("")
        self._dc_status.setAlignment(Qt.AlignCenter)
        self._dc_status.setFont(QFont("DM Sans", 11))
        main_lay.addWidget(self._dc_status)

        # Touch keyboard
        self._dc_keyboard_container = QWidget()
        self._dc_keyboard_layout = QVBoxLayout(self._dc_keyboard_container)
        self._dc_keyboard_layout.setContentsMargins(0, 0, 0, 0)
        self._dc_active_input = None
        self._build_dc_keyboard()
        main_lay.addWidget(self._dc_keyboard_container)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self._dc_cancel_btn = QPushButton(t("cancel"))
        self._dc_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._dc_cancel_btn.setFont(QFont("DM Sans", 11))
        self._dc_cancel_btn.setFixedHeight(40)
        self._dc_cancel_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.08); color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px; }}"
            f"QPushButton:pressed {{ background: rgba(255,255,255,0.15); }}"
        )
        self._dc_cancel_btn.clicked.connect(self._dc_cancel)
        btn_row.addWidget(self._dc_cancel_btn, stretch=1)

        go_btn = QPushButton(t("dc_continue"))
        go_btn.setCursor(Qt.PointingHandCursor)
        go_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        go_btn.setFixedHeight(40)
        go_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        go_btn.clicked.connect(self._dc_submit)
        btn_row.addWidget(go_btn, stretch=2)

        main_lay.addLayout(btn_row)

        # Bottom spacer — samen met de top spacer centreert dit alle content
        # verticaal op het scherm.
        main_lay.addStretch(1)

        self.stack.addWidget(page)

    def _build_dc_keyboard(self):
        """Build touch keyboard met echte toetsenbord-layout.

        Layout (boven naar onder):
          1 2 3 4 5 6 7 8 9 0
          Q W E R T Y U I O P
          A S D F G H J K L '            (apostrofe op 10e plek)
          shift  Z X C V B N M  backspace
          CAPS  [    SPACE    ]  TAB
          @ . - _ .com .nl .de
        """
        self._dc_keyboard_layout.setSpacing(4)
        self._dc_kb_style = (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; "
            f"font-size: 18px; font-weight: bold; min-height: 50px; min-width: 40px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        self._dc_special_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; "
            f"font-size: 14px; font-weight: bold; min-height: 50px; padding: 0 6px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY}; }}"
        )
        self._dc_modifier_active_style = (
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: 2px solid {config.COLOR_TEXT}; border-radius: 8px; "
            f"font-size: 14px; font-weight: bold; min-height: 50px; padding: 0 6px; }}"
        )
        kb_style = self._dc_kb_style
        special_style = self._dc_special_style

        KEY_STRETCH = 4
        MODIFIER_STRETCH = 6

        # Cijferrij (BOVEN)
        num_row = QHBoxLayout()
        num_row.setSpacing(4)
        for ch in "1234567890":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._dc_key(c))
            num_row.addWidget(btn, KEY_STRETCH)
        self._dc_keyboard_layout.addLayout(num_row)

        # QWERTY-rij
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for ch in "QWERTYUIOP":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch.lower(): self._dc_key(c))
            row1.addWidget(btn, KEY_STRETCH)
        self._dc_keyboard_layout.addLayout(row1)

        # ASDF-rij + apostrofe (10 keys, uitgelijnd onder rij 1)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for ch in "ASDFGHJKL'":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            send = ch.lower() if ch.isalpha() else ch
            btn.clicked.connect(lambda _, c=send: self._dc_key(c))
            row2.addWidget(btn, KEY_STRETCH)
        self._dc_keyboard_layout.addLayout(row2)

        # ZXCV-rij + shift links + backspace rechts
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._dc_shift_btn = QPushButton("\u21e7 SHIFT")
        self._dc_shift_btn.setCursor(Qt.PointingHandCursor)
        self._dc_shift_btn.setStyleSheet(special_style)
        self._dc_shift_btn.clicked.connect(lambda: self._dc_key("SHIFT"))
        row3.addWidget(self._dc_shift_btn, MODIFIER_STRETCH)
        for ch in "ZXCVBNM":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch.lower(): self._dc_key(c))
            row3.addWidget(btn, KEY_STRETCH)
        back_btn = QPushButton("\u232b")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(special_style)
        back_btn.clicked.connect(lambda: self._dc_key("BACK"))
        row3.addWidget(back_btn, MODIFIER_STRETCH)
        self._dc_keyboard_layout.addLayout(row3)

        # CAPS + brede SPATIE + TAB
        row4 = QHBoxLayout()
        row4.setSpacing(4)
        self._dc_caps_btn = QPushButton("CAPS")
        self._dc_caps_btn.setCursor(Qt.PointingHandCursor)
        self._dc_caps_btn.setStyleSheet(special_style)
        self._dc_caps_btn.clicked.connect(lambda: self._dc_key("CAPS"))
        row4.addWidget(self._dc_caps_btn, MODIFIER_STRETCH)
        space_btn = QPushButton(t("key_space").upper())
        space_btn.setCursor(Qt.PointingHandCursor)
        space_btn.setStyleSheet(kb_style)
        space_btn.clicked.connect(lambda: self._dc_key(" "))
        # Spatie krijgt 7 keys breedte zodat hij echt breed is + gecentreerd
        row4.addWidget(space_btn, KEY_STRETCH * 7)
        tab_btn = QPushButton("TAB \u21e5")
        tab_btn.setCursor(Qt.PointingHandCursor)
        tab_btn.setStyleSheet(special_style)
        tab_btn.clicked.connect(lambda: self._dc_key("TAB"))
        row4.addWidget(tab_btn, MODIFIER_STRETCH)
        self._dc_keyboard_layout.addLayout(row4)

        # Email shortcuts — vaste basis (@ . - _) plus TLD-knoppen dynamisch
        # op basis van actieve taal (.com altijd, plus lokale TLDs)
        email_row = QHBoxLayout()
        email_row.setSpacing(4)
        email_keys = [("@", "@"), (".", "."), ("-", "-"), ("_", "_")]
        email_keys += [(tld, tld) for tld in self._get_email_tlds()]
        for key, label in email_keys:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(special_style)
            btn.clicked.connect(lambda _, k=key: self._dc_key(k))
            email_row.addWidget(btn)
        self._dc_keyboard_layout.addLayout(email_row)

    def _dc_update_modifier_styles(self):
        """Update visual state van shift/caps knoppen (oplichten als actief)."""
        if hasattr(self, '_dc_shift_btn'):
            active = getattr(self, '_dc_shift', False)
            self._dc_shift_btn.setStyleSheet(
                self._dc_modifier_active_style if active else self._dc_special_style
            )
        if hasattr(self, '_dc_caps_btn'):
            active = getattr(self, '_dc_caps', False)
            self._dc_caps_btn.setStyleSheet(
                self._dc_modifier_active_style if active else self._dc_special_style
            )

    def _kill_windows_touch_keyboard(self):
        """Onderdruk Windows TabTip.exe (touch-toetsenbord) als die opdoemt.
        Zwijgend — als TabTip niet draait gebeurt er gewoon niks.
        Wordt ALLEEN aangeroepen vanuit voucher-input + data-collection schermen,
        dus elders in de app blijft het Windows-toetsenbord gewoon beschikbaar.
        Killt ook TextInputHost.exe (Win11 variant)."""
        try:
            import subprocess
            CREATE_NO_WINDOW = 0x08000000
            for proc in ("TabTip.exe", "TextInputHost.exe"):
                subprocess.Popen(
                    ["taskkill", "/F", "/IM", proc],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
        except Exception:
            pass

    @staticmethod
    def _get_email_tlds():
        """Return een lijst TLD-suggesties op basis van de actieve taal.

        Bedoeld voor de email-shortcut-knoppen onder het toetsenbord. Iedere
        taal krijgt .com (universeel) plus de eigen lokale TLD(s).
        """
        lang = get_language()
        return {
            "nl": [".com", ".nl", ".be"],
            "en": [".com"],
            "de": [".com", ".de"],
            "fr": [".com", ".fr"],
            "es": [".com", ".es"],
            "it": [".com", ".it"],
        }.get(lang, [".com"])

    @staticmethod
    def _format_birthdate(text):
        """Format birthdate string als DD/MM/YYYY.

        Strip alle non-digits, knip op 8 cijfers (DDMMYYYY), zet slashes
        ertussen na pos 2 en 4. Voorbeelden:
          ""           -> ""
          "1"          -> "1"
          "12"         -> "12"
          "123"        -> "12/3"
          "1234"       -> "12/34"
          "12345"      -> "12/34/5"
          "12345678"   -> "12/34/5678"
          "123456789"  -> "12/34/5678"   (max 8 cijfers)
          "abc12d3"    -> "12/3"         (non-digits genegeerd)
        """
        digits = ''.join(c for c in (text or '') if c.isdigit())[:8]
        if len(digits) <= 2:
            return digits
        if len(digits) <= 4:
            return f"{digits[:2]}/{digits[2:]}"
        return f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"

    def _dc_key(self, key):
        """Handle keyboard key press for data collection form."""
        # Onderdruk Windows touch-toetsenbord die soms onverwacht opduikt
        self._kill_windows_touch_keyboard()
        # Auto-focus first input if none active
        if not self._dc_active_input:
            if hasattr(self, '_dc_inputs') and self._dc_inputs:
                self._dc_active_input = list(self._dc_inputs.values())[0]
            else:
                return
        inp = self._dc_active_input
        is_birthdate = inp.property("dc_field_key") == "geboortedatum"

        if key == "BACK":
            inp.setText(inp.text()[:-1])
            if is_birthdate:
                inp.setText(self._format_birthdate(inp.text()))
        elif key == "SHIFT":
            # Toggle shift voor de volgende letter (auto-reset na gebruik)
            self._dc_shift = not getattr(self, '_dc_shift', False)
            self._dc_update_modifier_styles()
        elif key == "CAPS":
            # Toggle caps lock — blijft staan tot opnieuw geklikt
            self._dc_caps = not getattr(self, '_dc_caps', False)
            self._dc_update_modifier_styles()
        elif key == "TAB":
            # Spring naar volgende invoerveld
            if hasattr(self, '_dc_inputs') and self._dc_inputs:
                inputs = list(self._dc_inputs.values())
                if inp in inputs:
                    next_idx = (inputs.index(inp) + 1) % len(inputs)
                    inputs[next_idx].setFocus()
                    self._dc_active_input = inputs[next_idx]
            return
        else:
            # Geboortedatum: alleen cijfers accepteren, daarna auto-format
            if is_birthdate:
                if len(key) == 1 and key.isdigit():
                    inp.setText(self._format_birthdate(inp.text() + key))
                # Niet-cijfers worden genegeerd voor geboortedatum
            else:
                # Normale character — pas shift/caps toe op letters
                is_upper = (getattr(self, '_dc_shift', False)
                            or getattr(self, '_dc_caps', False))
                if is_upper and len(key) == 1 and key.isalpha():
                    char = key.upper()
                else:
                    char = key
                inp.setText(inp.text() + char)
            # Shift reset na een letter; caps blijft
            if getattr(self, '_dc_shift', False):
                self._dc_shift = False
                self._dc_update_modifier_styles()
        inp.setFocus()

    def _show_data_collection(self, timing_context="before"):
        """Show the data collection form with fields from event settings.

        Args:
            timing_context: "before" (pre-capture) or "after" (post-capture)
        """
        ev = self.active_event
        if not ev:
            return
        self._dc_timing_context = timing_context
        self.state = State.DATA_COLLECTION

        # Clear previous form fields
        while self._dc_form_layout.count():
            item = self._dc_form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build form fields dynamically
        fields_str = getattr(ev, 'data_collect_fields', 'email')
        fields = [f.strip() for f in fields_str.split(',') if f.strip()]

        field_config = {
            "email": (t("dc_email"), t("dc_email_placeholder")),
            "naam": (t("dc_name"), t("dc_name_placeholder")),
            "telefoon": (t("dc_phone"), t("dc_phone_placeholder")),
            "adres": (t("dc_address"), t("dc_address_placeholder")),
            "geboortedatum": (t("dc_birthdate"), t("dc_birthdate_placeholder")),
        }

        self._dc_inputs = {}
        for field_key in fields:
            label_text, placeholder = field_config.get(field_key, (field_key, ""))
            lbl = QLabel(label_text)
            lbl.setFont(QFont("DM Sans", 11, QFont.Bold))
            lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
            self._dc_form_layout.addWidget(lbl)

            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            inp.setFont(QFont("DM Sans", 16))
            inp.setMinimumHeight(44)
            # Drie lagen bescherming tegen Windows TabTip auto-popup:
            # 1) WA_InputMethodEnabled=False
            # 2) ImhSensitiveData hint (Win negeert touch-kb voor sensitive fields)
            # 3) ReadOnly (Win detecteert dan geen "tekst-invoer modus";
            #    setText() blijft programmatisch werken)
            inp.setAttribute(Qt.WA_InputMethodEnabled, False)
            inp.setInputMethodHints(
                Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            )
            inp.setReadOnly(True)
            inp.setStyleSheet(
                f"QLineEdit {{ background: rgba(255,255,255,0.1); color: {config.COLOR_TEXT}; "
                f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px 12px; }}"
                f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
            )
            inp.mousePressEvent = lambda e, i=inp: self._dc_focus_input(i)
            # Tag de input zodat _dc_key auto-format kan toepassen op specifieke velden
            inp.setProperty("dc_field_key", field_key)
            self._dc_form_layout.addWidget(inp)
            self._dc_inputs[field_key] = inp

        # Focus first input
        if self._dc_inputs:
            first = list(self._dc_inputs.values())[0]
            self._dc_active_input = first

        self._dc_status.setText("")
        self.stack.setCurrentIndex(self.pages["data_collection"])

    def _dc_focus_input(self, inp):
        """Set active input for keyboard."""
        self._dc_active_input = inp
        inp.setFocus()
        # Onderdruk Windows touch-toetsenbord die op focus kan triggeren
        self._kill_windows_touch_keyboard()

    # ── VOUCHER INPUT PAGE ──────────────────────────────────────────
    def _build_voucher_input_page(self):
        """Voucher-code invoer page (lay-out kopie van data_collection)."""
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        main_lay = QVBoxLayout(page)
        main_lay.setContentsMargins(20, 15, 20, 10)
        main_lay.setSpacing(6)

        # Portret: top-aligned. Landscape: gecentreerd. Gebruikt scherm-geometrie
        # direct omdat self.width()/height() tijdens __init__ nog Qt-default zijn.
        _screen = QApplication.primaryScreen().availableGeometry()
        _is_portrait = _screen.height() > _screen.width()
        _top_stretch = 0 if _is_portrait else 1
        main_lay.addStretch(_top_stretch)

        # Title
        title = QLabel(t("voucher_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 22, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        main_lay.addWidget(title)

        # Subtitle
        subtitle = QLabel(t("voucher_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 13))
        subtitle.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        main_lay.addWidget(subtitle)

        main_lay.addSpacing(12)

        # Code input
        self._voucher_input = QLineEdit()
        self._voucher_input.setPlaceholderText(t("voucher_placeholder"))
        self._voucher_input.setFont(QFont("DM Sans", 20, QFont.Bold))
        self._voucher_input.setMinimumHeight(54)
        self._voucher_input.setAlignment(Qt.AlignCenter)
        # Drie lagen bescherming tegen Windows TabTip auto-popup:
        # 1) WA_InputMethodEnabled=False  → Qt vertelt OS: geen IME nodig
        # 2) ImhSensitiveData hint        → flag 'gevoelige data' (Win negeert touch-kb)
        # 3) ImhNoPredictiveText etc.     → schakel alle IME features uit
        self._voucher_input.setAttribute(Qt.WA_InputMethodEnabled, False)
        self._voucher_input.setInputMethodHints(
            Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
        )
        # Read-only zodat OS geen "tekst-invoer modus" detecteert.
        # Onze custom keyboard gebruikt setText() programmatisch — dat negeert read-only.
        self._voucher_input.setReadOnly(True)
        self._voucher_input.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.1); color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px 12px; "
            f"letter-spacing: 2px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._voucher_input.mousePressEvent = lambda e: self._kill_windows_touch_keyboard()
        main_lay.addWidget(self._voucher_input)

        # Status label
        self._voucher_status = QLabel("")
        self._voucher_status.setAlignment(Qt.AlignCenter)
        self._voucher_status.setFont(QFont("DM Sans", 12))
        main_lay.addWidget(self._voucher_status)

        # Keyboard container — vereenvoudigde layout (geen shift/caps/tab/apostrofe/email)
        self._voucher_kb_container = QWidget()
        self._voucher_kb_layout = QVBoxLayout(self._voucher_kb_container)
        self._voucher_kb_layout.setContentsMargins(0, 0, 0, 0)
        self._build_voucher_keyboard()
        main_lay.addWidget(self._voucher_kb_container)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFont(QFont("DM Sans", 11))
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.08); color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px; }}"
            f"QPushButton:pressed {{ background: rgba(255,255,255,0.15); }}"
        )
        cancel_btn.clicked.connect(self._voucher_cancel)
        btn_row.addWidget(cancel_btn, stretch=1)

        go_btn = QPushButton(t("voucher_continue"))
        go_btn.setCursor(Qt.PointingHandCursor)
        go_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        go_btn.setFixedHeight(40)
        go_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        go_btn.clicked.connect(self._voucher_submit)
        btn_row.addWidget(go_btn, stretch=2)

        main_lay.addLayout(btn_row)

        # Bottom spacer
        main_lay.addStretch(1)

        self.stack.addWidget(page)

    def _build_voucher_keyboard(self):
        """Eenvoudig toetsenbord voor voucher-codes — alleen letters + cijfers + backspace."""
        self._voucher_kb_layout.setSpacing(4)
        kb_style = (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; "
            f"font-size: 18px; font-weight: bold; min-height: 50px; min-width: 40px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        special_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; "
            f"font-size: 14px; font-weight: bold; min-height: 50px; padding: 0 6px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY}; }}"
        )

        KEY_STRETCH = 4
        MODIFIER_STRETCH = 6

        # Cijferrij
        num_row = QHBoxLayout()
        num_row.setSpacing(4)
        for ch in "1234567890":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._voucher_key(c))
            num_row.addWidget(btn, KEY_STRETCH)
        self._voucher_kb_layout.addLayout(num_row)

        # QWERTY
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for ch in "QWERTYUIOP":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._voucher_key(c))
            row1.addWidget(btn, KEY_STRETCH)
        self._voucher_kb_layout.addLayout(row1)

        # ASDF (9 keys, halve toets ingerukt aan elke kant zoals echt QWERTY)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        row2.addStretch(2)
        for ch in "ASDFGHJKL":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._voucher_key(c))
            row2.addWidget(btn, KEY_STRETCH)
        row2.addStretch(2)
        self._voucher_kb_layout.addLayout(row2)

        # ZXCV + backspace (7 letters + backspace rechts)
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        row3.addStretch(MODIFIER_STRETCH)
        for ch in "ZXCVBNM":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._voucher_key(c))
            row3.addWidget(btn, KEY_STRETCH)
        back_btn = QPushButton("⌫")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(special_style)
        back_btn.clicked.connect(lambda: self._voucher_key("BACK"))
        row3.addWidget(back_btn, MODIFIER_STRETCH)
        self._voucher_kb_layout.addLayout(row3)

    def _voucher_key(self, key):
        """Handle keyboard key press in voucher input."""
        # Onderdruk Windows touch-toetsenbord
        self._kill_windows_touch_keyboard()
        if not hasattr(self, '_voucher_input'):
            return
        inp = self._voucher_input
        if key == "BACK":
            inp.setText(inp.text()[:-1])
        else:
            inp.setText(inp.text() + key)
        inp.setFocus()

    def _show_voucher_input(self):
        """Toon voucher-input scherm — gereset + focus op input."""
        if not hasattr(self, '_voucher_input'):
            return
        # Kill TabTip VOOR we focus geven — anders triggert het open-event 'm
        self._kill_windows_touch_keyboard()
        # Pause SumUp loop (parallel feature, mocht het toch lopen)
        if hasattr(self, '_sumup_loop') and self._sumup_loop:
            try:
                self._sumup_loop.pause()
            except Exception:
                pass
        # Check: zijn er überhaupt nog beschikbare codes?
        ev = self.active_event
        if ev:
            try:
                import voucher
                store = voucher.load_store(ev.id)
                if voucher.all_used(store["codes"]):
                    # Alle codes op — laat foutmelding zien op idle, geen input
                    self._voucher_input.setText("")
                    self._voucher_status.setText(t("voucher_all_used"))
                    self._voucher_status.setStyleSheet(f"color: {config.COLOR_DANGER};")
                    self.stack.setCurrentIndex(self.pages["voucher_input"])
                    return
            except Exception as e:
                print(f"[VOUCHER] Kon store niet laden: {e}")
        # Reset
        self._voucher_input.setText("")
        self._voucher_status.setText("")
        self.stack.setCurrentIndex(self.pages["voucher_input"])
        # Geef focus EN kill nog één keer voor de zekerheid (na de focus-event)
        self._voucher_input.setFocus()
        self._kill_windows_touch_keyboard()
        # Een kleine delayed kill voor het geval Windows TabTip async opstart
        QTimer.singleShot(100, self._kill_windows_touch_keyboard)
        QTimer.singleShot(500, self._kill_windows_touch_keyboard)

    def _voucher_cancel(self):
        """Annuleer voucher-input. Route hangt af van waar we vandaan kwamen:
          * default → idle (voucher-only modus, gast wil niet meer)
          * custom_payment → terug naar custom-betaalscherm
        """
        return_to = getattr(self, '_voucher_return_to', 'idle')
        # Reset zodat volgende sessie weer default-routing heeft
        self._voucher_return_to = 'idle'
        if return_to == "custom_payment":
            print("[VOUCHER] Cancel → terug naar custom-betaalscherm")
            self._show_custom_payment()
        else:
            self._go_idle()

    def _voucher_submit(self):
        """Valideer code, markeer als gebruikt.

        Vervolg-gedrag hangt af van context:
          * voucher-only modus (standaard) → start fotosessie
          * custom flow (foto's al gemaakt) → print + data-collection
        """
        import voucher
        ev = self.active_event
        if not ev:
            return
        code = self._voucher_input.text().strip()
        store = voucher.load_store(ev.id)
        ok, msg_key = voucher.validate(code, store["codes"])
        if not ok:
            # Toon foutmelding rood, blijf op scherm
            self._voucher_status.setText(t(msg_key))
            self._voucher_status.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return
        # Markeer als gebruikt
        if not voucher.mark_used(ev.id, code):
            self._voucher_status.setText(t("voucher_invalid"))
            self._voucher_status.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return
        # Geef positieve feedback
        self._voucher_status.setText(t("voucher_ok"))
        self._voucher_status.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        print(f"[VOUCHER] Code geredeemed: {code}")

        # Routing op basis van context. Veiligheidsklep: als _voucher_return_to
        # toevallig 'idle' is maar we staan WEL op State.CUSTOM_PAYMENT (foto's
        # al gemaakt), behandel als custom-flow. Voorkomt dat een race in de
        # state-reset de gebruiker terug naar templates stuurt i.p.v. naar
        # print+data-collection.
        return_to = getattr(self, '_voucher_return_to', 'idle')
        if return_to != "custom_payment" and self.state == State.CUSTOM_PAYMENT:
            print("[VOUCHER] _voucher_return_to was 'idle' maar state=CUSTOM_PAYMENT "
                  "— behandel als custom_payment (veiligheidsklep)")
            return_to = "custom_payment"
        self._voucher_return_to = 'idle'  # reset voor volgende sessie
        print(f"[VOUCHER] Submit succes, route='{return_to}' "
              f"(state={self.state}, strip_path={'JA' if self.strip_path else 'NEE'})")
        if return_to == "custom_payment":
            # Custom flow: foto's zijn al gemaakt — print + data-collection
            QTimer.singleShot(800, self._custom_payment_paid_success)
        else:
            # Standaard: start fotosessie
            QTimer.singleShot(800, self._go_select_template)

    # ── CUSTOM CHOICE PAGE ─────────────────────────────────────────
    def _build_custom_choice_page(self):
        """Keuzescherm voor custom flow: 2 tap-zones over een achtergrondafbeelding.

        Layout volledig overlay-based — twee onzichtbare buttons over de
        helften van het scherm. Geen tekst van ons; admin tekent zijn
        eigen visuele scheiding/labels op de achtergrond.

        Knoppen:
          * Optie A (links/boven): gratis digitaal → data-collection
          * Optie B (rechts/onder): betaald print → custom payment scherm
          * ✕ linksboven: terug naar idle
        """
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        # Geen QLayout — we positioneren widgets handmatig in resizeEvent
        page._is_custom_choice = True

        # Achtergrondafbeelding (label vult hele scherm)
        self._custom_choice_bg = QLabel(page)
        self._custom_choice_bg.setStyleSheet(f"background: {config.COLOR_BG};")
        self._custom_choice_bg.setAlignment(Qt.AlignCenter)
        self._custom_choice_bg.setScaledContents(False)
        self._custom_choice_bg.lower()  # achterste laag

        # Tap-zone A (links of boven). Volledig transparant.
        self._custom_choice_btn_a = QPushButton("", page)
        self._custom_choice_btn_a.setCursor(Qt.PointingHandCursor)
        self._custom_choice_btn_a.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self._custom_choice_btn_a.clicked.connect(self._custom_choose_a)

        # Tap-zone B (rechts of onder)
        self._custom_choice_btn_b = QPushButton("", page)
        self._custom_choice_btn_b.setCursor(Qt.PointingHandCursor)
        self._custom_choice_btn_b.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self._custom_choice_btn_b.clicked.connect(self._custom_choose_b)

        # ✕ knop linksboven (zelfde stijl als preview-page cancel)
        self._custom_choice_close_btn = QPushButton("✕", page)
        self._custom_choice_close_btn.setFixedSize(60, 60)
        self._custom_choice_close_btn.setFont(QFont("DM Sans", 24, QFont.Bold))
        self._custom_choice_close_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,0,0,0.5); color: white;"
            "  border: none; border-radius: 30px;"
            "}"
            "QPushButton:hover { background: rgba(0,0,0,0.7); }"
        )
        self._custom_choice_close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._custom_choice_close_btn.clicked.connect(self._custom_choice_cancel)
        self._custom_choice_close_btn.raise_()

        # Timer voor timeout
        self._custom_choice_timer = QTimer(self)
        self._custom_choice_timer.setSingleShot(True)
        self._custom_choice_timer.timeout.connect(self._custom_choice_timeout)

        # Geometry-update voor resize + show
        def _relayout():
            w, h = page.width(), page.height()
            if w <= 0 or h <= 0:
                return
            self._custom_choice_bg.setGeometry(0, 0, w, h)
            # Achtergrond pixmap herladen voor scaling
            self._custom_choice_apply_bg()
            # Tap-zones positioneren op basis van oriëntatie
            if h > w:
                # Portret: boven (A) / onder (B)
                self._custom_choice_btn_a.setGeometry(0, 0, w, h // 2)
                self._custom_choice_btn_b.setGeometry(0, h // 2, w, h - h // 2)
            else:
                # Landscape: links (A) / rechts (B)
                self._custom_choice_btn_a.setGeometry(0, 0, w // 2, h)
                self._custom_choice_btn_b.setGeometry(w // 2, 0, w - w // 2, h)
            # Close-knop linksboven met marge
            self._custom_choice_close_btn.move(20, 20)
            self._custom_choice_close_btn.raise_()

        page._relayout = _relayout
        original_resize = page.resizeEvent
        def _resize(event):
            if original_resize:
                original_resize(event)
            _relayout()
        page.resizeEvent = _resize

        self.stack.addWidget(page)
        self._custom_choice_page = page

    def _custom_choice_apply_bg(self):
        """Laad en schaal de keuzescherm-achtergrond uit het event-pad."""
        if not hasattr(self, '_custom_choice_bg'):
            return
        ev = self.active_event
        path = getattr(ev, 'custom_choice_bg_path', '') if ev else ''
        w = self._custom_choice_bg.width()
        h = self._custom_choice_bg.height()
        if path and os.path.isfile(path) and w > 0 and h > 0:
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self._custom_choice_bg.setPixmap(scaled)
                return
        # Fallback: lege grijze achtergrond
        self._custom_choice_bg.clear()
        self._custom_choice_bg.setStyleSheet(f"background: {config.COLOR_BG};")

    def _show_custom_choice(self):
        """Toon keuzescherm + start timeout-timer."""
        if not hasattr(self, '_custom_choice_page'):
            return
        # Reset event-flags zodat we vers in de custom-flow zitten.
        # _custom_flow_active wordt gezet bij _custom_choose_a/b.
        self._custom_flow_active = False
        self._custom_flow_paid_path = False
        self.state = State.CUSTOM_CHOICE
        self.stack.setCurrentIndex(self.pages["custom_choice"])
        # Force relayout + achtergrond opnieuw laden
        if hasattr(self._custom_choice_page, '_relayout'):
            self._custom_choice_page._relayout()
        # Timer starten met event-setting
        ev = self.active_event
        timeout_sec = getattr(ev, 'custom_choice_timeout', 30) if ev else 30
        self._custom_choice_timer.start(max(1, int(timeout_sec)) * 1000)
        print(f"[CUSTOM] Keuzescherm getoond (timeout {timeout_sec}s)")

    def _custom_choice_cancel(self):
        """Sluit-knop: timer stoppen, terug naar idle."""
        if self._custom_choice_timer.isActive():
            self._custom_choice_timer.stop()
        print("[CUSTOM] Keuzescherm geannuleerd via ✕")
        self._go_idle()

    def _custom_choice_timeout(self):
        """Timeout: terug naar idle."""
        print("[CUSTOM] Keuzescherm timeout — terug naar idle")
        self._go_idle()

    def _custom_choose_a(self):
        """Optie A: gratis digitaal → data-collection flow."""
        if self._custom_choice_timer.isActive():
            self._custom_choice_timer.stop()
        print("[CUSTOM] Optie A gekozen (gratis digitaal)")
        # Markeer dat we in custom-flow zitten, voor post-save routing
        self._custom_flow_active = True
        self._custom_flow_paid_path = False
        # Open data-collection scherm (post-foto, gratis variant)
        self._show_data_collection(timing_context="after")

    def _custom_choose_b(self):
        """Optie B: betaald print → custom payment scherm."""
        if self._custom_choice_timer.isActive():
            self._custom_choice_timer.stop()
        print("[CUSTOM] Optie B gekozen (betaald print)")
        self._custom_flow_active = True
        self._custom_flow_paid_path = True
        self._show_custom_payment()

    # ── CUSTOM PAYMENT PAGE ─────────────────────────────────────────
    def _build_custom_payment_page(self):
        """Betaalscherm voor custom flow: achtergrond + Stripe QR + voucher-knop.

        Layout:
          * Vol-scherm achtergrondafbeelding (event-config)
          * QR-code 250×250 centraal
          * "Ik heb een vouchercode" knop onder QR
          * ✕ linksboven → terug naar keuzescherm
          * Timer + Stripe-polling op de achtergrond
        """
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        page._is_custom_payment = True

        # Achtergrond
        self._custom_payment_bg = QLabel(page)
        self._custom_payment_bg.setAlignment(Qt.AlignCenter)
        self._custom_payment_bg.setStyleSheet(f"background: {config.COLOR_BG};")
        self._custom_payment_bg.lower()

        # QR code label (witte achtergrond zodat hij scherp leesbaar is op willekeurige bg)
        self._custom_payment_qr = QLabel(page)
        self._custom_payment_qr.setFixedSize(280, 280)
        self._custom_payment_qr.setAlignment(Qt.AlignCenter)
        self._custom_payment_qr.setStyleSheet(
            "background: white; border-radius: 12px; padding: 12px;"
        )

        # Voucher knop
        self._custom_payment_voucher_btn = QPushButton(t("custom_have_voucher"), page)
        self._custom_payment_voucher_btn.setCursor(Qt.PointingHandCursor)
        self._custom_payment_voucher_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        self._custom_payment_voucher_btn.setMinimumSize(280, 50)
        self._custom_payment_voucher_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 10px 20px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        self._custom_payment_voucher_btn.clicked.connect(self._custom_payment_open_voucher)

        # ✕ knop linksboven
        self._custom_payment_close_btn = QPushButton("✕", page)
        self._custom_payment_close_btn.setFixedSize(60, 60)
        self._custom_payment_close_btn.setFont(QFont("DM Sans", 24, QFont.Bold))
        self._custom_payment_close_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,0,0,0.5); color: white;"
            "  border: none; border-radius: 30px;"
            "}"
            "QPushButton:hover { background: rgba(0,0,0,0.7); }"
        )
        self._custom_payment_close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._custom_payment_close_btn.clicked.connect(self._custom_payment_cancel)

        # Timer (timeout terug naar keuzescherm)
        self._custom_payment_timer = QTimer(self)
        self._custom_payment_timer.setSingleShot(True)
        self._custom_payment_timer.timeout.connect(self._custom_payment_timeout)

        # Polling-timer voor Stripe (3 sec)
        self._custom_payment_poll_timer = QTimer(self)
        self._custom_payment_poll_timer.timeout.connect(self._custom_payment_poll)

        # Geometry helper
        def _relayout():
            w, h = page.width(), page.height()
            if w <= 0 or h <= 0:
                return
            self._custom_payment_bg.setGeometry(0, 0, w, h)
            self._custom_payment_apply_bg()
            # QR + voucher knop centraal
            qr_w, qr_h = 280, 280
            qr_x = (w - qr_w) // 2
            qr_y = (h - qr_h) // 2 - 40   # iets boven het midden zodat voucher-knop eronder past
            self._custom_payment_qr.setGeometry(qr_x, qr_y, qr_w, qr_h)
            # Voucher knop onder de QR, centraal
            btn = self._custom_payment_voucher_btn
            btn_w = max(280, btn.sizeHint().width())
            btn_h = 50
            btn.setGeometry((w - btn_w) // 2, qr_y + qr_h + 20, btn_w, btn_h)
            # ✕ linksboven
            self._custom_payment_close_btn.move(20, 20)
            self._custom_payment_close_btn.raise_()
            self._custom_payment_qr.raise_()
            self._custom_payment_voucher_btn.raise_()

        page._relayout = _relayout
        original_resize = page.resizeEvent
        def _resize(event):
            if original_resize:
                original_resize(event)
            _relayout()
        page.resizeEvent = _resize

        self.stack.addWidget(page)
        self._custom_payment_page = page

    def _custom_payment_apply_bg(self):
        """Laad + schaal de betaalscherm-achtergrond."""
        if not hasattr(self, '_custom_payment_bg'):
            return
        ev = self.active_event
        path = getattr(ev, 'custom_payment_bg_path', '') if ev else ''
        w = self._custom_payment_bg.width()
        h = self._custom_payment_bg.height()
        if path and os.path.isfile(path) and w > 0 and h > 0:
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self._custom_payment_bg.setPixmap(scaled)
                return
        self._custom_payment_bg.clear()

    def _custom_payment_render_qr(self):
        """Genereer Stripe-QR pixmap voor de huidige payment_link_url."""
        user, _ = auth.load_session()
        payment_url = user.get("payment_link_url", "") if user else ""
        if not payment_url:
            self._custom_payment_qr.setText("Geen\nStripe-link\ngeconfigureerd")
            self._custom_payment_qr.setStyleSheet(
                f"background: white; color: {config.COLOR_DANGER}; "
                f"border-radius: 12px; padding: 12px; font-weight: bold;"
            )
            return False
        try:
            import qrcode
            from io import BytesIO
            qr = qrcode.QRCode(version=1, box_size=8, border=2)
            qr.add_data(payment_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            qr_img.save(buf, format="PNG")
            buf.seek(0)
            pix = QPixmap()
            pix.loadFromData(buf.read())
            scaled = pix.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._custom_payment_qr.setPixmap(scaled)
            self._custom_payment_qr.setStyleSheet(
                "background: white; border-radius: 12px; padding: 12px;"
            )
            return True
        except Exception as e:
            print(f"[CUSTOM] QR generatie fout: {e}")
            self._custom_payment_qr.setText("QR fout")
            return False

    def _show_custom_payment(self):
        """Toon betaalscherm + start beide timers (timeout + polling)."""
        if not hasattr(self, '_custom_payment_page'):
            return
        self.state = State.CUSTOM_PAYMENT
        self.stack.setCurrentIndex(self.pages["custom_payment"])
        if hasattr(self._custom_payment_page, '_relayout'):
            self._custom_payment_page._relayout()
        self._custom_payment_render_qr()
        # Reset eventuele oude poll-state
        self._custom_payment_polling_active = False
        # Start polling (elke 3s) + timeout
        self._custom_payment_poll_timer.start(3000)
        ev = self.active_event
        timeout_sec = getattr(ev, 'custom_payment_timeout', 120) if ev else 120
        self._custom_payment_timer.start(max(1, int(timeout_sec)) * 1000)
        print(f"[CUSTOM] Betaalscherm getoond (timeout {timeout_sec}s, polling actief)")

    def _custom_payment_cancel(self):
        """✕ knop: stop alle timers, terug naar keuzescherm."""
        self._custom_payment_stop_timers()
        print("[CUSTOM] Betaalscherm geannuleerd via ✕")
        self._show_custom_choice()

    def _custom_payment_timeout(self):
        """Timeout: stop polling, terug naar keuzescherm."""
        self._custom_payment_stop_timers()
        print("[CUSTOM] Betaalscherm timeout — terug naar keuzescherm")
        self._show_custom_choice()

    def _custom_payment_stop_timers(self):
        """Stop alle custom payment timers (idempotent)."""
        if hasattr(self, '_custom_payment_timer') and self._custom_payment_timer.isActive():
            self._custom_payment_timer.stop()
        if hasattr(self, '_custom_payment_poll_timer') and self._custom_payment_poll_timer.isActive():
            self._custom_payment_poll_timer.stop()

    def _custom_payment_open_voucher(self):
        """Open voucher-input scherm vanuit custom payment (terug-route is custom_payment).

        Beide timers worden gestopt — bij Cancel uit voucher-input wordt
        _show_custom_payment opnieuw aangeroepen en starten ze opnieuw.
        Voorkomt race waarbij Stripe-betaling binnenkomt terwijl gast typt.
        """
        self._custom_payment_stop_timers()
        # Markeer waar de voucher-input vandaan komt voor Cancel-routing
        self._voucher_return_to = "custom_payment"
        self._show_voucher_input()

    def _custom_payment_poll(self):
        """Poll Supabase Edge Function check-sessions voor Stripe-betaling.

        Wordt elke 3s aangeroepen door _custom_payment_poll_timer.
        Bij detectie van een voltooide betaling: stop timers + start
        de paid-success flow (print + data-collection).
        """
        if self.state != State.CUSTOM_PAYMENT:
            return
        if getattr(self, '_custom_payment_polling_active', False):
            return
        user = self._cached_user
        booth_id = user.get("booth_secret", "") if user else ""
        if not booth_id:
            return
        self._custom_payment_polling_active = True

        def _do_poll():
            import urllib.request
            import json as _json
            try:
                url = f"{config.SUPABASE_URL}/functions/v1/check-sessions?booth_id={booth_id}"
                req = urllib.request.Request(url, headers={
                    "apikey": config.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
                }, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                # Detectie: triggeren als ER ÜBERHAUPT EEN INDICATIE is dat er
                # een betaling binnen is. De Supabase function geeft mogelijk
                # alleen start_session=true terug (zonder pending count) bij
                # een "pay-for-existing-strip" flow, want er hoeft geen nieuwe
                # fotosessie te starten. We behandelen ALLE truthy signalen
                # als "betaling binnen". Voor de IDLE-poller (los) blijft de
                # 'pending'-count gevolgd zoals voorheen.
                pending_count = int(data.get("pending", 0) or 0)
                start_flag = bool(data.get("start_session"))
                if start_flag and pending_count == 0:
                    # start_session=true maar geen pending counter → behandel
                    # als minimaal 1 pending betaling (anders mist detectie).
                    pending_count = 1
                self._custom_payment_poll_result = pending_count
                # Diagnose-log per poll (klein, alleen bij truthy om spam te
                # beperken). Helpt om te zien wat de server precies returnt
                # als de detectie ooit niet triggert.
                if start_flag or pending_count:
                    keys = sorted(list(data.keys()))[:8]
                    print(f"[CUSTOM] Stripe poll: start={start_flag} "
                          f"pending={pending_count} keys={keys}")
            except Exception as ex:
                print(f"[CUSTOM] Stripe poll fout (genegeerd): {ex}")
                self._custom_payment_poll_result = 0
            self._custom_payment_polling_active = False

        self._custom_payment_poll_result = 0
        threading.Thread(target=_do_poll, daemon=True).start()
        QTimer.singleShot(300, self._custom_payment_check_poll_result)

    def _custom_payment_check_poll_result(self):
        """Async-result handler — wacht max 6s op de poll-thread."""
        if getattr(self, '_custom_payment_polling_active', False):
            if not hasattr(self, '_custom_payment_poll_wait'):
                self._custom_payment_poll_wait = 0
            self._custom_payment_poll_wait += 1
            if self._custom_payment_poll_wait < 20:  # max ~6s wachten
                QTimer.singleShot(300, self._custom_payment_check_poll_result)
                return
            self._custom_payment_polling_active = False
            self._custom_payment_poll_wait = 0
            return
        self._custom_payment_poll_wait = 0
        pending = getattr(self, '_custom_payment_poll_result', 0)
        if pending > 0 and self.state == State.CUSTOM_PAYMENT:
            print(f"[CUSTOM] Stripe-betaling gedetecteerd ({pending} pending) — start paid flow")
            self._custom_payment_stop_timers()
            self._custom_payment_paid_success()

    def _custom_payment_paid_success(self):
        """Stripe-betaling of voucher gelukt: print verzenden + data-collection.

        Wordt aangeroepen vanuit:
          * _custom_payment_check_poll_result (Stripe-betaling gedetecteerd)
          * _voucher_submit via QTimer (vouchercode geredeemed in custom-flow)
          * _go_back_to_payment_after_dc (returning from DC pre-form)
        """
        print(f"[CUSTOM] _custom_payment_paid_success aangeroepen "
              f"(state={self.state}, strip_path={'JA' if self.strip_path else 'NEE'})")
        # EERST de print versturen
        if getattr(self, 'strip_path', None):
            try:
                copies = 1
                ev = self.active_event
                if ev:
                    copies = max(1, int(getattr(ev, 'auto_print_copies', 1) or 1))
                print(f"[CUSTOM] Print verzenden ({copies}× {self.strip_path})")
                self._do_print_job(copies=copies)
            except Exception as ex:
                print(f"[CUSTOM] Print verzenden mislukt: {ex}")
        else:
            print("[CUSTOM] Geen strip_path — print overgeslagen")
        # Direct daarna naar data-collection (voor digitale variant + mail)
        self._custom_flow_paid_path = True
        print("[CUSTOM] Plannen overgang naar data-collection (400ms)")
        QTimer.singleShot(400, lambda: self._show_data_collection(timing_context="after"))

    def _dc_skip(self):
        """Skip data collection (only for 'after' context — achteraf optioneel)."""
        self._back_to_sharing()

    def _dc_cancel(self):
        """Cancel data collection and end the session."""
        # Custom flow: bij cancel altijd naar idle (geen sharing-scherm aanwezig)
        if getattr(self, '_custom_flow_active', False):
            self._custom_flow_active = False
            self._custom_flow_paid_path = False
            self._go_idle()
            return
        if self._dc_timing_context == "before":
            self._go_idle()
        else:
            self._back_to_sharing()

    def _dc_submit(self):
        """Submit the data collection form."""
        import re
        ev = self.active_event

        # Collect data
        contact_data = {}
        for key, inp in self._dc_inputs.items():
            contact_data[key] = inp.text().strip()

        # Validate email if present
        email = contact_data.get("email", "")
        if "email" in self._dc_inputs and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            self._dc_status.setText(t("dc_invalid_email"))
            self._dc_status.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return

        # Save to CSV
        self._save_contact_to_csv(contact_data)

        # Store for auto-email after capture
        self._dc_collected_data = contact_data

        if self._dc_timing_context == "before":
            # Continue to capture
            self._go_direct_capture()
        else:
            # Auto-send email if er een mail-adres is. In custom-flow MOET de
            # mail altijd verstuurd worden (data_collect_auto_email is dan niet
            # relevant — de hele custom-flow draait om mail-versturen).
            in_custom = getattr(self, '_custom_flow_active', False)
            if email and (in_custom or getattr(ev, 'data_collect_auto_email', True)):
                self._auto_send_email(email)
                self._show_email_toast(t("email_sent"), success=True)
            if in_custom:
                # Custom flow: na save niet terug naar sharing maar naar idle.
                self._custom_flow_active = False
                self._custom_flow_paid_path = False
                # Korte vertraging zodat de email-thread kan starten + toast zichtbaar is
                QTimer.singleShot(1500, self._go_idle)
            else:
                self._back_to_sharing()

    def _auto_send_email(self, to_email):
        """Automatically send photos to the collected email address."""
        try:
            from email_sender import load_gmail_config, EmailThread

            gmail = load_gmail_config()
            if not gmail:
                print("[EMAIL] Geen Gmail gekoppeld — auto-email overgeslagen")
                return

            ev = self.active_event
            attachments = []
            if (not ev or ev.email_send_strip) and self.strip_path:
                # Use single strip if sharing single strip is enabled.
                # display_*-paden geven gedraaide versie als template alle
                # frames 90/270 heeft, anders het origineel.
                share_strip = self.display_single_strip_path or self.display_strip_path
                attachments.append(share_strip)
            if ev and ev.email_send_originals:
                for p in self.photos:
                    if p and os.path.exists(p):
                        attachments.append(p)
            if (not ev or ev.email_send_gif) and getattr(self, '_boomerang_path', None):
                attachments.append(self._boomerang_path)

            subject = ev.email_subject if ev and ev.email_subject else None
            body = ev.email_body if ev and ev.email_body else None

            self._email_thread = EmailThread(to_email, attachments,
                                             subject=subject, body=body)
            self._email_thread.email_sent.connect(self._on_email_sent)
            self._email_thread.email_failed.connect(self._on_email_failed)
            self._email_thread.start()
            print(f"[EMAIL] Auto-verzenden naar {to_email}")
        except Exception as e:
            print(f"[EMAIL] Auto-email fout: {e}")

    # --- EMAIL INPUT ---
    def _build_email_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 15, 20, 10)
        lay.setSpacing(8)

        lay.addWidget(self._make_title(t("btn_email"), 32))

        # Email input field - wide enough for long addresses
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText(t("placeholder_email_example"))
        self.email_input.setFont(QFont("DM Sans", 24))
        self.email_input.setAlignment(Qt.AlignCenter)
        self.email_input.setMinimumHeight(65)
        self.email_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 3px solid {config.COLOR_BORDER}; "
            f"border-radius: 15px; padding: 10px 25px; color: {config.COLOR_TEXT}; "
            f"font-size: 24px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        lay.addWidget(self.email_input)

        # Status label for feedback
        self.email_status_label = QLabel("")
        self.email_status_label.setAlignment(Qt.AlignCenter)
        self.email_status_label.setFont(QFont("DM Sans", 14))
        self.email_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        lay.addWidget(self.email_status_label, alignment=Qt.AlignCenter)

        # On-screen keyboard for touch kiosk
        keyboard_widget = self._build_touch_keyboard()
        lay.addWidget(keyboard_widget)

        # Action buttons
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(30)
        skip_btn = self._make_button("OVERSLAAN", self._skip_email, "secondaryBtn")
        self.email_send_btn = self._make_button("VERSTUREN", self._send_email, "successBtn")
        self.email_send_btn.setMinimumSize(250, 70)
        btn_lay.addWidget(skip_btn)
        btn_lay.addStretch()
        btn_lay.addWidget(self.email_send_btn)
        lay.addLayout(btn_lay)

        self.stack.addWidget(page)

    def _build_touch_keyboard(self):
        """Build on-screen keyboard for email input (same style as TextInputDialog)."""
        keyboard = QWidget()
        kb_lay = QVBoxLayout(keyboard)
        kb_lay.setSpacing(4)
        kb_lay.setContentsMargins(0, 5, 0, 0)

        kb_style = (
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; "
            f"font-size: 18px; font-weight: bold; min-height: 50px; min-width: 40px; padding: 0; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        special_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; "
            f"font-size: 16px; font-weight: bold; min-height: 50px; padding: 0 8px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY}; }}"
        )

        self._kb_caps = False

        # QWERTY layout met echte toetsenbord-uitlijning (zie TextInputDialog).
        KEY_STRETCH = 4
        rows = [
            ("QWERTYUIOP", 0, 0),
            ("ASDFGHJKL",  2, 2),
            ("ZXCVBNM",    6, 6),
        ]
        for row_chars, left_pad, right_pad in rows:
            row_lay = QHBoxLayout()
            row_lay.setSpacing(4)
            if left_pad:
                row_lay.addStretch(left_pad)
            for ch in row_chars:
                btn = QPushButton(ch)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(kb_style)
                btn.clicked.connect(lambda _, c=ch: self._on_keyboard_key(c.lower()))
                row_lay.addWidget(btn, KEY_STRETCH)
            if right_pad:
                row_lay.addStretch(right_pad)
            kb_lay.addLayout(row_lay)

        # Cijferrij — uitgelijnd onder rij 1
        num_row = QHBoxLayout()
        num_row.setSpacing(4)
        for ch in "0123456789":
            btn = QPushButton(ch)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(kb_style)
            btn.clicked.connect(lambda _, c=ch: self._on_keyboard_key(c))
            num_row.addWidget(btn, KEY_STRETCH)
        kb_lay.addLayout(num_row)

        # Space + backspace
        space_row = QHBoxLayout()
        space_row.setSpacing(6)
        space_btn = QPushButton(t("key_space").upper())
        space_btn.setCursor(Qt.PointingHandCursor)
        space_btn.setStyleSheet(kb_style)
        space_btn.clicked.connect(lambda: self._on_keyboard_key(" "))
        space_row.addWidget(space_btn, stretch=3)
        back_btn = QPushButton("\u232b")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(special_style)
        back_btn.clicked.connect(lambda: self._on_keyboard_key("\u232b"))
        space_row.addWidget(back_btn, stretch=1)
        kb_lay.addLayout(space_row)

        # Email row — vaste basis (@ . - _) plus TLD-knoppen dynamisch
        # op basis van actieve taal
        email_row = QHBoxLayout()
        email_row.setSpacing(4)
        email_keys = [("@", "@"), (".", "."), ("-", "-"), ("_", "_")]
        email_keys += [(tld, tld) for tld in self._get_email_tlds()]
        for key, label in email_keys:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(special_style)
            btn.clicked.connect(lambda _, k=key: self._on_keyboard_key(k))
            email_row.addWidget(btn)
        kb_lay.addLayout(email_row)

        return keyboard

    def _on_keyboard_key(self, key):
        """Handle on-screen keyboard key press."""
        if key == "\u232b":
            self.email_input.setText(self.email_input.text()[:-1])
        else:
            self.email_input.setText(self.email_input.text() + key)


    # --- DONE ---
    def _build_done_page(self):
        page = QWidget()
        page.setStyleSheet("background: #1a1a1a;")
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(20)
        lay.addStretch()
        thanks = QLabel(t("thanks"))
        thanks.setAlignment(Qt.AlignCenter)
        thanks.setFont(QFont("DM Sans", 72, QFont.Bold))
        thanks.setStyleSheet("color: white;")
        lay.addWidget(thanks)
        subtitle = QLabel(t("thanks_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 22))
        subtitle.setStyleSheet("color: #888888;")
        lay.addWidget(subtitle)
        lay.addStretch()
        self.stack.addWidget(page)

    # --- ERROR ---
    def _build_error_page(self):
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        main_lay = QVBoxLayout(page)
        main_lay.setContentsMargins(0, 0, 0, 0)

        # Content area centered
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(20)
        lay.addStretch()

        # Error icon
        icon_label = QLabel("\u26a0\ufe0f")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 40px;")
        lay.addWidget(icon_label)

        err_title = QLabel(t("error_title"))
        err_title.setAlignment(Qt.AlignCenter)
        err_title.setFont(QFont("DM Sans", 18, QFont.Bold))
        err_title.setStyleSheet("color: #222222; font-size: 18px;")
        lay.addWidget(err_title)

        self.error_message = QLabel(t("error_default"))
        self.error_message.setAlignment(Qt.AlignCenter)
        self.error_message.setWordWrap(True)
        self.error_message.setMaximumWidth(min(600, self.width() - 60) if self.width() > 0 else 600)
        self.error_message.setFont(QFont("DM Sans", 12))
        self.error_message.setStyleSheet("color: #444444; font-size: 12px;")
        lay.addWidget(self.error_message, alignment=Qt.AlignCenter)

        lay.addSpacing(8)

        # Buttons row
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(12)
        btn_lay.addStretch()
        back_btn = QPushButton(t("back"))
        back_btn.clicked.connect(self._go_idle)
        back_btn.setCursor(QCursor(Qt.PointingHandCursor))
        back_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        back_btn.setMinimumSize(200, 52)
        back_btn.setStyleSheet(
            "QPushButton { background: #333333; color: white; border-radius: 12px; "
            "padding: 12px 28px; font-size: 13px; }"
            "QPushButton:hover { background: #555555; }"
        )
        btn_lay.addWidget(back_btn)
        btn_lay.addStretch()
        lay.addLayout(btn_lay)

        lay.addStretch()
        main_lay.addWidget(content)

        # Lock icon bottom-right to go to settings
        lock_btn = QPushButton("🔒")
        lock_btn.setFont(QFont("DM Sans", 20))
        lock_btn.setFixedSize(60, 60)
        lock_btn.setCursor(Qt.PointingHandCursor)
        lock_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {config.COLOR_TEXT_DIM}; }}"
            f"QPushButton:hover {{ color: {config.COLOR_PRIMARY}; }}"
        )
        lock_btn.clicked.connect(self._go_settings)
        lock_btn.setParent(page)
        # Position will be set in resizeEvent-like approach
        self._error_lock_btn = lock_btn

        self.stack.addWidget(page)

    def _retry_from_error(self):
        """Retry: go back to idle, camera reconnects automatically on next use."""
        print("[UI] Retry from error — terug naar idle")
        if hasattr(self, '_recovery_timer'):
            self._recovery_timer.stop()
        self._auto_reconnect_attempts = 0
        self._go_idle()

    # ── State Transitions ─────────────────────

    # _rebuild_idle_page is defined later in the class (single canonical version)

    def _go_idle(self):
        print(f"[UI] _go_idle aangeroepen (was state={self.state})", flush=True)
        # Resume DNP-poll bij terugkeer naar idle (UI-Automation focus-steal
        # is alleen risico wanneer er pc-input wordt gegeven).
        self._pause_dnp_poll(False)
        # FLUSH: als de pakket-delay nog loopt op het moment dat de sessie
        # eindigt (countdown afgelopen, data-collect geannuleerd, custom
        # flow klaar) dan moet de print alsnog DIRECT verstuurd worden —
        # de gast heeft erom gevraagd (en er mogelijk voor betaald).
        # Vroeger werd de timer hier stilletjes gestopt = print kwijt.
        # NB: ná _pause_dnp_poll(False) — _actually_send_print pauzeert de
        # poller zelf opnieuw voor de duur van de print.
        try:
            timer = getattr(self, '_inline_print_delay_timer', None)
            if timer is not None and timer.isActive():
                copies = getattr(self, '_inline_print_copies', 1)
                timer.stop()
                print(f"[PRINTER] Sessie eindigt tijdens pakket-delay — "
                      f"print ({copies}x) wordt nu direct verstuurd")
                self._actually_send_print(copies)
        except Exception as e:
            print(f"[PRINTER] Delay-flush fout: {e}")
        # Fout ontstaan TIJDENS de sessie? De status-callback vuurt alleen
        # bij veranderingen, dus als de fout blijft bestaan komt er geen
        # nieuwe trigger meer — toon de overlay nu alsnog. Alleen bij een
        # gekoppeld event: op het welcome/QR-scherm geen printer-meldingen.
        try:
            st = getattr(self, '_dnp_last_status', None)
            ev_chk = self.active_event
            coupled = bool(ev_chk and getattr(ev_chk, 'linked_booking_id', ''))
            if (coupled and self.effective_print_enabled
                    and st is not None and st.is_blocking()
                    and self._dnp_error_overlay is None):
                from dnp_status import StatusLevel
                if (st.level == StatusLevel.ERROR
                        or (not st.connected and st.level != StatusLevel.UNKNOWN)):
                    print("[DNP-STATUS] Blokkerende fout uit sessie — "
                          "overlay alsnog tonen op idle")
                    self._show_dnp_error_overlay(st)
        except Exception as e:
            print(f"[DNP-STATUS] Overlay-check bij idle fout: {e}")
        # Reset pending-print state — vorige sessie is afgelopen
        self._pending_print_copies = None
        # Cleanup inline print-delay widgets + timer + audio
        try:
            self._stop_printer_busy_sound()
        except Exception:
            pass
        try:
            self._cleanup_inline_print_widgets()
        except Exception:
            pass
        # Cleanup eventuele open print-delay overlay (legacy — voor backup
        # mocht code-path nog ergens triggeren)
        if hasattr(self, '_print_delay_overlay') and self._print_delay_overlay is not None:
            try:
                self._hide_print_delay_overlay()
            except Exception:
                pass
        # Custom flow opruimen (idempotent — geen effect als niet actief)
        self._custom_flow_active = False
        self._custom_flow_paid_path = False
        self._voucher_return_to = 'idle'
        for attr in ('_custom_choice_timer', '_custom_payment_timer',
                     '_custom_payment_poll_timer'):
            timer = getattr(self, attr, None)
            if timer is not None and timer.isActive():
                timer.stop()
        self.state = State.IDLE
        self._stop_live_view()
        self._reset_countdown_ui()
        self.review_timer.stop()
        self.done_timer.stop()
        self.photos = []
        self.current_photo_num = 0
        self.strip_path = None
        self._single_strip_path = None
        self._display_strip_path = None
        self._display_single_strip_path = None
        self.selected_template = None
        self._strip_bg = None
        self._processed_photos = []
        self._photo_filters = {}
        self.session_id = None
        self._capture_search_folders = None
        self._capture_existing_files = None
        self._boomerang_path = None
        self._boomerang_frames = None
        self._session_prints_used = 0
        self._qr_ready = False
        if hasattr(self, '_qr_overlay'):
            self._qr_overlay.hide()
        if hasattr(self, '_qr_spinner_timer'):
            self._stop_qr_spinner()
        if self._frame_buffer:
            self._frame_buffer.clear()
        self._email_thread = None
        # Free cached pixmaps and images to reduce memory
        self._last_live_pixmap = None
        self._capture_screen_pixmap = None
        QPixmapCache.clear()  # Clear Qt's internal pixmap cache
        gc.collect()  # Force garbage collection after session cleanup

        # Routing: bij geen gekoppeld event → welcome page (taal/wifi/QR setup);
        # anders normale idle (tap to start).
        ev = self.active_event
        no_booking = not ev or not getattr(ev, 'linked_booking_id', '')
        if no_booking and "welcome" in self.pages:
            # Start in CHECKING state — spinner-card terwijl we 3 pings doen
            if hasattr(self, '_welcome_action_stack'):
                self._welcome_state = 'checking'
                self._welcome_check_results = []
                self._welcome_consecutive_failures = 0
                self._welcome_consecutive_successes = 0
                self._has_internet = None
                self._welcome_action_stack.setCurrentIndex(2)
                # Spinner animatie aan
                if hasattr(self, '_welcome_spinner_timer'):
                    self._welcome_spinner_timer.start()
            self.stack.setCurrentIndex(self.pages["welcome"])
            self._refresh_welcome_serial()
            # Start ping-check direct (eerste van de 3) + timer voor follow-ups
            if hasattr(self, '_welcome_wifi_timer'):
                self._welcome_check_connectivity()
                self._welcome_wifi_timer.start()
            # Sync active language to button highlight
            try:
                from translations import get_language
                current = get_language()
                for code, btn in self._welcome_lang_btns.items():
                    btn.setStyleSheet(self._welcome_lang_btn_style(active=(code == current)))
            except Exception:
                pass
            print("[UI] Welcome page getoond (geen event gekoppeld) — state=CHECKING")
            return

        # Stop wifi-timer + spinner als we naar normale idle gaan
        if hasattr(self, '_welcome_wifi_timer') and self._welcome_wifi_timer.isActive():
            self._welcome_wifi_timer.stop()
        if hasattr(self, '_welcome_spinner_timer') and self._welcome_spinner_timer.isActive():
            self._welcome_spinner_timer.stop()

        self.stack.setCurrentIndex(self.pages["idle"])
        self._update_status()
        # Position lock button in bottom-right corner
        if hasattr(self, '_idle_lock_btn'):
            self._idle_lock_btn.show()
            self._idle_lock_btn.raise_()
            # Delay positioning until page is laid out (state is already IDLE
            # so resizeEvent will also call _position_idle_lock)
            QTimer.singleShot(150, self._position_idle_lock)
        # Wifi-tip popup: 2-sec polling, start nu en doe meteen 1e check
        if hasattr(self, '_idle_wifi_check_timer'):
            QTimer.singleShot(200, self._position_idle_wifi_tip)
            self._idle_wifi_check_tick()
            if not self._idle_wifi_check_timer.isActive():
                self._idle_wifi_check_timer.start()
        # Serienummer linksonderin (zoals ingesteld in Geavanceerd), op de plek
        # waar vroeger "Event: ..." stond. Bij lage schijfruimte komt de
        # waarschuwing erachter.
        parts = []
        sn = (self.serial_number or "").strip()
        if sn:
            parts.append(sn)
        try:
            import shutil
            disk = shutil.disk_usage(config.PHOTO_DIR)
            free_gb = disk.free / (1024 ** 3)
            if free_gb < 10.0:
                parts.append(f"\u26a0 Schijfruimte: {free_gb:.1f} GB vrij")
        except Exception:
            pass
        self.status_label.setText("   \u00b7   ".join(parts))
        # Start payment polling if enabled
        if self.active_event and self.active_event.payment_enabled:
            self._start_payment_polling()
        elif self.active_event and getattr(self.active_event, 'sumup_enabled', False):
            self._start_sumup_always_on()
        else:
            self._stop_payment_polling()
            self._stop_sumup_always_on()
        # Auto-start next session if payment queue has pending
        if getattr(self, '_payment_queue', 0) > 0:
            self._payment_queue -= 1
            print(f"[PAYMENT] Volgende sessie uit wachtrij ({self._payment_queue} resterend)")
            QTimer.singleShot(2000, self._go_select_template)

    def _find_template_by_name(self, name, _retry=True):
        """Find a template by name with retry logic for transient file locks.

        Prioritizes custom templates (with background) over presets.
        Retries once after 100ms if JSON load fails transiently.
        """
        import time as _time
        presets = get_preset_layouts()
        custom = list_templates(config.TEMPLATES_DIR, config.BACKGROUNDS_DIR)

        # Search custom templates first (they may have backgrounds)
        custom_match = None
        preset_match = None
        for t in custom:
            if t.name == name:
                custom_match = t
        for t in presets:
            if t.name == name:
                preset_match = t

        # Prefer custom (with background) over preset
        if custom_match:
            if custom_match.background_path:
                print(f"[TEMPLATE] Gevonden: custom '{name}' met achtergrond")
                return custom_match
            # Custom exists but no bg — check if there's a JSON on disk
            # that might have a bg (maybe load failed silently)
            if _retry and os.path.isdir(config.TEMPLATES_DIR):
                import json as _json
                for fname in os.listdir(config.TEMPLATES_DIR):
                    if fname.lower().endswith(".json"):
                        fpath = os.path.join(config.TEMPLATES_DIR, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                data = _json.load(f)
                            if data.get("name") == name and data.get("background_path"):
                                # JSON has a bg but template load lost it — retry
                                print(f"[TEMPLATE] JSON heeft achtergrond maar load miste het — retry")
                                return self._find_template_by_name(name, _retry=False)
                        except Exception:
                            pass
            return custom_match

        if preset_match:
            # Check if there's a custom JSON on disk that failed to load
            if _retry and os.path.isdir(config.TEMPLATES_DIR):
                import json as _json
                for fname in os.listdir(config.TEMPLATES_DIR):
                    if fname.lower().endswith(".json"):
                        fpath = os.path.join(config.TEMPLATES_DIR, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                data = _json.load(f)
                            if data.get("name") == name and data.get("background_path"):
                                print(f"[TEMPLATE] Custom JSON met achtergrond gevonden maar niet geladen — retry")
                                return self._find_template_by_name(name, _retry=False)
                        except Exception:
                            pass
            print(f"[TEMPLATE] Gevonden: preset '{name}' (geen achtergrond)")
            return preset_match

        print(f"[TEMPLATE] NIET gevonden: '{name}'")
        return None

    def _go_select_template(self):
        """Show template selection screen, or skip directly to capture if event has a template."""
        # Guard: only start from idle state
        if self.state != State.IDLE:
            print(f"[UI] _go_select_template genegeerd (state={self.state})")
            return
        pos = self.pos()
        print(
            f"[UI] _go_select_template gestart  venster=({pos.x()},{pos.y()}) "
            f"grootte={self.width()}x{self.height()}  volledig={self.isFullScreen()}",
            flush=True
        )
        self.review_timer.stop()
        # Pause SumUp loop during photo session
        if hasattr(self, '_sumup_loop') and self._sumup_loop:
            self._sumup_loop.pause()
        # Ensure camera mode matches event setting (webcam vs DSLR)
        ev = self.active_event
        expected_mode = ev.camera_mode if ev else "dslr"
        # Detect current camera type by attribute (more reliable than isinstance in frozen EXE)
        current_is_webcam = hasattr(self.camera, 'cap')  # WebcamCamera has .cap, Camera does not
        current_mode = "webcam" if current_is_webcam else "dslr"
        print(f"[CAMERA] Check: verwacht={expected_mode}, huidig={current_mode}, connected={self.camera.is_connected()}")

        # Always reinitialize if mode doesn't match
        if current_mode != expected_mode:
            print(f"[CAMERA] Mode mismatch — herinitialiseren naar {expected_mode}")
            try:
                self.camera.disconnect()
            except Exception:
                pass
            if expected_mode == "webcam":
                try:
                    from webcam import WebcamCamera
                    self.camera = WebcamCamera()
                    wc_idx = ev.webcam_index if ev else 0
                    wc_res = ev.webcam_resolution if ev else ""
                    wc_name = ev.webcam_name if ev else ""
                    print(f"[CAMERA] Webcam verbinden: index={wc_idx}, res={wc_res}, naam={wc_name}")
                    if not self.camera.connect(wc_idx, wc_res, wc_name):
                        self._show_error("Webcam niet gevonden.\n\nControleer:\n• Is de webcam aangesloten?\n• Kies de juiste webcam via Instellingen → Camera")
                        return
                    self._digicam_ready = True
                    print(f"[CAMERA] Webcam herinitialiseerd OK")
                except ImportError as ie:
                    self._show_error(f"Webcam module niet beschikbaar:\n{ie}")
                    return
            else:
                from camera import Camera
                self.camera = Camera()
                if not self.camera.connect():
                    self._show_error(t("camera_not_found"))
                    return
                self._digicam_ready = True
                print("[CAMERA] DSLR herinitialiseerd OK")

        # Camera mode matches — just check connection
        elif not self.camera.is_connected():
            print(f"[CAMERA] Niet verbonden — opnieuw verbinden als {expected_mode}")
            if expected_mode == "webcam":
                wc_idx = ev.webcam_index if ev else 0
                wc_res = ev.webcam_resolution if ev else ""
                wc_name = ev.webcam_name if ev else ""
                print(f"[CAMERA] Webcam reconnect: index={wc_idx}, res={wc_res}, naam={wc_name}")
                if not self.camera.connect(wc_idx, wc_res, wc_name):
                    self._show_error("Webcam niet gevonden.\n\nControleer:\n• Is de webcam aangesloten?\n• Kies de juiste webcam via Instellingen → Camera")
                    return
            else:
                if not self.camera.connect():
                    self._show_error(t("camera_not_found"))
                    return

        # Check disk space before starting session
        try:
            import shutil
            disk = shutil.disk_usage(config.PHOTO_DIR)
            free_gb = disk.free / (1024 ** 3)
            if free_gb < 1.0:
                self._show_error(t("error_disk_full", gb=f"{free_gb:.1f}"))
                return
            elif free_gb < 10.0:
                print(f"[DISK] Waarschuwing: slechts {free_gb:.1f} GB vrij")
        except Exception:
            pass  # Don't block session if check fails

        # ── Linked-modus: meerdere templates? → picker tonen ─────────────
        # Als er >1 linked templates beschikbaar zijn voor de actieve booking,
        # laat de gast eerst kiezen. Anders direct door met enige beschikbare.
        linked_choices = self._get_linked_templates_for_booking()
        if len(linked_choices) > 1:
            self._show_template_picker(linked_choices)
            return  # _show_template_picker zal _go_direct_capture aanroepen na keuze

        # ── Geen cloud-templates? → default-picker (4 foto's / strips) ──
        # Wanneer er een booking gekoppeld is maar geen templates uit het
        # portaal beschikbaar zijn, laat de gast kiezen tussen '4 foto's op
        # een vel' of 'strips' met witte achtergrond. Bij keuze: in-memory
        # Template (niet persist naar disk).
        ev = self.active_event
        has_booking = ev and getattr(ev, 'linked_booking_id', '')
        if has_booking and len(linked_choices) == 0:
            defaults = self._build_default_templates_for_event()
            if defaults:
                self._show_template_picker(defaults)
                return

        # Check for pre-selected template from active event
        saved_name = self.active_event.template_name if self.active_event else ""
        match = self._find_template_by_name(saved_name) if saved_name else None

        # Als er precies 1 linked template is, gebruik die (overschrijf saved_name)
        if len(linked_choices) == 1:
            match = linked_choices[0]

        # Fallback: als er geen template gekozen is (of de gekozen naam niet
        # meer bestaat), pak automatisch de EERSTE preset uit de lijst en sla
        # die op bij het event. Dit voorkomt dat de oude template-keuze-pagina
        # met "foto maken" knop ooit nog getoond wordt vanuit de tap-flow.
        # Handmatige template-keuze via event-instellingen blijft gewoon werken.
        if not match:
            presets = get_preset_layouts()
            if presets:
                match = presets[0]
                if self.active_event:
                    self.active_event.template_name = match.name
                    self.active_event.save(config.EVENTS_DIR)
                print(f"[TEMPLATE] Auto-fallback naar eerste preset: '{match.name}'")

        if match:
            self.selected_template = match
            print(f"[TEMPLATE] Auto-geselecteerd: '{match.name}' - {match.num_photos} foto's, bg={match.background_path or 'geen'}")
            # Track session for active event
            if self.active_event:
                self.active_event.increment_session(config.EVENTS_DIR)
            self._preload_background()
            # Check if data collection is needed BEFORE capture
            ev = self.active_event
            if (ev and getattr(ev, 'data_collect_enabled', False)
                    and getattr(ev, 'data_collect_timing', 'after') == 'before'
                    and self._is_pro_feature("data_collection")):
                self._show_data_collection("before")
                return
            # Skip preview, go directly to countdown
            self._go_direct_capture()
            return

        # Niet bereikbaar in normale flow (er is altijd minstens 1 preset).
        # Behouden als laatste vangnet zodat de app niet vastloopt als
        # template_model.get_preset_layouts() ooit leeg zou returnen.
        self.state = State.SELECT_TEMPLATE
        self._load_templates()
        self.stack.setCurrentIndex(self.pages["select_template"])

    def _build_default_templates_for_event(self):
        """Maak in-memory default Template-objecten voor de actieve booking
        wanneer geen cloud-templates beschikbaar zijn.

        Pakket-bewust:
          - standaard (4x6) → ALLEEN '4 foto's op een vel' (2x2 landscape)
          - premium  (3strips) → '4 foto's op een vel' + '3 strips van 3 foto's'
        Nooit een 2-foto opmaak. Achtergrond is leeg (wit). Niet opgeslagen.
        """
        from template_model import Template, PhotoFrame
        ev = self.active_event
        if not ev:
            return []
        # Bepaal of premium-flow (DNP 3-strips) of standaard (Canon 2-strips)
        pm = getattr(ev, 'printer_mode', '3strips')
        if pm == 'dnp':
            pm = '3strips'
        elif pm == 'canon':
            pm = '4x6'
        is_premium = (pm == '3strips')

        # ── Template 1: 4 foto's op een vel (landscape 1800×1200) ──
        sheet_4 = Template(
            name="4 foto's op een vel",
            background_path="",
            frames=[
                PhotoFrame(x=30,  y=30,  width=855, height=555, rotation=0),
                PhotoFrame(x=915, y=30,  width=855, height=555, rotation=0),
                PhotoFrame(x=30,  y=615, width=855, height=555, rotation=0),
                PhotoFrame(x=915, y=615, width=855, height=555, rotation=0),
            ],
            is_double_strip=True,
            cut_default=False,
            is_triple_strip=False,
            is_4x3_strip=False,
        )

        # Standaard pakket: ALLEEN de 4-foto 2x2 opmaak (geen strip, nooit 2-foto).
        if not is_premium:
            return [sheet_4]

        # ── Premium: daarnaast de 3-foto strip (DNP triple, auto-cut) ──
        strips = Template(
            name="3 strips van 3 foto's",
            background_path="",
            frames=[
                PhotoFrame(x=48, y=30,  width=504, height=283, rotation=0),
                PhotoFrame(x=48, y=343, width=504, height=283, rotation=0),
                PhotoFrame(x=48, y=656, width=504, height=283, rotation=0),
            ],
            is_double_strip=False,
            cut_default=True,
            is_triple_strip=True,
            is_4x3_strip=False,
        )
        return [sheet_4, strips]

    def _get_linked_templates_for_booking(self):
        """Geef lijst van linked Template-objecten voor de actieve booking.

        Linked templates worden herkend aan de filename-prefix
        'linked_<booking_id>_' OF de naam-prefixes 'Event <id> — ' / 'Event <id> (...)'.
        Returns een lege lijst als geen actieve booking of geen linked templates.
        """
        ev = self.active_event
        if not ev:
            return []
        booking_id = getattr(ev, 'linked_booking_id', '') or ''
        if not booking_id:
            return []
        from template_model import Template
        results = []
        if not os.path.isdir(config.TEMPLATES_DIR):
            return []
        prefix = f"linked_{booking_id}_"
        for fname in sorted(os.listdir(config.TEMPLATES_DIR)):
            if not (fname.startswith(prefix) and fname.endswith(".json")):
                continue
            fpath = os.path.join(config.TEMPLATES_DIR, fname)
            try:
                t = Template.load(fpath)
                # NOOIT een 2-foto opmaak tonen. Oude events die vóór de
                # 2-foto-stop gekoppeld zijn, hebben dat bestand nog op schijf;
                # filter 'm hier weg én ruim het verouderde bestand op zodat
                # 'ie nooit meer in de keuzelijst verschijnt (zelf-herstellend).
                n_photos = getattr(t, 'num_photos', None)
                if n_photos is None:
                    n_photos = len(getattr(t, 'frames', []))
                if n_photos == 2:
                    print(f"[TEMPLATE-PICKER] 2-foto variant overgeslagen + opgeruimd: {fname}")
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
                    continue
                results.append(t)
            except Exception as e:
                print(f"[TEMPLATE-PICKER] Kon {fname} niet laden: {e}")
        return results

    def _show_template_picker(self, choices):
        """Template-picker met max 3 cards per pagina + ◀ ▶ navigatie.

        Toont een rij van max 3 templates tegelijk. Bij meer dan 3:
        carousel-pagina's met pijlen voor links/rechts. Cards tonen
        ECHTE template-previews als 'gevallen vel' (drop-shadow op
        donkere achtergrond) zodat het volledige papier zichtbaar is.
        """
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QStackedWidget,
        )

        current_page = self.stack.currentWidget()
        if current_page is None:
            current_page = self
        self._tmpl_picker_overlay = QWidget(current_page)
        self._tmpl_picker_overlay.setGeometry(0, 0, current_page.width(), current_page.height())
        self._tmpl_picker_overlay.setStyleSheet("background: rgba(20,20,22,0.97);")
        self._tmpl_picker_choices = list(choices)
        self._tmpl_picker_page = 0
        PER_PAGE = 3

        lay = QVBoxLayout(self._tmpl_picker_overlay)
        lay.setContentsMargins(40, 36, 40, 32)
        lay.setSpacing(14)

        # ── Header ────────────────────────────────────────────────────
        title = QLabel("Kies je ontwerp")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 40, QFont.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        lay.addWidget(title)

        subtitle = QLabel("Tik op het ontwerp dat je wilt gebruiken")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 15))
        subtitle.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent;")
        lay.addWidget(subtitle)
        lay.addSpacing(8)

        # ── Scherm-afmetingen + card-hoogte ───────────────────────────
        screen = self.screen()
        screen_w = screen.geometry().width() if screen else 1920
        screen_h = screen.geometry().height() if screen else 1080

        # Doelhoogte cards: 50% van scherm — iets kleiner dan eerder zodat
        # landscape templates met 3:2 aspect proportioneel volledig zichtbaar
        # zijn (ipv klein-en-zwevend in een te hoge card). Per template krijgt
        # de card z'n eigen (w,h) op basis van de paper-aspect.
        target_h = min(560, int(screen_h * 0.50))

        # Bouw alle cards 1 keer (totale breedte beperkt door PER_PAGE)
        cards = []
        for tmpl in self._tmpl_picker_choices:
            thumb_w, thumb_h = self._picker_card_size(tmpl, target_h)
            card = self._build_template_picker_card(tmpl, thumb_w, thumb_h)
            cards.append(card)

        # Paginate
        n = len(cards)
        n_pages = max(1, (n + PER_PAGE - 1) // PER_PAGE)

        # ── Carousel: stacked pages met max 3 cards per pagina ────────
        self._tmpl_picker_stack = QStackedWidget()
        self._tmpl_picker_stack.setStyleSheet("background: transparent;")
        for page_idx in range(n_pages):
            start = page_idx * PER_PAGE
            end = min(start + PER_PAGE, n)
            page_widget = QWidget()
            page_widget.setStyleSheet("background: transparent;")
            page_lay = QHBoxLayout(page_widget)
            page_lay.setSpacing(48)
            page_lay.setContentsMargins(0, 0, 0, 0)
            page_lay.addStretch()
            for card in cards[start:end]:
                page_lay.addWidget(card, alignment=Qt.AlignCenter)
            page_lay.addStretch()
            self._tmpl_picker_stack.addWidget(page_widget)

        # Carousel-row: ◀ [stack] ▶
        carousel_row = QHBoxLayout()
        carousel_row.setContentsMargins(0, 0, 0, 0)
        carousel_row.setSpacing(0)

        def _arrow_btn(text, slot, side):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(QFont("DM Sans", 28, QFont.Bold))
            b.setFixedSize(64, 64)
            b.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.08); color: white; "
                "border: 1px solid rgba(255,255,255,0.18); border-radius: 32px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.16); }"
                "QPushButton:disabled { color: rgba(255,255,255,0.2); "
                "background: rgba(255,255,255,0.04); }"
            )
            b.clicked.connect(slot)
            return b

        self._tmpl_picker_prev_btn = _arrow_btn("‹", self._tmpl_picker_page_prev, "left")
        self._tmpl_picker_next_btn = _arrow_btn("›", self._tmpl_picker_page_next, "right")

        carousel_row.addWidget(self._tmpl_picker_prev_btn, alignment=Qt.AlignVCenter)
        carousel_row.addSpacing(16)
        carousel_row.addWidget(self._tmpl_picker_stack, stretch=1)
        carousel_row.addSpacing(16)
        carousel_row.addWidget(self._tmpl_picker_next_btn, alignment=Qt.AlignVCenter)

        # Verberg pijlen als er maar 1 pagina is
        if n_pages <= 1:
            self._tmpl_picker_prev_btn.hide()
            self._tmpl_picker_next_btn.hide()

        lay.addLayout(carousel_row, stretch=1)

        # ── Page-indicator dots (alleen bij >1 pagina) ────────────────
        self._tmpl_picker_dots = []
        if n_pages > 1:
            dots_row = QHBoxLayout()
            dots_row.setSpacing(10)
            dots_row.addStretch()
            for i in range(n_pages):
                dot = QLabel()
                dot.setFixedSize(10, 10)
                dot.setStyleSheet(
                    f"background: {'white' if i == 0 else 'rgba(255,255,255,0.28)'}; "
                    f"border-radius: 5px;"
                )
                self._tmpl_picker_dots.append(dot)
                dots_row.addWidget(dot)
            dots_row.addStretch()
            lay.addLayout(dots_row)
            lay.addSpacing(6)

        # ── Annuleer knop onderaan ───────────────────────────────────
        cancel_btn = QPushButton("✕  Annuleer")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFont(QFont("DM Sans", 15, QFont.Bold))
        cancel_btn.setFixedHeight(48)
        cancel_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: white; "
            "border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; "
            "padding: 6px 32px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        cancel_btn.clicked.connect(self._on_template_picker_cancel)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        cancel_row.addWidget(cancel_btn)
        cancel_row.addStretch()
        lay.addLayout(cancel_row)

        self._tmpl_picker_overlay.show()
        self._tmpl_picker_overlay.raise_()
        # Update knop-state (eerste pagina)
        self._tmpl_picker_update_page_state()
        print(f"[TEMPLATE-PICKER] Geopend met {n} keuzes over {n_pages} pagina(s) "
              f"(thumb h={thumb_h})")

    def _tmpl_picker_update_page_state(self):
        """Update arrow-buttons + dot-indicator op basis van huidige pagina."""
        if not hasattr(self, '_tmpl_picker_stack'):
            return
        idx = self._tmpl_picker_page
        n_pages = self._tmpl_picker_stack.count()
        if hasattr(self, '_tmpl_picker_prev_btn'):
            self._tmpl_picker_prev_btn.setEnabled(idx > 0)
            self._tmpl_picker_next_btn.setEnabled(idx < n_pages - 1)
        for i, dot in enumerate(self._tmpl_picker_dots):
            dot.setStyleSheet(
                f"background: {'white' if i == idx else 'rgba(255,255,255,0.28)'}; "
                f"border-radius: 5px;"
            )

    def _tmpl_picker_page_prev(self):
        if self._tmpl_picker_page > 0:
            self._tmpl_picker_page -= 1
            self._tmpl_picker_stack.setCurrentIndex(self._tmpl_picker_page)
            self._tmpl_picker_update_page_state()

    def _tmpl_picker_page_next(self):
        if self._tmpl_picker_page < self._tmpl_picker_stack.count() - 1:
            self._tmpl_picker_page += 1
            self._tmpl_picker_stack.setCurrentIndex(self._tmpl_picker_page)
            self._tmpl_picker_update_page_state()

    def _picker_canvas_dims(self, tmpl):
        """Bepaal canvas (paper) afmetingen van een template voor preview-aspect."""
        frames = tmpl.frames or []
        max_x = max((f.x + f.width for f in frames), default=600)
        max_y = max((f.y + f.height for f in frames), default=1200)

        if getattr(tmpl, 'is_triple_strip', False):
            return 600, 1200
        if getattr(tmpl, 'is_4x3_strip', False):
            return 1200, 900
        if not tmpl.is_double_strip and max_x <= 700 and max_y >= max_x:
            # Canon dubbele strip (mirror) — 600x1800
            return 600, 1800
        if max_x > max_y:
            return 1800, 1200
        return 1200, 1800

    def _picker_card_size(self, tmpl, target_h):
        """Bereken card (thumb_w, thumb_h) zodat het hele velletje proportioneel
        zichtbaar is binnen een redelijke footprint.

        target_h is het maximum voor card-hoogte. Voor landscape templates wordt
        de card juist BREDER en KORTER zodat de paper-aspect klopt en geen
        ruimte wordt verspild boven/onder het vel.

        Strategie: 'footprint area' van max 290.000 px² per card (≈ 540×540
        equivalent), maar nooit hoger dan target_h en nooit breder dan 720.
        Vorm = canvas-aspect.
        """
        canvas_w, canvas_h = self._picker_canvas_dims(tmpl)
        aspect = canvas_w / canvas_h
        # Doel: card fits in target_h × 720 box met juiste aspect
        max_w = 720
        max_h = target_h
        # Begin met card op max_h hoog
        h = max_h
        w = int(h * aspect)
        if w > max_w:
            # Card te breed → schaal naar beneden
            w = max_w
            h = int(w / aspect)
        # Minimum
        if w < 180:
            w = 180
            h = int(w / aspect)
        return w, h

    # ── Carousel navigation helpers ───────────────────────────────────

    def _tmpl_picker_update_buttons(self):
        """Update prev/next disable state + dot-indicator op huidige index."""
        idx = self._tmpl_picker_index
        n = len(self._tmpl_picker_choices)
        if hasattr(self, '_tmpl_picker_prev_btn'):
            self._tmpl_picker_prev_btn.setEnabled(idx > 0)
            self._tmpl_picker_next_btn.setEnabled(idx < n - 1)
        for i, dot in enumerate(self._tmpl_picker_dots):
            dot.setStyleSheet(
                f"background: {'white' if i == idx else 'rgba(255,255,255,0.3)'}; "
                f"border-radius: 7px;"
            )

    def _tmpl_picker_prev(self):
        if self._tmpl_picker_index > 0:
            self._tmpl_picker_index -= 1
            self._tmpl_picker_stack.setCurrentIndex(self._tmpl_picker_index)
            self._tmpl_picker_update_buttons()

    def _tmpl_picker_next(self):
        if self._tmpl_picker_index < len(self._tmpl_picker_choices) - 1:
            self._tmpl_picker_index += 1
            self._tmpl_picker_stack.setCurrentIndex(self._tmpl_picker_index)
            self._tmpl_picker_update_buttons()

    def _tmpl_picker_confirm(self):
        """Bevestig huidige selectie en start sessie."""
        tmpl = self._tmpl_picker_choices[self._tmpl_picker_index]
        self._on_template_picked(tmpl)

    def _install_swipe_on(self, widget, on_swipe_right, on_swipe_left, on_tap=None):
        """Voeg touch/mouse-drag swipe-gestures + optionele tap-handler toe.

        Swipe naar RECHTS (vinger gaat rechts) → on_swipe_right (= vorige)
        Swipe naar LINKS (vinger gaat links)   → on_swipe_left (= volgende)
        Tap (geen significante x-beweging)     → on_tap
        """
        widget._swipe_start_x = None
        widget._swipe_threshold = 50  # px

        original_press = widget.mousePressEvent
        original_release = widget.mouseReleaseEvent

        def press(e):
            widget._swipe_start_x = e.x()
            if original_press:
                original_press(e)

        def release(e):
            if widget._swipe_start_x is not None:
                dx = e.x() - widget._swipe_start_x
                if dx > widget._swipe_threshold:
                    on_swipe_right()
                elif dx < -widget._swipe_threshold:
                    on_swipe_left()
                elif on_tap is not None:
                    # Geen swipe → tap detected
                    on_tap()
                widget._swipe_start_x = None
            if original_release:
                original_release(e)

        widget.mousePressEvent = press
        widget.mouseReleaseEvent = release

    def _build_template_picker_card(self, tmpl, thumb_w, thumb_h):
        """Bouw één klikbare template-kaart voor de picker.

        Visueel: het hele papiervel met 8% breathing-room rond de
        canvas-rand, drop-shadow eronder zodat het lijkt op een echt
        liggend fotopapiertje. Naam eronder.
        """
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QLabel, QGraphicsDropShadowEffect,
        )
        from PyQt5.QtGui import QColor

        # Outer container — transparent, clickbaar
        card = QWidget()
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet("background: transparent;")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(20)

        # Render ECHTE preview met 8% breathing-room (tight=False) zodat
        # het hele vel zichtbaar is incl. de witte paper-margins rond de
        # frames. Achtergrond van de pixmap is transparant gemaakt door
        # de inner-render aan te passen.
        raw_pix = self._render_layout_preview(tmpl, thumb_w, thumb_h, tight=False)
        rounded_pix = self._apply_rounded_corners(raw_pix, radius=18)
        thumb = QLabel()
        thumb.setPixmap(rounded_pix)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setFixedSize(thumb_w, thumb_h)
        # Subtiele drop-shadow zodat het vel "zweeft" boven de donkere
        # achtergrond — geeft tactiel papier-gevoel.
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 160))
        thumb.setGraphicsEffect(shadow)
        thumb.setStyleSheet("background: transparent;")
        card_lay.addWidget(thumb, alignment=Qt.AlignCenter)

        # Naam (zonder "Event <id> —" prefix)
        display_name = tmpl.name
        if " — " in display_name:
            display_name = display_name.split(" — ", 1)[1]
        name_lbl = QLabel(display_name)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setFont(QFont("DM Sans", 17, QFont.Bold))
        name_lbl.setStyleSheet(
            "color: white; background: transparent; "
            "letter-spacing: -0.2px;"
        )
        name_lbl.setWordWrap(True)
        name_lbl.setMaximumWidth(thumb_w + 40)
        card_lay.addWidget(name_lbl)

        # Click handler — direct confirm bij tap (via thumb of card)
        def _on_click(_event, t=tmpl):
            self._on_template_picked(t)
        card.mousePressEvent = _on_click
        thumb.mousePressEvent = _on_click
        return card

    def _apply_rounded_corners(self, pix, radius=24):
        """Apply rounded corners to a QPixmap (returns nieuwe pixmap)."""
        from PyQt5.QtGui import QPainter, QPainterPath
        from PyQt5.QtCore import QRectF
        w, h = pix.width(), pix.height()
        rounded = QPixmap(w, h)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pix)
        painter.end()
        return rounded

    def _picker_visual_type(self, tmpl):
        """Classificeer template voor minimal-thumbnail rendering.

        Returns: 'sheet_landscape' | 'sheet_portrait' |
                 '2_strips' | '3_strips' | '4x3'
        """
        if getattr(tmpl, 'is_triple_strip', False):
            return '3_strips'
        if getattr(tmpl, 'is_4x3_strip', False):
            return '4x3'
        # Frame-extents bepalen of het landscape of portrait is
        frames = tmpl.frames or []
        max_x = max((f.x + f.width for f in frames), default=0)
        max_y = max((f.y + f.height for f in frames), default=0)
        # Canon dubbele strip: frames staan in linkerhelft (max_x ~ 600),
        # is_double_strip=False (= mirror-mode). Wordt 2 strips bij print.
        if not tmpl.is_double_strip and max_x <= 700 and max_y >= max_x:
            return '2_strips'
        # Anders: vol vel — landscape of portrait gebaseerd op aspect
        if max_x > max_y:
            return 'sheet_landscape'
        return 'sheet_portrait'

    def _render_minimal_template_shape(self, tmpl, w, h):
        """Render een minimalist paper-shape voor de picker.

        Strip = 1/3 breedte van een sheet (proportie aan papierformaat).
        Soft brand-color fill, afgeronde hoeken, geen interne details.
        """
        from PyQt5.QtGui import QPainter, QColor, QBrush, QPen
        from PyQt5.QtCore import QRect, QRectF
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Soft brand-tinted fill
        fill_color = QColor(config.COLOR_PRIMARY)
        fill_color.setAlpha(180)
        border_color = QColor(config.COLOR_PRIMARY_HOVER)
        painter.setBrush(QBrush(fill_color))
        painter.setPen(QPen(border_color, 2))

        vtype = self._picker_visual_type(tmpl)
        # Berekenen op een vast referentie-grid van breedte-eenheden
        # zodat strip = 1/3 sheet-width (visueel proportioneel)
        sheet_w_units = 6.0   # sheet = 6 eenheden breed
        strip_w_units = 2.0   # strip = 2 eenheden breed (= 1/3 van sheet)
        gap_units = 0.4
        radius = 18

        if vtype == 'sheet_landscape':
            # Landscape sheet 3:2 — wide rect
            shape_w_units = sheet_w_units
            shape_h_units = sheet_w_units / 1.5
            unit = min(w * 0.85 / shape_w_units, h * 0.85 / shape_h_units)
            shape_w = shape_w_units * unit
            shape_h = shape_h_units * unit
            x = (w - shape_w) / 2
            y = (h - shape_h) / 2
            painter.drawRoundedRect(QRectF(x, y, shape_w, shape_h), radius, radius)

        elif vtype == 'sheet_portrait':
            # Portrait sheet 2:3
            shape_w_units = sheet_w_units / 1.5
            shape_h_units = sheet_w_units
            unit = min(w * 0.85 / shape_w_units, h * 0.85 / shape_h_units)
            shape_w = shape_w_units * unit
            shape_h = shape_h_units * unit
            x = (w - shape_w) / 2
            y = (h - shape_h) / 2
            painter.drawRoundedRect(QRectF(x, y, shape_w, shape_h), radius, radius)

        elif vtype == '4x3':
            # 4x3 half-sheet landscape, 4:3 ratio
            shape_w_units = sheet_w_units * 0.75
            shape_h_units = sheet_w_units * 0.75 / (4/3)
            unit = min(w * 0.85 / shape_w_units, h * 0.85 / shape_h_units)
            shape_w = shape_w_units * unit
            shape_h = shape_h_units * unit
            x = (w - shape_w) / 2
            y = (h - shape_h) / 2
            painter.drawRoundedRect(QRectF(x, y, shape_w, shape_h), radius, radius)

        elif vtype in ('2_strips', '3_strips'):
            count = 2 if vtype == '2_strips' else 3
            # Elke strip = 1/3 sheet-width, hoogte = sheet-height (portrait)
            strip_h_units = sheet_w_units  # zelfde als sheet width voor proportie
            total_w_units = count * strip_w_units + (count - 1) * gap_units
            unit = min(w * 0.85 / total_w_units, h * 0.85 / strip_h_units)
            strip_w = strip_w_units * unit
            strip_h = strip_h_units * unit
            gap = gap_units * unit
            total_w = count * strip_w + (count - 1) * gap
            x_start = (w - total_w) / 2
            y = (h - strip_h) / 2
            for i in range(count):
                x = x_start + i * (strip_w + gap)
                painter.drawRoundedRect(QRectF(x, y, strip_w, strip_h),
                                         radius * 0.6, radius * 0.6)

        painter.end()
        return pixmap

    def _on_template_picked(self, tmpl):
        """User heeft een template gekozen uit de picker → start sessie."""
        print(f"[TEMPLATE-PICKER] Gekozen: '{tmpl.name}'")
        if hasattr(self, '_tmpl_picker_overlay') and self._tmpl_picker_overlay:
            self._tmpl_picker_overlay.deleteLater()
            self._tmpl_picker_overlay = None
        self.selected_template = tmpl
        if self.active_event:
            self.active_event.template_name = tmpl.name
            self.active_event.save(config.EVENTS_DIR)
        # Track session for active event
        if self.active_event:
            self.active_event.increment_session(config.EVENTS_DIR)
        self._preload_background()
        # Check data collection (zelfde als _go_select_template)
        ev = self.active_event
        if (ev and getattr(ev, 'data_collect_enabled', False)
                and getattr(ev, 'data_collect_timing', 'after') == 'before'
                and self._is_pro_feature("data_collection")):
            self._show_data_collection("before")
            return
        self._go_direct_capture()

    def _on_template_picker_cancel(self):
        """User klikt Annuleer in de picker → terug naar idle."""
        print("[TEMPLATE-PICKER] Geannuleerd")
        if hasattr(self, '_tmpl_picker_overlay') and self._tmpl_picker_overlay:
            self._tmpl_picker_overlay.deleteLater()
            self._tmpl_picker_overlay = None

    def _check_internet_bg(self):
        """Check internet connectivity in background thread."""
        def _check():
            import urllib.request
            try:
                urllib.request.urlopen("https://www.google.com", timeout=3)
                self._has_internet = True
            except Exception:
                self._has_internet = False
            print(f"[NET] Internetverbinding: {'ja' if self._has_internet else 'nee'}")
        threading.Thread(target=_check, daemon=True).start()

    def _go_direct_capture(self):
        """Start photo session directly: init session, start live view, begin countdown."""
        # NB: DNP-poller blijft DRAAIEN tijdens capture/review/sharing.
        # Anders kan de auto-retry-print niet detecteren wanneer de fout
        # is verholpen. Tijdens capture is er geen typing, dus geen
        # focus-steal-risico. Eventuele email/naam-input op sharing krijgt
        # later een gerichte pause rondom dat dialog (TODO).
        # Check internet connectivity in background
        self._has_internet = True  # Assume yes until check completes
        self._check_internet_bg()
        self.current_photo_num = 0
        self.photos = []
        self._processed_photos = []
        self._photo_filters = {}
        self.session_id = self._new_session_id()
        self._rebuild_thumbnails()
        self.camera.configure_save_folder()
        self._update_counter()
        # Go to preview, start live view, then wait for first frame before countdown
        self.state = State.PREVIEW
        self.capture_btn.hide()  # Skip "FOTO MAKEN" button for direct capture
        # Clear previous photo from label so it doesn't flash
        self.countdown_live_label.clear()
        self._showing_preview = False
        pos = self.pos()
        print(
            f"[UI] Voor setCurrentIndex(preview): venster=({pos.x()},{pos.y()}) "
            f"grootte={self.width()}x{self.height()}  volledig={self.isFullScreen()}",
            flush=True
        )
        self.stack.setCurrentIndex(self.pages["preview"])
        pos2 = self.pos()
        print(
            f"[UI] Na setCurrentIndex(preview): venster=({pos2.x()},{pos2.y()}) "
            f"grootte={self.width()}x{self.height()}  volledig={self.isFullScreen()}",
            flush=True
        )
        # Always start live view — _start_live_view handles reconnect if needed
        self._start_live_view()
        # Delay overlay positioning to give the layout time to fully settle
        # (was 50ms — increased to 150ms to avoid race with DPI geometry fixes)
        QTimer.singleShot(150, self._position_session_overlays)
        # Wait for first live view frame, then start countdown
        self._lv_ready_for_countdown = False
        self._lv_wait_start = __import__('time').time()
        self._lv_wait_timer = QTimer()
        self._lv_wait_timer.setInterval(200)
        self._lv_wait_timer.timeout.connect(self._check_lv_ready)
        self._lv_wait_timer.start()

    def _check_lv_ready(self):
        """Wait for first live view frame before starting countdown."""
        import time as _t
        # Check if live view label has a pixmap (frame received)
        has_frame = (self.countdown_live_label.pixmap() is not None and
                     not self.countdown_live_label.pixmap().isNull())
        elapsed = _t.time() - self._lv_wait_start
        if has_frame or elapsed > 3.0:  # Max 3s wait
            self._lv_wait_timer.stop()
            if not has_frame:
                print(f"[UI] Live view timeout ({elapsed:.1f}s), start countdown anyway")
            else:
                print(f"[UI] Live view klaar in {elapsed*1000:.0f}ms")
            # Start countdown immediately — no extra preview delay between photos
            self._start_countdown()

    def _go_preview(self):
        self.state = State.PREVIEW
        # Check internet connectivity in background
        self._has_internet = True  # Assume yes until check completes
        self._check_internet_bg()
        # Clear previous session's photos from labels
        self.countdown_live_label.clear()
        self.live_view_label.clear()
        self._showing_preview = False
        if not self.photos:
            self.current_photo_num = 0
            self.session_id = self._new_session_id()
            self._rebuild_thumbnails()
            self.camera.configure_save_folder()
            # Track session for active event
            if self.active_event:
                self.active_event.increment_session(config.EVENTS_DIR)

        self._update_counter()
        self.capture_btn.show()
        self.stack.setCurrentIndex(self.pages["preview"])
        self._start_live_view()
        # Position overlays after page is shown
        QTimer.singleShot(50, self._position_session_overlays)

    def _update_counter(self):
        num = self.current_photo_num + 1
        text = f"Foto {num} van {self.num_photos}"
        self.photo_counter_label.setText(text)
        self.countdown_info.setText(text)

    def _cancel_session(self):
        """Cancel the current photo session and return to idle.

        Stops all active timers to prevent delayed callbacks from
        restarting the session after cancel.
        """
        print("[UI] Sessie geannuleerd", flush=True)
        # Stop any pending timers that could restart the session
        if hasattr(self, '_lv_wait_timer') and self._lv_wait_timer.isActive():
            self._lv_wait_timer.stop()
        if hasattr(self, 'countdown_timer') and self.countdown_timer.isActive():
            self.countdown_timer.stop()
        self._reset_countdown_ui()
        self._stop_live_view(blocking=False)
        self._go_idle()

    def _reset_thumbnails(self):
        for i, thumb in enumerate(self.thumb_labels):
            thumb.setPixmap(QPixmap())
            thumb.setText(str(i + 1))
            thumb.setStyleSheet(
                f"background: rgba(255,255,255,0.15); "
                f"border: 2px dashed rgba(255,255,255,0.4); border-radius: 8px;"
                f" color: rgba(255,255,255,0.5);"
            )

    def _update_thumbnail(self, index, photo_path):
        """Show a small thumbnail of a captured photo in the bottom blocks."""
        if index >= len(self.thumb_labels):
            return
        pixmap = QPixmap(photo_path)
        if pixmap.isNull():
            return
        # Apply same center-crop as strip
        template = self.selected_template
        if template and 0 <= index < len(template.frames):
            frame = template.frames[index]
            fw, fh = frame.width, frame.height
            if getattr(frame, 'rotation', 0) in (90, 270, -90, -270):
                fw, fh = fh, fw
            if fw > 0 and fh > 0:
                frame_ratio = fw / fh
                pw, ph = pixmap.width(), pixmap.height()
                cam_ratio = pw / ph if ph > 0 else 1.0
                if abs(cam_ratio - frame_ratio) > 0.05:
                    if cam_ratio > frame_ratio:
                        new_w = int(ph * frame_ratio)
                        pixmap = pixmap.copy((pw - new_w) // 2, 0, new_w, ph)
                    else:
                        new_h = int(pw / frame_ratio)
                        pixmap = pixmap.copy(0, (ph - new_h) // 2, pw, new_h)
        thumb = self.thumb_labels[index]
        thumb.setText("")
        scaled = pixmap.scaled(
            thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        thumb.setPixmap(scaled)
        thumb.setStyleSheet(
            f"background: rgba(255,255,255,0.9); "
            f"border: 2px solid {config.COLOR_SUCCESS}; border-radius: 8px;"
        )

    def _position_session_overlays(self):
        """Position all overlays on the preview/session page."""
        preview_page = self.stack.widget(self.pages["preview"])
        # Use screen geometry mapped to page — robust against layout-driven size corruption
        from PyQt5.QtCore import QPoint
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            tl = preview_page.mapFromGlobal(QPoint(sg.left(), sg.top()))
            br = preview_page.mapFromGlobal(QPoint(sg.right(), sg.bottom()))
            w = br.x() - tl.x() + 1
            h = br.y() - tl.y() + 1
        else:
            w, h = preview_page.width(), preview_page.height()
        from PyQt5.QtCore import QRect
        rect = QRect(0, 0, w, h)

        # Cancel button — top-left with margin
        self.cancel_session_btn.move(15, 15)
        self.cancel_session_btn.raise_()

        # Photo blocks — bottom center (cache sizeHint)
        thumb_hint = self.thumbs_widget.sizeHint()
        thumb_w, thumb_h = thumb_hint.width(), thumb_hint.height()
        self.thumbs_widget.setGeometry(
            (w - thumb_w) // 2, h - thumb_h - 20, thumb_w, thumb_h
        )
        self.thumbs_widget.raise_()

        # Capture button — center-bottom, above thumbnails
        btn_hint = self.capture_btn.sizeHint()
        btn_w, btn_h = btn_hint.width(), btn_hint.height()
        self.capture_btn.setGeometry(
            (w - btn_w) // 2, h - thumb_h - btn_h - 40, btn_w, btn_h
        )
        self.capture_btn.raise_()

        # Countdown ring — full page overlay
        self.countdown_ring.setGeometry(rect)

        # Intro label — centered
        self.intro_label.setGeometry(
            w // 4, h // 3, w // 2, h // 4
        )

        # Capture screen — full page
        self.capture_screen_label.setGeometry(rect)

    def _reset_countdown_ui(self):
        """Central cleanup for all countdown UI elements."""
        self.countdown_timer.stop()
        self.countdown_ring.stop()
        self.countdown_ring.hide()
        self.intro_label.hide()
        self.capture_screen_label.hide()
        self._countdown_phase = None

    def _start_countdown(self):
        # Guard: only start if still in an active session state
        if self.state == State.IDLE:
            print("[COUNTDOWN] Genegeerd — sessie is al geannuleerd")
            return
        print(f"[COUNTDOWN] Start (photo {self.current_photo_num+1}/{self.num_photos})", flush=True)
        self.state = State.COUNTDOWN
        self._showing_preview = False  # Allow live frames to show again

        # COB-LED: als de flits-relay (nog) niet verbonden is, probeer 'm nu
        # alvast (niet-blokkerend) te verbinden. De countdown duurt enkele
        # seconden, dus de flits voor deze foto — óók de 1e — heeft zo maximaal
        # kans op een werkende verbinding.
        self._led_try_connect_async()

        # Hide the start button during countdown
        self.capture_btn.hide()

        # Use event's countdown duration or global default
        # Use event's countdown duration or global default
        if self.active_event and self.active_event.countdown_seconds > 0:
            self.countdown_value = self.active_event.countdown_seconds
        else:
            self.countdown_value = config.COUNTDOWN_SECONDS

        self._update_counter()
        # Stay on preview page — countdown happens as overlay
        self.stack.setCurrentIndex(self.pages["preview"])

        # Position overlays on preview page
        preview_page = self.stack.widget(self.pages["preview"])
        self._position_session_overlays()

        # Start buffering frames for boomerang
        if self._frame_buffer:
            self._frame_buffer.enable()

        # Pre-compute file snapshot in background thread during countdown
        # so CaptureThread can fire capture command IMMEDIATELY without prep work
        def _prepare():
            import time as _t
            t0 = _t.time()
            self._capture_search_folders = get_search_folders(self.camera)
            self._capture_existing_files = snapshot_files(self._capture_search_folders)
            ms = (_t.time() - t0) * 1000
            print(f"[CAPTURE] Snapshot voorbereid in {ms:.0f}ms "
                  f"({len(self._capture_existing_files)} bestanden)")
        threading.Thread(target=_prepare, daemon=True).start()

        # AF disabled during live view/countdown — camera does its own AF during capture

        # First photo: show intro screen, then countdown
        # Subsequent photos: skip intro, go straight to countdown
        if self.current_photo_num == 0:
            self._countdown_phase = "intro"
            self.countdown_ring.hide()
            # Show custom intro image or default text
            intro_path = self.active_event.intro_screen_path if self.active_event else ""
            intro_dur = (self.active_event.intro_duration if self.active_event else 2) * 1000
            if intro_path and os.path.isfile(intro_path):
                intro_pix = QPixmap(intro_path)
                if not intro_pix.isNull():
                    preview_page = self.stack.widget(self.pages["preview"])
                    scaled = intro_pix.scaled(
                        preview_page.size(), Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
                    self.intro_label.setPixmap(scaled)
                    self.intro_label.setAlignment(Qt.AlignCenter)
                else:
                    self.intro_label.setPixmap(QPixmap())
            else:
                self.intro_label.setPixmap(QPixmap())

            # Show intro text overlay if enabled
            intro_text_on = self.active_event.intro_text_enabled if self.active_event else True
            if intro_text_on:
                intro_txt = (self.active_event.intro_text if self.active_event
                             else t("intro_default_text"))
                # If text matches any language's default, use current language version
                _defaults = ["We gaan {n} foto's maken", "We're going to take {n} photos",
                             "Wir machen {n} Fotos", "Nous allons prendre {n} photos",
                             "Vamos a tomar {n} fotos", "Faremo {n} foto"]
                if intro_txt in _defaults:
                    intro_txt = t("intro_default_text")
                intro_txt = intro_txt.replace("{n}", str(self.num_photos))
                self.intro_label.setText(intro_txt)
            else:
                self.intro_label.setText("")
            self.intro_label.raise_()
            self.intro_label.show()
            QTimer.singleShot(intro_dur, self._on_intro_done)
        else:
            # Skip intro — go straight to counting
            self._countdown_phase = "counting"
            self.intro_label.hide()
            self.countdown_ring.raise_()
            self.countdown_ring.show()
            self.countdown_ring.start_second(self.countdown_value)
            self._play_beep(final=False)
            self.countdown_timer.start(1000)

    def _play_beep(self, final=False):
        """Play a countdown beep sound (non-blocking)."""
        if not getattr(config, 'COUNTDOWN_BEEP', False):
            return
        def _beep():
            try:
                import winsound
                if final:
                    # Camera click sound for capture moment
                    winsound.Beep(1400, 80)
                    import time; time.sleep(0.05)
                    winsound.Beep(1000, 60)
                else:
                    # Normal countdown tick
                    winsound.Beep(800, 100)
            except Exception:
                pass
        t = threading.Thread(target=_beep, daemon=True)
        t.start()

    def _on_intro_done(self):
        """Transition from intro screen to counting phase."""
        # Guard: only proceed if we're still in intro phase
        if self.state != State.COUNTDOWN or self._countdown_phase != "intro":
            return
        self._countdown_phase = "counting"
        self.intro_label.hide()
        self.countdown_ring.raise_()
        self.countdown_ring.show()
        self.countdown_ring.start_second(self.countdown_value)
        self._play_beep(final=False)
        # AF already started during intro phase — don't restart
        self.countdown_timer.start(1000)

    def _on_countdown_tick(self):
        if self.state != State.COUNTDOWN:
            return
        self.countdown_value -= 1
        val = self.countdown_value
        print(f"[COUNTDOWN] Tick: {val}", flush=True)

        is_webcam = type(self.camera).__name__ == "WebcamCamera"
        if val >= 1:
            # Countdown tick beep (including val==1)
            self.countdown_ring.start_second(val)
            self._play_beep(final=False)
            if val == 1:
                if self.led:
                    self.led.on()
                if not is_webcam:
                    # DSLR: freeze + capture early (camera needs ~1s shutter time)
                    self._freeze_and_capture()
        elif val == 0:
            # Camera click sound at moment of capture
            self.countdown_ring.start_second(0)
            self.countdown_timer.stop()
            self._play_beep(final=True)
            if is_webcam:
                # Webcam: instant capture at 0 (no shutter delay)
                self._freeze_and_capture()
        else:
            # Fallback safety
            self.countdown_timer.stop()
            self._freeze_and_capture()

    def _freeze_and_capture(self):
        """Freeze live view at current frame, start capture. Countdown keeps running."""
        if self.state != State.COUNTDOWN:
            print(f"[CAPTURE] Freeze genegeerd: verkeerde state ({self.state})")
            return

        import time as _time
        self._capture_t0 = _time.time()

        # Freeze the current live view frame — stop updating but keep showing last frame
        self._live_view_frozen = True

        # Snapshot boomerang frames
        if self._frame_buffer:
            self._frame_buffer.disable()
            if self.current_photo_num == self.num_photos - 1:
                self._boomerang_frames = self._frame_buffer.get_frames()
            self._frame_buffer.clear()

        self.state = State.CAPTURE

        # Check if using webcam or DSLR
        is_webcam = type(self.camera).__name__ == "WebcamCamera"

        if is_webcam:
            # Webcam: capture fresh high-res frame — naar photos/<event>/raw/
            raw_dir = self._get_raw_dir()
            filepath = os.path.join(
                raw_dir,
                self._timestamp_filename(ext=".jpg",
                                          photo_num=self.current_photo_num + 1),
            )
            # Use high-res capture (reads fresh frame at quality 95)
            if hasattr(self.camera, 'capture_high_res'):
                frame_data = self.camera.capture_high_res()
            else:
                frame_data = getattr(self.camera, '_last_frame', None)
            print(f"[WEBCAM] Capture: {len(frame_data)//1024}KB" if frame_data else "[WEBCAM] Capture: GEEN frame!")
            if frame_data:
                with open(filepath, 'wb') as f:
                    f.write(frame_data)
                print(f"[WEBCAM] Foto opgeslagen: {filepath} ({len(frame_data)//1024}KB)")
                self._on_capture_complete(filepath)
            else:
                print("[WEBCAM] Geen frame beschikbaar!")
                self._show_error(t("error_webcam_no_video"))
            return

        # DSLR: Send capture command directly to worker (don't show capture screen)
        try:
            self.camera._edsdk_captured_file = None
        except AttributeError:
            pass  # Not a DSLR camera
        self._capture_waiting = True
        self.camera.capture_photo(use_af=False)

        # Poll every 100ms for capture result
        if not hasattr(self, '_capture_poll_timer'):
            self._capture_poll_timer = QTimer()
            self._capture_poll_timer.setInterval(100)
            self._capture_poll_timer.timeout.connect(self._check_capture_inline)
        self._capture_poll_timer.start()

        print(f"[TIMING] Freeze + capture gestart in "
              f"{(_time.time() - self._capture_t0)*1000:.0f}ms")

    def _check_capture_inline(self):
        """Poll for capture completion every 100ms."""
        if not getattr(self, '_capture_waiting', False):
            if hasattr(self, '_capture_poll_timer'):
                self._capture_poll_timer.stop()
            return
        filepath = getattr(self.camera, '_edsdk_captured_file', None)
        if filepath and os.path.isfile(filepath):
            try:
                size_kb = os.path.getsize(filepath) / 1024
                if size_kb > 10:
                    self._capture_waiting = False
                    self._capture_poll_timer.stop()
                    import time as _t
                    elapsed = (_t.time() - self._capture_t0) * 1000
                    print(f"[CAPTURE-POLL] Bestand gevonden in {elapsed:.0f}ms: "
                          f"{os.path.basename(filepath)} ({size_kb:.0f}KB)", flush=True)
                    self._on_capture_complete(filepath)
                    return
            except OSError:
                pass
        # Timeout after 10s (was 20s — faster recovery bij disconnect)
        import time as _t
        if _t.time() - self._capture_t0 > 10:
            self._capture_waiting = False
            self._capture_poll_timer.stop()
            self._on_capture_failed("Capture timeout na 10s.")
            return

    def _show_capture_screen(self):
        """Show capture screen overlay at the exact moment of capture.

        If a custom capture screen image is set, show that fullscreen.
        Otherwise, show a white flash (classic photobooth effect).
        """
        # Check for custom capture screen image
        cap_path = ""
        if self.active_event and self.active_event.capture_screen_path:
            cap_path = self.active_event.capture_screen_path

        preview_page = self.stack.widget(self.pages["preview"])

        if cap_path and os.path.isfile(cap_path):
            # Load and cache the capture screen image
            if self._capture_screen_pixmap is None or getattr(self, '_capture_screen_src', '') != cap_path:
                self._capture_screen_pixmap = QPixmap(cap_path)
                self._capture_screen_src = cap_path

            if not self._capture_screen_pixmap.isNull():
                self.capture_screen_label.setGeometry(preview_page.rect())
                scaled = self._capture_screen_pixmap.scaled(
                    preview_page.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                self.capture_screen_label.setPixmap(scaled)
                self.capture_screen_label.setStyleSheet("background: black; color: white;")
                self.capture_screen_label.raise_()
                self.capture_screen_label.show()
                # Show capture text overlay if enabled
                cap_text_on = self.active_event.capture_text_enabled if self.active_event else True
                if cap_text_on:
                    cap_txt = (self.active_event.capture_text if self.active_event
                               else t("photo_make"))
                    self.capture_screen_label.setText(cap_txt)
                else:
                    self.capture_screen_label.setText("")
                return

        # Fallback: white flash
        self.capture_screen_label.setPixmap(QPixmap())
        self.capture_screen_label.setGeometry(preview_page.rect())
        self.capture_screen_label.setStyleSheet("background: white; color: #333333;")
        self.capture_screen_label.raise_()
        self.capture_screen_label.show()

        # Show capture text overlay if enabled
        cap_text_on = self.active_event.capture_text_enabled if self.active_event else True
        if cap_text_on:
            cap_txt = (self.active_event.capture_text if self.active_event
                       else "Blijf lachen tot de tweede klik")
            self.capture_screen_label.setText(cap_txt)
        else:
            self.capture_screen_label.setText("")

    def _led_safety_tick(self):
        """Periodieke vangnet-check (elke 300ms): de flits-LED hoort alleen
        aan te staan tijdens een actieve opname. In elke andere state
        forceren we 'm uit, zodat hij nooit blijft hangen na een
        onderbreking (kruisje/fout/timeout). ensure_off() is idempotent —
        schrijft alleen als de LED echt nog aan staat."""
        if not self.led:
            return
        if self.state not in (State.COUNTDOWN, State.CAPTURE):
            self.led.ensure_off()

    def _led_reconnect_tick(self):
        """Periodiek (10s): probeer de COB-LED (her)te verbinden zodat een
        los, laat-ingeplugd of op een andere COM-poort verschenen relay
        alsnog wordt opgepakt. Draait op een bg-thread (serial-open kan even
        duren) zodat de UI nooit hapert; overlappende runs worden vermeden."""
        if not self.led or getattr(self, '_led_reconnect_pending', False):
            return
        self._led_reconnect_pending = True

        def _work():
            try:
                self.led.ensure_connected()
            except Exception as e:
                print(f"[LED] Reconnect-tick fout: {e}")
            finally:
                self._led_reconnect_pending = False

        threading.Thread(target=_work, daemon=True).start()

    def _led_try_connect_async(self):
        """Kick een niet-blokkerende (her)verbindpoging voor de COB-LED —
        gebruikt bij de start van een opname zodat de flits voor de 1e foto
        zoveel mogelijk kans heeft op een werkende verbinding."""
        if not self.led or self.led.available:
            return
        threading.Thread(
            target=lambda: self.led.ensure_connected(), daemon=True
        ).start()

    def _end_flash_effect(self):
        """Hide capture screen and restore live view display.

        Live view thread is still running, so new frames will automatically
        update the countdown_live_label — no static/frozen screen.
        """
        if self.led:
            self.led.off()
        self.capture_screen_label.hide()
        self.countdown_live_label.setStyleSheet("background: black;")
        # Live view keeps updating automatically — no need to set a static frame

    def _on_capture_complete(self, file_path):
        import time as _time
        if hasattr(self, '_capture_t0'):
            total_ms = (_time.time() - self._capture_t0) * 1000
            print(f"[TIMING] Capture compleet: {total_ms:.0f}ms (freeze -> bestand gevonden)")

        # Unfreeze live view — new frames will update the display again
        self._live_view_frozen = False
        self._reset_countdown_ui()
        self._end_flash_effect()

        captured_idx = self.current_photo_num
        filters_on = getattr(config, 'FILTERS_ENABLED', False)

        # Restart live view: needed for the next photo, and — with the filter
        # screen — also after the LAST photo so 'Foto opnieuw nemen' works on
        # every photo.
        if captured_idx < self.num_photos - 1 or filters_on:
            # Small delay after capture for camera mirror to settle
            QTimer.singleShot(200, self.camera.start_live_view)

        # Save ALTIJD in photos/<event>/raw/ — consistent voor alle events.
        # save_photos_locally toggle wordt straks overruled: structured opslag
        # is altijd actief zodat de gebruiker niet meer foto's op random
        # plekken terugvindt.
        ext = os.path.splitext(file_path)[1] or ".jpg"
        raw_dir = self._get_raw_dir()
        dest = os.path.join(
            raw_dir,
            self._timestamp_filename(ext=ext, photo_num=captured_idx + 1),
        )
        if file_path != dest:
            try:
                shutil.copy2(file_path, dest)
            except Exception:
                # Bij kopieerfout: val terug op originele location zodat de
                # strip-build nog kan lezen van het bronbestand.
                dest = file_path

        self.photos.append(dest)
        self._update_thumbnail(captured_idx, dest)

        if filters_on:
            # Filterscherm: de foto wordt PAS verwerkt voor de strip én
            # geüpload nadat de gast een filter koos en op 'Volgende' drukt
            # (zie _filter_next). Zo komt het gekozen filter zowel in de strip
            # als in de losse geüploade foto, en is 'opnieuw nemen' schoon
            # (er is nog niets verwerkt/geüpload om ongedaan te maken).
            self._show_captured_preview(dest)
            # Toon de gemaakte foto eerst ~1 sec (niet te snel), dan pas de
            # filters.
            QTimer.singleShot(
                1000, lambda d=dest, i=captured_idx: self._show_filter_screen(d, i)
            )
            return

        # ── Klassiek gedrag (filterscherm uitgeschakeld) ──
        # Linked-modus: enqueue voor cloud upload
        self._maybe_enqueue_linked(dest)
        # Pre-process photo for strip building
        self._process_photo_for_strip(dest, captured_idx)
        # Show captured photo briefly, then live view resumes on screen
        self._show_captured_preview(dest)

        self.current_photo_num += 1

        if self.current_photo_num < self.num_photos:
            # Show photo briefly, then continue to next countdown
            delay = self.active_event.photo_delay_ms if self.active_event else 1500
            preview_time = max(800, delay)  # at least 0.8s preview
            QTimer.singleShot(preview_time, self._continue_after_preview)
        else:
            # Show last photo briefly, then build strip
            QTimer.singleShot(800, self._finish_after_preview)

    def _show_captured_preview(self, photo_path):
        """Show the just-captured photo large on screen briefly.

        Sets _showing_preview flag so live view frames don't overwrite
        the captured photo during the brief preview period.
        Applies the same crop as the strip so preview matches the final result.
        """
        pixmap = QPixmap(photo_path)
        if pixmap.isNull():
            return
        # Apply camera mirror + rotation (same as live view)
        ev = self.active_event
        if ev and (ev.camera_mirror or ev.camera_rotation):
            from PyQt5.QtGui import QTransform
            transform = QTransform()
            if ev.camera_mirror:
                transform = transform.scale(-1, 1)
            if ev.camera_rotation:
                transform = transform.rotate(ev.camera_rotation)
            pixmap = pixmap.transformed(transform, Qt.SmoothTransformation)

        # Apply same center-crop as the strip uses for this photo's frame slot
        # current_photo_num is still the 0-based index of the just-captured photo
        # (increment happens after this call)
        template = self.selected_template
        photo_idx = self.current_photo_num
        if template and 0 <= photo_idx < len(template.frames):
            frame = template.frames[photo_idx]
            fw, fh = frame.width, frame.height
            if getattr(frame, 'rotation', 0) in (90, 270, -90, -270):
                fw, fh = fh, fw
            if fw > 0 and fh > 0:
                frame_ratio = fw / fh
                pw, ph = pixmap.width(), pixmap.height()
                cam_ratio = pw / ph if ph > 0 else 1.0
                if abs(cam_ratio - frame_ratio) > 0.05:
                    if cam_ratio > frame_ratio:
                        new_w = int(ph * frame_ratio)
                        x_offset = (pw - new_w) // 2
                        pixmap = pixmap.copy(x_offset, 0, new_w, ph)
                    else:
                        new_h = int(pw / frame_ratio)
                        y_offset = (ph - new_h) // 2
                        pixmap = pixmap.copy(0, y_offset, pw, new_h)

        self._showing_preview = True
        # Show on the countdown live label (we're still on countdown page)
        label = self.countdown_live_label
        self._reset_countdown_ui()
        dpr = label.devicePixelRatioF()
        target_w = int(label.width() * dpr)
        target_h = int(label.height() * dpr)
        scaled = pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        label.setPixmap(scaled)

    def _continue_after_preview(self):
        """After showing captured photo, continue directly to next countdown.

        Wait for live view to have a frame before starting countdown.
        """
        # Guard: don't continue if session was cancelled
        if self.state == State.IDLE:
            print("[PREVIEW] Genegeerd — sessie is al geannuleerd")
            return
        self._showing_preview = False
        self._lv_wait_start = __import__('time').time()
        self._lv_wait_timer = QTimer()
        self._lv_wait_timer.setInterval(200)
        self._lv_wait_timer.timeout.connect(self._check_lv_ready)
        self._lv_wait_timer.start()

    def _finish_after_preview(self):
        """After showing last captured photo, build and show the strip."""
        self._showing_preview = False
        self._stop_live_view(blocking=False)
        if self.active_event:
            self.active_event.increment_photos(self.num_photos, config.EVENTS_DIR)
        self._create_and_show_strip()

    # ── Filterscherm (na elke foto) ─────────────────────────────
    # Links de foto, onderin de filterkeuzes, rechts de knoppen. Er wordt
    # ALLEEN op de gast gewacht: de sessie gaat pas door bij 'Volgende' (groen),
    # 'Foto opnieuw nemen' of 'Stoppen'. Geen auto-timeout.

    def _pil_to_qpixmap(self, pil_img):
        """Converteer een PIL-afbeelding naar een QPixmap (RGB of RGBA)."""
        if pil_img.mode == "RGBA":
            data = pil_img.tobytes("raw", "RGBA")
            qimg = QImage(data, pil_img.width, pil_img.height,
                          pil_img.width * 4, QImage.Format_RGBA8888)
        else:
            im = pil_img.convert("RGB")
            data = im.tobytes("raw", "RGB")
            qimg = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _build_filter_page(self):
        """Filterscherm: boven foto (links) + knoppen (rechts), onderaan een
        filterbalk over de volle breedte met 2 rijen van 8 filters.

        Compact gehouden zodat alles op één schermhoogte past; de foto schaalt
        altijd mee binnen z'n vak (zie _FitLabel)."""
        page = QWidget()
        page.setStyleSheet("background: #15151b;")
        self._filter_page = page
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ══ Bovenkant: foto (links) + knoppen (rechts) ══
        top = QWidget()
        top.setStyleSheet("background: transparent;")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(28, 18, 22, 10)
        top_lay.setSpacing(18)

        # — Links: compacte koptekst + grote foto —
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(2)

        self._filter_title = QLabel("Kies een filter")
        self._filter_title.setFont(QFont("DM Sans", 19, QFont.Bold))
        self._filter_title.setStyleSheet("color: #ffffff; background: transparent;")
        left_lay.addWidget(self._filter_title)

        self._filter_subtitle = QLabel("Tik onderin op een filter")
        self._filter_subtitle.setFont(QFont("DM Sans", 12))
        self._filter_subtitle.setStyleSheet(
            f"color: {config.COLOR_PRIMARY}; background: transparent;"
        )
        left_lay.addWidget(self._filter_subtitle)
        left_lay.addSpacing(6)

        # Zelf-schalende foto (past altijd binnen het vak, blaast de layout
        # niet op).
        self._filter_preview_label = _FitLabel("Foto laden…")
        self._filter_preview_label.setAlignment(Qt.AlignCenter)
        self._filter_preview_label.setStyleSheet(
            "color: #777; background: rgba(255,255,255,0.03); "
            "border-radius: 14px; font-size: 16px;"
        )
        left_lay.addWidget(self._filter_preview_label, stretch=1)
        top_lay.addWidget(left, stretch=1)

        # — Rechts: knoppenpaneel (kaart) —
        right = QWidget()
        right.setFixedWidth(340)
        right.setStyleSheet(
            "QWidget { background: rgba(255,255,255,0.05); border-radius: 18px; }"
        )
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(20, 22, 20, 22)
        right_lay.setSpacing(12)
        right_lay.addStretch()

        # Volgende foto maken — GROEN (2 regels zodat 'ie nooit afkapt)
        self._filter_next_btn = QPushButton("Volgende\nfoto maken")
        self._filter_next_btn.setCursor(Qt.PointingHandCursor)
        self._filter_next_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._filter_next_btn.setMinimumHeight(96)
        self._filter_next_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 16px; padding: 10px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
            f"QPushButton:pressed {{ background: #3A8B5E; }}"
        )
        self._filter_next_btn.clicked.connect(self._filter_next)
        right_lay.addWidget(self._filter_next_btn)

        # Foto opnieuw nemen — neutraal (2 regels)
        self._filter_retake_btn = QPushButton("Foto opnieuw\nnemen")
        self._filter_retake_btn.setCursor(Qt.PointingHandCursor)
        self._filter_retake_btn.setFont(QFont("DM Sans", 15))
        self._filter_retake_btn.setMinimumHeight(74)
        self._filter_retake_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.10); color: white; "
            "border: 1px solid rgba(255,255,255,0.22); border-radius: 14px; "
            "padding: 8px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.20); }"
        )
        self._filter_retake_btn.clicked.connect(self._filter_retake)
        right_lay.addWidget(self._filter_retake_btn)

        # Stoppen — rood
        self._filter_stop_btn = QPushButton("Stoppen")
        self._filter_stop_btn.setCursor(Qt.PointingHandCursor)
        self._filter_stop_btn.setFont(QFont("DM Sans", 15, QFont.Bold))
        self._filter_stop_btn.setMinimumHeight(54)
        self._filter_stop_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: white; "
            f"border: none; border-radius: 14px; padding: 6px; }}"
            f"QPushButton:hover {{ background: #A93223; }}"
        )
        self._filter_stop_btn.clicked.connect(self._filter_stop)
        right_lay.addWidget(self._filter_stop_btn)

        right_lay.addStretch()
        top_lay.addWidget(right)

        root.addWidget(top, stretch=1)

        # ══ Onderkant: filterbalk over de volle breedte (2 rijen × 8) ══
        bar = QWidget()
        bar.setObjectName("filterbar")
        bar.setStyleSheet(
            "QWidget#filterbar { background: rgba(255,255,255,0.04); "
            "border-top: 1px solid rgba(255,255,255,0.08); }"
        )
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(24, 8, 24, 12)
        bar_lay.setSpacing(6)

        self._filter_bar_header = QLabel("FILTERS")
        self._filter_bar_header.setAlignment(Qt.AlignCenter)
        self._filter_bar_header.setFont(QFont("DM Sans", 10, QFont.Bold))
        self._filter_bar_header.setStyleSheet(
            "color: rgba(255,255,255,0.40); background: transparent; "
            "letter-spacing: 2px;"
        )
        bar_lay.addWidget(self._filter_bar_header)

        grid_holder = QWidget()
        grid_holder.setStyleSheet("background: transparent;")
        self._filter_thumbs_layout = QGridLayout(grid_holder)
        self._filter_thumbs_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_thumbs_layout.setHorizontalSpacing(8)
        self._filter_thumbs_layout.setVerticalSpacing(6)
        # Verdeel de 8 kolommen gelijk over de volle breedte (links → rechts).
        for c in range(8):
            self._filter_thumbs_layout.setColumnStretch(c, 1)
        bar_lay.addWidget(grid_holder)

        root.addWidget(bar)

        # BELANGRIJK: voeg de pagina toe aan de stack, anders wijst
        # pages["filter"] naar een niet-bestaande index en negeert Qt de
        # setCurrentIndex stilletjes (blijft op de foto-preview hangen).
        self.stack.addWidget(page)

    def _clear_filter_thumbs(self):
        """Verwijder alle filter-thumbnailknoppen uit de strip."""
        self._filter_thumb_btns = {}
        lay = getattr(self, '_filter_thumbs_layout', None)
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _reset_thumbnail_at(self, idx):
        """Zet één onderste sessie-thumbnail terug op placeholder (na retake)."""
        if 0 <= idx < len(self.thumb_labels):
            thumb = self.thumb_labels[idx]
            thumb.setPixmap(QPixmap())
            thumb.setText(str(idx + 1))
            thumb.setStyleSheet(
                "background: rgba(255,255,255,0.15); "
                "border: 2px dashed rgba(255,255,255,0.4); border-radius: 8px; "
                "color: rgba(255,255,255,0.5);"
            )

    def _show_filter_screen(self, photo_path, photo_idx):
        """Toon het filterscherm voor de zojuist gemaakte foto."""
        # Sessie kan tijdens de 1-sec vertraging geannuleerd zijn.
        if self.state == State.IDLE:
            return
        self.state = State.FILTER
        # Houd live frames tegen zodat de gemaakte foto blijft staan.
        self._showing_preview = True
        cur = self._photo_filters.get(photo_idx, 'origineel')
        self._photo_filters[photo_idx] = cur
        self._filter_ctx = {'path': photo_path, 'idx': photo_idx,
                            'base': None, 'thumbs': None}
        last = (photo_idx >= self.num_photos - 1)
        self._filter_title.setText(f"Foto {photo_idx + 1} van {self.num_photos}")
        self._filter_subtitle.setText("Kies hieronder een filter")
        self._filter_next_btn.setText("Klaar" if last else "Volgende\nfoto maken")
        # Toon meteen de zojuist gemaakte foto (hergebruik de al-gespiegelde/
        # gecropte pixmap van _show_captured_preview) zodat het scherm nooit
        # leeg/'ladend' oogt. De async build verfijnt 'm + voegt filters toe.
        shown = False
        try:
            cap_pm = self.countdown_live_label.pixmap()
            if cap_pm is not None and not cap_pm.isNull():
                self._filter_preview_label.setSourcePixmap(cap_pm)
                shown = True
        except Exception:
            shown = False
        if not shown:
            self._filter_preview_label.clearSource()
            self._filter_preview_label.setText("Foto laden…")
        self._clear_filter_thumbs()
        self.stack.setCurrentIndex(self.pages["filter"])
        self._build_filter_thumbs_async(photo_path, photo_idx, cur)

    def _build_filter_thumbs_async(self, photo_path, photo_idx, current_fid):
        """Bouw (op een bg-thread) de grote preview + 16 filter-thumbnails."""
        self._filter_token += 1
        token = self._filter_token
        ev = self.active_event
        cam_mirror = bool(getattr(ev, 'camera_mirror', False)) if ev else False
        cam_rot = int(getattr(ev, 'camera_rotation', 0)) if ev else 0
        template = self.selected_template
        fw = fh = 0
        if template and 0 <= photo_idx < len(template.frames):
            frame = template.frames[photo_idx]
            fw, fh = frame.width, frame.height
            if getattr(frame, 'rotation', 0) in (90, 270, -90, -270):
                fw, fh = fh, fw

        def _work():
            try:
                from PIL import Image, ImageOps
                import filters as _filters
                with Image.open(photo_path) as raw:
                    img = ImageOps.exif_transpose(raw)
                    img = img.convert("RGB")
                if cam_mirror:
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if cam_rot:
                    img = img.rotate(-cam_rot, expand=True)
                # Center-crop naar frame-aspect (zoals de korte capture-preview)
                if fw > 0 and fh > 0:
                    tr = fw / fh
                    iw, ih = img.size
                    cr = iw / ih if ih else 1.0
                    if abs(cr - tr) > 0.02:
                        if cr > tr:
                            nw = int(ih * tr); x = (iw - nw) // 2
                            img = img.crop((x, 0, x + nw, ih))
                        else:
                            nh = int(iw / tr); y = (ih - nh) // 2
                            img = img.crop((0, y, iw, y + nh))
                base = img
                base.thumbnail((900, 900), Image.LANCZOS)
                preview = _filters.apply_filter(base, current_fid)

                def _round(im, rad=14):
                    from PIL import Image as _I, ImageDraw as _D
                    im = im.convert("RGBA")
                    w, h = im.size
                    mask = _I.new("L", (w, h), 0)
                    _D.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                                    radius=rad, fill=255)
                    im.putalpha(mask)
                    return im

                tbox = ImageOps.fit(base, (128, 80), Image.LANCZOS)
                thumbs = []
                for fid, label in _filters.FILTERS:
                    thumbs.append((fid, label, _round(_filters.apply_filter(tbox, fid), 11)))
                self._filter_ready_signal.emit({
                    'token': token, 'idx': photo_idx,
                    'base': base, 'preview': preview, 'thumbs': thumbs,
                })
            except Exception as e:
                print(f"[FILTER] Thumb-build fout: {e}")

        threading.Thread(target=_work, daemon=True).start()

    def _on_filter_thumbs_ready(self, payload):
        """Main-thread: ontvang preview + thumbnails en toon ze."""
        try:
            if not payload or payload.get('token') != self._filter_token:
                return  # verouderd — andere foto of al doorgegaan
            if self._filter_ctx is None:
                return
            self._filter_ctx['base'] = payload.get('base')
            self._filter_ctx['thumbs'] = payload.get('thumbs')
            cur = self._photo_filters.get(payload.get('idx'), 'origineel')
            self._populate_filter_thumbs(payload.get('thumbs') or [], cur)
            prev = payload.get('preview')
            if prev is not None:
                self._set_filter_preview(prev)
        except Exception as e:
            print(f"[FILTER] thumbs_ready fout: {e}")

    def _populate_filter_thumbs(self, thumbs, current_fid):
        """Vul de filterbalk met klikbare thumbnails — 2 rijen × 8 kolommen."""
        self._clear_filter_thumbs()
        style = (
            "QToolButton { background: rgba(255,255,255,0.05); color: #cfcfcf; "
            "border: 3px solid transparent; border-radius: 14px; padding: 4px; "
            "font-size: 12px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.12); color: #fff; }"
            "QToolButton:checked { border: 3px solid " + config.COLOR_PRIMARY + "; "
            "color: #fff; background: rgba(214,194,155,0.16); font-weight: bold; }"
        )
        cols = 8
        for i, (fid, label, pil_im) in enumerate(thumbs):
            pix = self._pil_to_qpixmap(pil_im)
            btn = QToolButton()
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIcon(QIcon(pix))
            btn.setIconSize(QSize(pix.width(), pix.height()))
            btn.setText(label)
            btn.setCheckable(True)
            btn.setChecked(fid == current_fid)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(style)
            btn.setFixedSize(150, 122)
            btn.clicked.connect(lambda _=False, f=fid: self._filter_select(f))
            r, c = divmod(i, cols)
            self._filter_thumbs_layout.addWidget(btn, r, c, alignment=Qt.AlignCenter)
            self._filter_thumb_btns[fid] = btn

    def _set_filter_preview(self, pil_img):
        """Toon de grote preview met het huidige filter. Het zelf-schalende
        _FitLabel zorgt dat 'ie altijd binnen het vak past (ook bij resize)."""
        if self._filter_ctx is not None:
            self._filter_ctx['preview_pil'] = pil_img
        self._filter_preview_label.setSourcePixmap(self._pil_to_qpixmap(pil_img))

    def _filter_select(self, fid):
        """Gast koos een filter — markeer en herrender de grote preview."""
        if self._filter_ctx is None:
            return
        idx = self._filter_ctx.get('idx')
        self._photo_filters[idx] = fid
        for f, btn in self._filter_thumb_btns.items():
            btn.setChecked(f == fid)
        base = self._filter_ctx.get('base')
        if base is not None:
            try:
                import filters as _filters
                self._set_filter_preview(_filters.apply_filter(base, fid))
            except Exception as e:
                print(f"[FILTER] Preview-render fout: {e}")

    def _enqueue_photo_filtered(self, dest, fid):
        """Upload de losse foto — gefilterd indien een filter gekozen is."""
        if not fid or fid == 'origineel':
            self._maybe_enqueue_linked(dest)
            return
        # In standalone-modus wordt er toch niet geüpload — sla het
        # wegschrijven van een gefilterde kopie dan over (geen rommel in raw/).
        ev = self.active_event
        linked = bool(ev and getattr(ev, 'booth_mode', 'standalone') == 'linked'
                      and getattr(ev, 'linked_booking_id', ''))
        if not linked:
            return

        def _work():
            path = dest
            try:
                from PIL import Image
                import filters as _filters
                with Image.open(dest) as im:
                    out = _filters.apply_filter(im, fid)
                root, ext = os.path.splitext(dest)
                fpath = f"{root}_f{ext or '.jpg'}"
                out.save(fpath, quality=95)
                path = fpath
            except Exception as e:
                print(f"[FILTER] Upload-filter mislukt: {e}")
                path = dest
            self._maybe_enqueue_linked(path)

        threading.Thread(target=_work, daemon=True).start()

    def _filter_next(self):
        """Groene knop: pas filter toe (strip + upload) en ga door."""
        # Consumeer de context meteen → waterdicht tegen dubbelklik (de
        # state-overgang gebeurt pas async via timer/strip-build).
        ctx = self._filter_ctx
        if ctx is None:
            return
        self._filter_ctx = None
        idx = ctx.get('idx', self.current_photo_num)
        dest = ctx.get('path')
        fid = self._photo_filters.get(idx, 'origineel')
        self._filter_token += 1  # stop eventuele lopende thumb-build
        self._showing_preview = False
        if dest:
            # Verwerk voor de strip (past het filter toe) + upload de losse foto.
            self._process_photo_for_strip(dest, idx)
            self._enqueue_photo_filtered(dest, fid)
        self.current_photo_num = idx + 1
        if self.current_photo_num < self.num_photos:
            self._continue_after_preview()
        else:
            # Geef de laatste foto een kleine voorsprong om te verwerken
            # voordat de strip gebouwd wordt (matcht het klassieke gedrag).
            QTimer.singleShot(500, self._finish_after_preview)

    def _filter_retake(self):
        """Maak de huidige foto opnieuw (verwijder de zojuist gemaakte)."""
        ctx = self._filter_ctx
        if ctx is None:
            return
        self._filter_ctx = None
        idx = ctx.get('idx', self.current_photo_num)
        self._filter_token += 1
        # Verwijder de zojuist gemaakte foto + reset de thumbnail.
        if self.photos and len(self.photos) == idx + 1:
            try:
                self.photos.pop()
            except Exception:
                pass
        self._photo_filters.pop(idx, None)
        self._reset_thumbnail_at(idx)
        self.current_photo_num = idx
        self._showing_preview = False
        self._continue_after_preview()

    def _filter_stop(self):
        """Stop de sessie volledig en ga terug naar idle."""
        self._filter_token += 1
        self._filter_ctx = None
        self._showing_preview = False
        self._cancel_session()

    def _auto_next_photo(self):
        """Automatically start countdown for next photo."""
        if self.state == State.PREVIEW and self.current_photo_num < self.num_photos:
            self._start_countdown()

    def _on_capture_failed(self, error_msg):
        import time as _time
        if hasattr(self, '_capture_t0'):
            total_ms = (_time.time() - self._capture_t0) * 1000
            print(f"[TIMING] Capture MISLUKT na {total_ms:.0f}ms: {error_msg}")
        # Unfreeze and reset UI on failure too
        self._live_view_frozen = False
        self._reset_countdown_ui()
        self._end_flash_effect()
        self._stop_live_view(blocking=False)
        # Try to restart live view so camera doesn't stay stuck
        try:
            self.camera.start_live_view()
        except Exception:
            pass
        self._show_error(error_msg)

    def _preload_background(self):
        """Pre-load and resize the background image in a background thread."""
        template = self.selected_template
        bg_path = ""
        if self.active_event and self.active_event.background_path:
            bg_path = self.active_event.background_path
        elif template and template.background_path:
            bg_path = template.background_path
        self._bg_preload_done = threading.Event()
        print(f"[PRELOAD] Start preload voor template '{template.name if template else '?'}', bg='{bg_path}'")

        def _load():
            from PIL import Image
            PRINT_W, PRINT_H = 1200, 1800
            try:
                if bg_path and os.path.isfile(bg_path):
                    with Image.open(bg_path) as bg_raw:
                        bg = bg_raw.convert("RGB")
                    # Scale background to full print size
                    result = bg.resize((PRINT_W, PRINT_H), Image.LANCZOS)
                    del bg
                    self._strip_bg = result
                    print(f"[PRELOAD] Achtergrond geladen: {bg_path} ({result.size})")
                else:
                    self._strip_bg = None
                    print(f"[PRELOAD] Geen achtergrond gevonden (pad='{bg_path}', bestaat={os.path.isfile(bg_path) if bg_path else 'n/a'})")
            except Exception as e:
                print(f"[PRELOAD] FOUT bij laden achtergrond: {e}")
                self._strip_bg = None
            finally:
                self._bg_preload_done.set()

        threading.Thread(target=_load, daemon=True).start()

    def _process_photo_for_strip(self, photo_path, frame_index):
        """Pre-resize a photo to fit its template frame (called after each capture)."""
        template = self.selected_template
        if not template or frame_index >= len(template.frames):
            return

        frame = template.frames[frame_index]

        def _process():
            from PIL import Image, ImageOps
            try:
                if not os.path.isfile(photo_path):
                    print(f"[STRIP] Bestand niet gevonden: {photo_path}")
                    return
                with Image.open(photo_path) as raw_img:
                    # Apply EXIF rotation (camera may store rotated orientation)
                    img = ImageOps.exif_transpose(raw_img)
                    img = img.convert("RGB")
                # Apply camera mirror + rotation (user settings, not frame rotation)
                ev = self.active_event
                if ev and ev.camera_mirror:
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if ev and ev.camera_rotation:
                    img = img.rotate(-ev.camera_rotation, expand=True)
                # File handle is now closed; continue processing in memory
                if getattr(frame, 'rotation', 0) != 0:
                    img = img.rotate(-frame.rotation, expand=True)
                # Auto-orient: als frame en foto verschillende aspect-oriëntatie
                # hebben (bv. landscape frame + portrait camera-foto), draai
                # de foto 90° zodat z'n long-axis matcht met de long-axis van
                # het frame. Voorkomt sterk croppen / verkeerd uitsnijden bij
                # liggende templates zoals '4 foto's op een vel'.
                _iw, _ih = img.size
                frame_land = frame.width > frame.height
                img_land = _iw > _ih
                if frame_land != img_land:
                    img = img.rotate(90, expand=True)
                    print(f"[STRIP] Foto {frame_index + 1} auto-georiënteerd "
                          f"(frame={'L' if frame_land else 'P'}, "
                          f"foto={'L' if img_land else 'P'} → draai 90°)")
                # Crop-to-fit: vult het frame exact, snijdt overtollige randen af
                img = ImageOps.fit(img, (frame.width, frame.height), Image.LANCZOS)
                # Pas het door de gast gekozen filter toe (na elke foto). Het
                # filter komt zo in de geprinte/gedeelde strip terecht.
                fid = None
                try:
                    fid = self._photo_filters.get(frame_index)
                except Exception:
                    fid = None
                if fid and fid != 'origineel':
                    try:
                        import filters as _filters
                        img = _filters.apply_filter(img, fid)
                        print(f"[STRIP] Foto {frame_index + 1} filter '{fid}' toegepast")
                    except Exception as _fe:
                        print(f"[STRIP] Filter '{fid}' mislukt: {_fe}")
                with self._processed_lock:
                    self._processed_photos.append((frame_index, img))
                print(f"[STRIP] Foto {frame_index + 1} voorverwerkt ({frame.width}x{frame.height})")
            except Exception as e:
                print(f"[STRIP] FOUT bij verwerken foto: {e}")

        threading.Thread(target=_process, daemon=True).start()

    def _create_and_show_strip(self):
        """Combine photos into strip and show review page."""
        self.strip_path = self._build_strip_image()
        if self.strip_path:
            self._create_boomerang()
            self._start_cloud_upload()  # Start upload early for max time
            # Linked-modus: bij DNP triple is strip_path het 3-up print-vel
            # (alleen intern voor de printer, niet voor de gast) en
            # _display_strip_path de echte portrait strip. Upload alleen de
            # versie die voor de gast bedoeld is.
            if self._display_strip_path and self._display_strip_path != self.strip_path:
                # DNP flow → alleen portrait uploaden, NIET de 3-up print sheet
                self._maybe_enqueue_linked(self._display_strip_path, prefix="strip_portrait_")
            elif self._single_strip_path and os.path.isfile(self._single_strip_path):
                # Canon flow → upload 600x1800 enkele strip (niet het 1200x1800
                # vel met 2 gedupliceerde helften).
                self._maybe_enqueue_linked(self._single_strip_path, prefix="strip_")
            else:
                # Fallback: enkel strip_path beschikbaar (bv. test of niet-linked)
                self._maybe_enqueue_linked(self.strip_path, prefix="strip_")
            self._go_review()
        else:
            self._show_error(t("error_cannot_make_strip"))

    def _maybe_enqueue_linked(self, file_path: str, prefix: str = "",
                              kind: str = "") -> None:
        """Voeg foto toe aan upload-queue als Linked-modus actief en gekoppeld.

        kind ('photo'/'strip'/'gif') + self.session_id reizen mee naar de
        cloud zodat het digitale album in het klantenportaal per sessie kan
        groeperen met de strip als held. Zonder expliciete kind wordt 'ie
        afgeleid van het prefix/de extensie.
        """
        ev = self.active_event
        if not ev:
            return
        if getattr(ev, 'booth_mode', 'standalone') != 'linked':
            return
        booking_id = getattr(ev, 'linked_booking_id', '')
        if not booking_id or not file_path or not os.path.isfile(file_path):
            return
        if not kind:
            if file_path.lower().endswith('.gif'):
                kind = 'gif'
            elif prefix.startswith('strip'):
                kind = 'strip'
            else:
                kind = 'photo'
        try:
            from cloud_uploader import enqueue
            enqueue(booking_id, file_path,
                    session_id=getattr(self, 'session_id', '') or '',
                    kind=kind)
        except Exception as e:
            print(f"[LINKED] Enqueue fout: {e}")

    def _create_boomerang(self):
        """Create boomerang GIF from buffered frames (non-blocking)."""
        if not getattr(self, '_boomerang_frames', None):
            return
        # Feature gate: boomerang requires Professional plan
        if not self._is_pro_feature("boomerang"):
            print("[BOOMERANG] Overgeslagen — Starter abonnement")
            return

        from boomerang import BoomerangThread

        # Boomerang naar photos/<event>/gif/
        gif_dir = self._get_gif_dir()
        self._boomerang_path = os.path.join(
            gif_dir, self._timestamp_filename(ext=".gif")
        )

        self._boomerang_thread = BoomerangThread(
            frames=self._boomerang_frames,
            output_path=self._boomerang_path,
            target_size=getattr(config, 'BOOMERANG_SIZE', (480, 320)),
            frame_duration_ms=getattr(config, 'BOOMERANG_FRAME_DURATION_MS', 66),
        )
        self._boomerang_thread.gif_complete.connect(self._on_boomerang_complete)
        self._boomerang_thread.gif_failed.connect(self._on_boomerang_failed)
        self._boomerang_thread.start()
        self._boomerang_frames = None  # free memory

    def _on_boomerang_complete(self, gif_path):
        print(f"[BOOMERANG] GIF klaar: {gif_path}")
        self._boomerang_path = gif_path
        # Upload boomerang to cloud separately (cloud upload started before boomerang was ready)
        self._upload_boomerang_to_cloud(gif_path)
        # Linked-modus: gif ook naar het digitale album in het klantenportaal
        self._maybe_enqueue_linked(gif_path, kind='gif')

    def _upload_boomerang_to_cloud(self, gif_path):
        """Upload boomerang GIF to R2 after it's created (separate from main upload)."""
        if not self._is_logged_in() or not getattr(config, 'CLOUD_UPLOAD_ENABLED', False):
            return
        if not self.session_id or not gif_path or not os.path.exists(gif_path):
            return
        try:
            from cloud_storage import upload_to_r2_direct
            import threading
            def _upload():
                try:
                    upload_to_r2_direct(
                        gif_path,
                        f"{self.session_id}/boomerang.gif",
                        content_type='image/gif'
                    )
                    print(f"[CLOUD] Boomerang apart geüpload: {self.session_id}/boomerang.gif")
                except Exception as e:
                    print(f"[CLOUD] Boomerang upload mislukt: {e}")
            threading.Thread(target=_upload, daemon=True).start()
        except ImportError:
            pass

    def _on_boomerang_failed(self, error_msg):
        print(f"[BOOMERANG] Mislukt: {error_msg}")
        self._boomerang_path = None

    # ── Email ─────────────────────────────────

    def _go_email_input(self):
        """Show email input page."""
        self.state = State.EMAIL_INPUT
        self.review_timer.stop()
        self._sharing_countdown_timer.stop()
        self.done_timer.stop()
        if hasattr(self, '_qr_overlay'):
            self._qr_overlay.hide()
        self.email_input.clear()
        self.email_status_label.setText("")
        self.email_send_btn.setEnabled(True)
        self.stack.setCurrentIndex(self.pages["email_input"])

        # Auto-timeout: go idle after sharing_timeout seconds of inactivity
        sharing_secs = self.active_event.sharing_timeout if self.active_event else 30
        try:
            self.done_timer.timeout.disconnect()
        except TypeError:
            pass
        self.done_timer.timeout.connect(self._go_idle)
        self.done_timer.start(sharing_secs * 1000)

    def _skip_email(self):
        """Skip email and go back to sharing screen."""
        self.done_timer.stop()
        self._back_to_sharing()

    def _back_to_sharing(self):
        """Return to the sharing/review screen from email input."""
        self.done_timer.stop()
        self.state = State.REVIEW
        self.stack.setCurrentIndex(self.pages["review"])
        # Restart the sharing countdown
        self._start_sharing_countdown()

    def _send_email(self):
        """Validate email and send photos with per-event settings."""
        import re
        email = self.email_input.text().strip()

        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            self.email_status_label.setText(t("dc_invalid_email"))
            self.email_status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return

        self.done_timer.stop()

        try:
            from email_sender import load_gmail_config, EmailThread

            gmail = load_gmail_config()
            if not gmail:
                self.email_status_label.setText(t("dc_no_gmail"))
                self.email_status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
                return

            # Build attachment list based on event preferences
            ev = self.active_event
            attachments = []

            # Strip (use single strip if enabled). Display-paden geven gedraaide
            # versie wanneer template alle frames 90/270 heeft, anders origineel.
            if (not ev or ev.email_send_strip) and self.strip_path:
                share_strip = self.display_single_strip_path or self.display_strip_path
                attachments.append(share_strip)

            # Original photos
            if ev and ev.email_send_originals:
                for photo_path in self.photos:
                    if photo_path and os.path.exists(photo_path):
                        attachments.append(photo_path)

            # Boomerang GIF
            if (not ev or ev.email_send_gif) and getattr(self, '_boomerang_path', None):
                attachments.append(self._boomerang_path)

            # Custom subject and body from event
            subject = ev.email_subject if ev and ev.email_subject else None
            body = ev.email_body if ev and ev.email_body else None

            # Start email in background, go back to sharing immediately
            self._email_thread = EmailThread(email, attachments,
                                             subject=subject, body=body)
            self._email_thread.email_sent.connect(self._on_email_sent)
            self._email_thread.email_failed.connect(self._on_email_failed)
            self._email_thread.start()

            print(f"[EMAIL] Verzenden naar {email} op achtergrond gestart")

            # Save email address to CSV if collection is enabled
            self._save_email_to_csv(email)

        except ImportError as e:
            self.email_status_label.setText(t("error_module_missing", error=str(e)))
            self.email_status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return

        # Go back to sharing screen and show confirmation
        self._back_to_sharing()
        self._show_email_toast(t("email_sending_confirm"), success=True)

    def _on_email_sent(self):
        print("[EMAIL] Verzonden!")
        self._show_email_toast(t("email_sent"), success=True)

    def _on_email_failed(self, error_msg):
        print(f"[EMAIL] Verzenden mislukt: {error_msg}")
        short_msg = error_msg[:100] if len(error_msg) > 100 else error_msg
        self._show_email_toast(t("email_failed", error=short_msg), success=False)

    def _show_email_toast(self, text, success=True):
        """Show a floating toast notification on any screen."""
        color = config.COLOR_SUCCESS if success else config.COLOR_DANGER
        bg = "rgba(0,0,0,0.85)"
        toast = QLabel(text, self)
        toast.setAlignment(Qt.AlignCenter)
        toast.setWordWrap(True)
        toast.setFont(QFont("DM Sans", 13, QFont.Bold))
        toast.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {color}; "
            f"border: 2px solid {color}; border-radius: 12px; padding: 14px 24px; }}"
        )
        toast.setFixedWidth(min(400, self.width() - 40))
        toast.adjustSize()
        # Position at bottom center
        screen = self.screen()
        sw = screen.geometry().width() if screen else self.width()
        sh = screen.geometry().height() if screen else self.height()
        tx = (sw - toast.width()) // 2
        ty = sh - toast.height() - 80
        toast.move(tx, ty)
        toast.show()
        toast.raise_()
        # Auto-hide after 5 seconds
        duration = 5000 if success else 8000
        QTimer.singleShot(duration, toast.deleteLater)

    def _build_strip_image(self):
        """Create a 4x6 image with 2 identical strips using template frames.

        Uses pre-loaded background and pre-resized photos for speed.
        Falls back to loading from disk if pre-processing isn't done yet.
        """
        try:
            from PIL import Image
            import time as _time

            template = self.selected_template
            if not template:
                print("[STRIP] FOUT: Geen template geselecteerd")
                return None

            # DNP verhuur-flow: triple strip → portrait 5x10cm, 3x gestapeld op vel.
            if getattr(template, 'is_triple_strip', False):
                return self._build_triple_strip_image(template)

            # Detecteer landscape canvas via frame-extents. Eerder gebruikten
            # we een `cloud_w >= 1500` heuristic, maar die brak wanneer de
            # operator de frames kleiner schaalde — dan viel max_x onder 1500
            # en werd het ten onrechte als portrait gerenderd. Nu pure
            # max_x > max_y check; landscape canvas hoort daar altijd bij.
            _frames = list(template.frames) if template and template.frames else []
            cloud_w = max((f.x + f.width) for f in _frames) if _frames else 0
            cloud_h = max((f.y + f.height) for f in _frames) if _frames else 0
            # Landscape-detectie: als frames buiten de 1200-px portrait-breedte
            # vallen, MOET het canvas landscape zijn (1800×1200). We checken
            # NIET op is_double_strip — die flag bleek in oude DB-rijen niet
            # consistent gezet, met als gevolg dat 4-foto landscape templates
            # ten onrechte als portrait gerenderd werden (helft eraf, lege
            # ruimte onder). Pure positie-check is fool-proof.
            is_landscape = cloud_w > 1200 and cloud_w > cloud_h
            print(f"[STRIP] Canvas-detect: max_xy=({cloud_w},{cloud_h}), "
                  f"is_double={template.is_double_strip}, "
                  f"is_triple={getattr(template,'is_triple_strip',False)}, "
                  f"is_4x3={getattr(template,'is_4x3_strip',False)} → "
                  f"landscape={is_landscape}")
            if getattr(template, 'is_4x3_strip', False):
                PRINT_W = 1200
                PRINT_H = 900
            elif is_landscape:
                # Landscape 4x6: bouw op 1800x1200 canvas, roteer 90° voor print
                PRINT_W = 1800
                PRINT_H = 1200
            else:
                PRINT_W = 1200
                PRINT_H = 1800
            STRIP_W = PRINT_W // 2

            # Wait briefly for any pending photo processing (max 500ms)
            # Use short sleeps with processEvents to keep UI responsive
            for _wait in range(10):
                with self._processed_lock:
                    ready = len(self._processed_photos) >= self.num_photos
                if ready:
                    break
                _time.sleep(0.03)  # Short sleep, photos usually already done

            # Wait for background preload to finish (max 3 seconds)
            if hasattr(self, '_bg_preload_done'):
                self._bg_preload_done.wait(timeout=3.0)

            # Use pre-loaded background, or load synchronously
            bg_path = ""
            if self.active_event and self.active_event.background_path:
                bg_path = self.active_event.background_path
            elif template and template.background_path:
                bg_path = template.background_path
            print(f"[STRIP] Template: '{template.name}', bg_path='{bg_path}', _strip_bg={'JA' if self._strip_bg else 'NEE'}, doel=({PRINT_W},{PRINT_H})")
            if self._strip_bg:
                strip = self._strip_bg.copy()
                # BUG-FIX: _preload_background gebruikt hardcoded 1200×1800.
                # Bij een landscape template (1800×1200) klopt die preload-grootte
                # niet — daardoor werd het strip-canvas portrait, frames erbuiten
                # geclipt, en de gast zag de helft van z'n foto's niet. Resize
                # de preload-BG hier alsnog naar de juiste doelgrootte.
                if strip.size != (PRINT_W, PRINT_H):
                    print(f"[STRIP] Preload-BG resize {strip.size} → ({PRINT_W},{PRINT_H})")
                    strip = strip.resize((PRINT_W, PRINT_H), Image.LANCZOS)
                else:
                    print("[STRIP] Achtergrond uit preload gebruikt")
            elif bg_path and os.path.isfile(bg_path):
                print(f"[STRIP] Achtergrond synchroon laden: {bg_path}")
                with Image.open(bg_path) as bg_raw:
                    bg = bg_raw.convert("RGB")
                # Scale background to full print size
                strip = bg.resize((PRINT_W, PRINT_H), Image.LANCZOS)
                del bg
                print("[STRIP] Achtergrond synchroon geladen OK")
            else:
                strip = Image.new("RGB", (PRINT_W, PRINT_H), (255, 255, 255))
                print("[STRIP] Geen achtergrond — wit gebruikt")

            # Build lookup of pre-processed photos by frame index
            with self._processed_lock:
                processed = {idx: img for idx, img in self._processed_photos}

            # The HiTi printer ALWAYS uses split paper (cuts into 2 strips).
            # So ALL templates must produce a 1200x1800 image where left and
            # right halves (each 600x1800) are IDENTICAL.
            #
            # is_double_strip=False: frames designed for 600px canvas → duplicate
            # is_double_strip=True:  frames designed for 1200px canvas → scale down
            #                        to 600px, then duplicate

            for i, frame in enumerate(template.frames):
                if i in processed:
                    img = processed[i]
                elif i < len(self.photos) and os.path.isfile(self.photos[i]):
                    from PIL import ImageOps
                    with Image.open(self.photos[i]) as raw_img:
                        img = ImageOps.exif_transpose(raw_img)
                        img = img.convert("RGB")
                    # Apply camera mirror + rotation (same as _process_photo_for_strip)
                    ev = self.active_event
                    if ev and ev.camera_mirror:
                        img = img.transpose(Image.FLIP_LEFT_RIGHT)
                    if ev and ev.camera_rotation:
                        img = img.rotate(-ev.camera_rotation, expand=True)
                    if getattr(frame, 'rotation', 0) != 0:
                        img = img.rotate(-frame.rotation, expand=True)
                    # Auto-orient: zie _process_photo_for_strip — match aspect
                    # van foto met aspect van frame zodat liggende templates
                    # ook correct werken met een portrait camera-opname.
                    _iw, _ih = img.size
                    if (frame.width > frame.height) != (_iw > _ih):
                        img = img.rotate(90, expand=True)
                    img = ImageOps.fit(img, (frame.width, frame.height), Image.LANCZOS)
                else:
                    continue

                # Bij landscape (vol-vel) of expliciete is_double_strip is
                # het canvas één geheel — gewoon één keer pasten. Anders
                # (smal 600-px Canon mirror-template) dupliceren naar rechter
                # helft. is_landscape detecteerden we eerder via frame-extents
                # (cloud_w > 1200) — die overschrijft de is_double_strip flag
                # omdat die in oudere DB-rijen onbetrouwbaar bleek.
                if template.is_double_strip or is_landscape:
                    strip.paste(img, (frame.x, frame.y))
                else:
                    # 600px canvas — frames fit in one half, duplicate to other half
                    strip.paste(img, (frame.x, frame.y))
                    strip.paste(img, (frame.x + STRIP_W, frame.y))

            # Add watermark if not logged in
            if not self._is_logged_in():
                try:
                    from PIL import ImageDraw, ImageFont
                    watermark_text = "BOOTHAROO.COM"
                    overlay = Image.new("RGBA", strip.size, (0, 0, 0, 0))
                    draw = ImageDraw.Draw(overlay)
                    # Try to use a large bold font, fall back to default
                    font_size = 72
                    try:
                        font = ImageFont.truetype("arialbd.ttf", font_size)
                    except (OSError, IOError):
                        try:
                            font = ImageFont.truetype("arial.ttf", font_size)
                        except (OSError, IOError):
                            font = ImageFont.load_default()
                    # Draw watermark diagonally across the strip, repeated
                    tw = draw.textlength(watermark_text, font=font) if hasattr(draw, 'textlength') else font_size * len(watermark_text) * 0.6
                    for y_pos in range(0, PRINT_H, 300):
                        for x_pos in range(-200, PRINT_W, int(tw) + 100):
                            draw.text((x_pos, y_pos), watermark_text,
                                      fill=(255, 255, 255, 100), font=font)
                    # Rotate overlay for diagonal effect
                    overlay = overlay.rotate(30, expand=False, center=(PRINT_W // 2, PRINT_H // 2))
                    # Composite onto strip
                    strip = strip.convert("RGBA")
                    strip = Image.alpha_composite(strip, overlay)
                    strip = strip.convert("RGB")
                    print("[STRIP] Watermerk toegevoegd (niet ingelogd)")
                except Exception as wm_err:
                    print(f"[STRIP] Watermerk fout: {wm_err}")

            # Landscape canvas (bv. 1800x1200) → bewaar 2 versies:
            #   - display-versie (landscape, voor sharing-screen)
            #   - print-versie (90° gedraaid, voor portrait 4x6 paper)
            strip_dir = self._get_strips_dir()
            landscape_display_path = None
            if is_landscape:
                landscape_display_path = os.path.join(
                    strip_dir,
                    self._timestamp_filename(ext=".jpg", suffix="_landscape")
                )
                try:
                    strip.save(landscape_display_path, "JPEG", quality=95)
                    print(f"[STRIP] Landscape display opgeslagen: {landscape_display_path}")
                except OSError as e:
                    print(f"[STRIP] Landscape display save fout: {e}")
                    landscape_display_path = None
                # Nu roteren voor de print-versie (op portrait 4x6 paper)
                strip = strip.rotate(-90, expand=True)
                print(f"[STRIP] Landscape canvas → 90° gedraaid voor portrait print "
                      f"(eindgrootte {strip.size})")

            # Strip-composiet (print-versie) naar photos/<event>/strips/
            strip_path = os.path.join(strip_dir, self._timestamp_filename(ext=".jpg"))
            try:
                strip.save(strip_path, "JPEG", quality=95)
            except OSError as disk_err:
                print(f"[STRIP] FOUT bij opslaan (disk vol?): {disk_err}")
                # Try lower quality as fallback
                try:
                    strip.save(strip_path, "JPEG", quality=60)
                except OSError:
                    print(f"[STRIP] Opslaan definitief mislukt")
                    return None
            print(f"[STRIP] Fotostrip opgeslagen: {strip_path}")

            # Generate single strip (left half) for sharing if enabled, OR
            # altijd in Canon linked-modus (cloud-gallery moet de 600x1800
            # single strip tonen, niet het 1200x1800 vel met dubbele helften).
            self._single_strip_path = None
            ev = self.active_event
            # Force single strip upload bij linked Canon (4x6) flow zodat de
            # cloud-gallery de 600x1800 single strip ziet i.p.v. het
            # 1200x1800 vel met gedupliceerde helften.
            _pm = getattr(ev, 'printer_mode', '3strips') if ev else '3strips'
            _is_canon_mode = _pm in ('4x6', 'canon')  # canon = legacy
            _force_single = bool(
                ev
                and getattr(ev, 'booth_mode', 'standalone') == 'linked'
                and _is_canon_mode
                and not template.is_double_strip
            )
            if ev and (ev.share_single_strip or _force_single) and PRINT_W >= 1200:
                try:
                    from PIL import Image as _Img
                    full = _Img.open(strip_path)
                    half_w = full.width // 2
                    single = full.crop((0, 0, half_w, full.height))
                    # Single-strip naar strips/ met _enkel suffix.
                    # Gebruik basenaam van de strip zodat de timestamp matched.
                    base = os.path.splitext(os.path.basename(strip_path))[0]
                    single_path = os.path.join(
                        os.path.dirname(strip_path), f"{base}_enkel.jpg"
                    )
                    single.save(single_path, "JPEG", quality=95)
                    self._single_strip_path = single_path
                    del single, full
                    print(f"[STRIP] Enkele strip opgeslagen: {single_path}")
                except Exception as e:
                    print(f"[STRIP] Enkele strip fout: {e}")

            # Bouw display-versies (alleen gedraaid wanneer de template
            # uitsluitend frames met rotation 90 of 270 heeft). PRINT blijft
            # altijd het originele strip_path gebruiken, niet deze.
            self._maybe_create_rotated_display_strips(strip_path)

            # Landscape templates: gebruik de landscape-versie als display
            # zodat de gast op sharing-screen het ontwerp horizontaal ziet
            # (zoals klant het ontwierp), niet de 90° rotated print-versie.
            if landscape_display_path and os.path.isfile(landscape_display_path):
                self._display_strip_path = landscape_display_path
                print(f"[STRIP] Display-pad → landscape versie")

            # Free processed photos immediately (can be 24-48MB)
            with self._processed_lock:
                self._processed_photos.clear()
            self._strip_bg = None
            del processed, strip
            gc.collect()
            print("[STRIP] Geheugen vrijgegeven na strip build")

            return strip_path

        except Exception as e:
            print(f"[STRIP] FOUT bij maken strip: {e}")
            # Still try to free memory on error
            with self._processed_lock:
                self._processed_photos.clear()
            self._strip_bg = None
            gc.collect()
            return None

    def _build_triple_strip_image(self, template):
        """Bouw DNP triple-strip output.

        Strip-ontwerp: 600x1200 portrait canvas (= 2x4 inch = 5x10 cm).
        Print vel: 1200x1800 met 3x gedraaide strip gestapeld (y=0, 600, 1200).
        DNP-driver met 2-inch cut snijdt het vel in 3 fysieke strips.

        Sla 2 files op:
        - <ts>_portrait.jpg → 600x1200 strip voor review/QR/sharing
        - <ts>.jpg          → 1200x1800 print sheet voor de printer
        """
        try:
            from PIL import Image, ImageOps
            import time as _time

            STRIP_W = 600
            STRIP_H = 1200
            PRINT_W = 1200
            PRINT_H = 1800

            # Wacht kort op pending photo processing (zelfde patroon als hoofd-build)
            for _wait in range(10):
                with self._processed_lock:
                    ready = len(self._processed_photos) >= self.num_photos
                if ready:
                    break
                _time.sleep(0.03)

            # Achtergrond laden (event-specifiek of template-specifiek)
            bg_path = ""
            if self.active_event and self.active_event.background_path:
                bg_path = self.active_event.background_path
            elif template.background_path:
                bg_path = template.background_path

            if bg_path and os.path.isfile(bg_path):
                with Image.open(bg_path) as bg_raw:
                    bg = bg_raw.convert("RGB")
                strip = bg.resize((STRIP_W, STRIP_H), Image.LANCZOS)
                del bg
                print(f"[DNP-STRIP] Achtergrond geladen: {bg_path}")
            else:
                strip = Image.new("RGB", (STRIP_W, STRIP_H), (255, 255, 255))
                print("[DNP-STRIP] Geen achtergrond — wit gebruikt")

            # Foto's in frames pasten (zelfde transformatielogica als hoofd-build)
            with self._processed_lock:
                processed = {idx: img for idx, img in self._processed_photos}

            for i, frame in enumerate(template.frames):
                if i in processed:
                    img = processed[i]
                elif i < len(self.photos) and os.path.isfile(self.photos[i]):
                    with Image.open(self.photos[i]) as raw_img:
                        img = ImageOps.exif_transpose(raw_img).convert("RGB")
                    ev = self.active_event
                    if ev and ev.camera_mirror:
                        img = img.transpose(Image.FLIP_LEFT_RIGHT)
                    if ev and ev.camera_rotation:
                        img = img.rotate(-ev.camera_rotation, expand=True)
                    if getattr(frame, 'rotation', 0) != 0:
                        img = img.rotate(-frame.rotation, expand=True)
                    # Auto-orient: zie _process_photo_for_strip
                    _iw, _ih = img.size
                    if (frame.width > frame.height) != (_iw > _ih):
                        img = img.rotate(90, expand=True)
                    img = ImageOps.fit(img, (frame.width, frame.height), Image.LANCZOS)
                else:
                    continue
                strip.paste(img, (frame.x, frame.y))

            # Portrait variant opslaan voor review/QR/sharing
            strip_dir = self._get_strips_dir()
            portrait_path = os.path.join(
                strip_dir,
                self._timestamp_filename(ext=".jpg", suffix="portrait"),
            )
            try:
                strip.save(portrait_path, "JPEG", quality=95)
            except OSError as disk_err:
                print(f"[DNP-STRIP] FOUT bij opslaan portrait (disk vol?): {disk_err}")
                return None
            print(f"[DNP-STRIP] Portrait strip opgeslagen: {portrait_path}")

            # 90° draaien → 1200x600 landscape
            landscape = strip.rotate(-90, expand=True)

            # Print vel bouwen: 1200x1800 wit, 3x landscape strip gestapeld
            print_sheet = Image.new("RGB", (PRINT_W, PRINT_H), (255, 255, 255))
            for row in range(3):
                print_sheet.paste(landscape, (0, row * STRIP_W))  # y = 0, 600, 1200

            # Print sheet opslaan (dit is wat de printer krijgt)
            print_path = os.path.join(
                strip_dir,
                self._timestamp_filename(ext=".jpg"),
            )
            try:
                print_sheet.save(print_path, "JPEG", quality=95)
            except OSError as disk_err:
                print(f"[DNP-STRIP] FOUT bij opslaan print-sheet: {disk_err}")
                return None
            print(f"[DNP-STRIP] Print-sheet opgeslagen: {print_path}")

            # Display + sharing pad zetten naar portrait variant
            self._display_strip_path = portrait_path
            self._display_single_strip_path = portrait_path
            self._single_strip_path = portrait_path

            # Geheugen vrijgeven
            with self._processed_lock:
                self._processed_photos.clear()
            self._strip_bg = None
            del strip, landscape, print_sheet, processed
            gc.collect()

            return print_path

        except Exception as e:
            print(f"[DNP-STRIP] FOUT bij maken triple strip: {e}")
            with self._processed_lock:
                self._processed_photos.clear()
            self._strip_bg = None
            gc.collect()
            return None

    # ── Display-rotatie helpers ───────────────────────────────────
    #
    # Auto-detectie: als ALLE frames in het actieve template rotation == 90
    # (of allemaal 270) hebben, draaien we het EIND-stripje voor display/share
    # zodat de inhoud rechtop staat voor de kijker (mooi voor mobiele preview).
    # PRINT raakt deze code NIET — die blijft altijd self.strip_path gebruiken.

    def _template_display_rotation(self):
        """Returnt 0, 90 of 270. Geeft alleen 90/270 terug als ALLE frames
        van de huidige template dezelfde rotation hebben (90 of 270). Voor
        gemengde rotaties / 0 / 180: geeft 0 terug (geen display-rotatie)."""
        template = self.selected_template
        if not template or not template.frames:
            return 0
        rots = {int(getattr(f, 'rotation', 0) or 0) % 360 for f in template.frames}
        if rots == {90}:
            return 90
        if rots == {270}:
            return 270
        return 0

    def _maybe_create_rotated_display_strips(self, strip_path):
        """Maak gedraaide kopieën van strip + enkele strip wanneer nodig.

        Slaat de gedraaide JPG's op naast het origineel met een `_display`
        suffix, en zet self._display_strip_path / self._display_single_strip_path.
        Bij geen rotatie of fout: paden blijven None → fallback in
        display_strip_path-property gebruikt het origineel.
        """
        # Reset eerst (oude waarden van vorige sessie)
        self._display_strip_path = None
        self._display_single_strip_path = None

        target = self._template_display_rotation()
        if target == 0 or not strip_path:
            return

        # PIL: positive angle = counter-clockwise. Frames met rotation=90
        # plaatsen de foto 90° CW in het frame, dus om weer rechtop te
        # komen draaien we de hele strip 90° CCW = rotate(+90). Voor
        # rotation=270 (foto 90° CCW geplaatst): strip 90° CW = rotate(-90).
        pil_angle = 90 if target == 90 else -90
        try:
            from PIL import Image
            base, ext = os.path.splitext(strip_path)
            rotated_path = f"{base}_display{ext}"
            with Image.open(strip_path) as im:
                rotated = im.rotate(pil_angle, expand=True)
                rotated.save(rotated_path, "JPEG", quality=95)
            self._display_strip_path = rotated_path
            print(f"[STRIP] Display-rotatie {target}° toegepast: {rotated_path}")

            single = getattr(self, '_single_strip_path', None)
            if single and os.path.isfile(single):
                s_base, s_ext = os.path.splitext(single)
                rotated_single = f"{s_base}_display{s_ext}"
                with Image.open(single) as im:
                    rotated = im.rotate(pil_angle, expand=True)
                    rotated.save(rotated_single, "JPEG", quality=95)
                self._display_single_strip_path = rotated_single
                print(f"[STRIP] Enkele-strip display-rotatie: {rotated_single}")
        except Exception as e:
            print(f"[STRIP] Display-rotatie mislukt (fallback naar origineel): {e}")
            self._display_strip_path = None
            self._display_single_strip_path = None

    @property
    def display_strip_path(self):
        """Pad naar strip-image voor REVIEW + SHARE (gedraaid indien nodig).

        Print MAG dit NIET gebruiken — print moet self.strip_path direct
        gebruiken om de fysieke uitvoer ongewijzigd te laten.
        """
        path = getattr(self, '_display_strip_path', None)
        if path and os.path.exists(path):
            return path
        return self.strip_path

    @property
    def display_single_strip_path(self):
        """Pad naar enkele-strip voor share (gedraaid indien nodig)."""
        path = getattr(self, '_display_single_strip_path', None)
        if path and os.path.exists(path):
            return path
        return getattr(self, '_single_strip_path', None)

    def _go_review(self):
        """Show the unified sharing screen after photos are taken.

        Custom-flow branch: bij payment_method == "custom" gaat de gebruiker
        eerst naar het keuzescherm (gratis digitaal vs betaalde print) i.p.v.
        de standaard sharing-screen. Alle andere paden blijven 100% identiek.
        """
        # ── Custom flow integratie (single if/else, geen verdere code-wijzigingen) ──
        ev = self.active_event
        if ev and getattr(ev, 'payment_method', 'none') == "custom":
            self._stop_live_view()
            self.camera.stop_live_view()
            self._session_prints_used = 0
            self._show_custom_choice()
            return
        # ── Bestaande flow: bit-voor-bit identiek aan vóór deze wijziging ──
        self.state = State.REVIEW
        self._stop_live_view()
        self.camera.stop_live_view()
        self._session_prints_used = 0

        # Configure button visibility based on event settings
        ev = self.active_event
        print_on = ev.print_enabled if ev else True
        auto_print = ev.auto_print if ev else True
        qr_on = ev.gallery_enabled if ev else False
        email_on = ev.email_enabled if ev else False
        max_prints = ev.max_prints if ev else 1

        # Print button visibility:
        # - auto_print OFF: show if print enabled (uses max_prints)
        # - auto_print ON: show only if extra_prints_allowed > 0
        extra_prints = ev.extra_prints_allowed if ev else 0
        show_print_btn = print_on and (not auto_print or extra_prints > 0)
        self._sharing_print_btn.setVisible(show_print_btn)
        self._sharing_prints_remaining.setVisible(show_print_btn)
        self._sharing_print_status.hide()
        self._update_print_remaining()

        # QR button: only if gallery enabled AND plan allows it
        qr_visible = qr_on and self._is_pro_feature("qr_sharing")
        self._sharing_qr_btn.setVisible(qr_visible)

        # Email button: only if email enabled AND plan allows AND Gmail is configured
        from email_sender import load_gmail_config
        gmail_ok = load_gmail_config() is not None
        email_visible = email_on and self._is_pro_feature("email") and gmail_ok

        # Data collection mode handling
        ev = self.active_event
        dc_enabled = ev and getattr(ev, 'data_collect_enabled', False)
        dc_timing = getattr(ev, 'data_collect_timing', 'after_optional') if ev else ''

        # Disconnect email button to prevent duplicate connections
        try:
            self._sharing_email_btn.clicked.disconnect()
        except TypeError:
            pass

        if dc_enabled and dc_timing == 'after_optional' and gmail_ok:
            # Optional: show "ONTVANG JE FOTO" button, user chooses
            self._sharing_email_btn.setText("\U0001f4e7  " + t("btn_receive_photo"))
            self._sharing_email_btn.setVisible(True)
            self._sharing_email_btn.clicked.connect(
                lambda: self._show_data_collection("after"))
            email_visible = True
        elif dc_enabled and dc_timing == 'after_auto' and gmail_ok:
            # Automatic: show form immediately after review page loads
            self._sharing_email_btn.setVisible(False)
            QTimer.singleShot(500, lambda: self._show_data_collection("after"))
            email_visible = False
        else:
            self._sharing_email_btn.setText("\U0001f4e8  " + t("btn_email"))
            self._sharing_email_btn.setVisible(email_visible)
            self._sharing_email_btn.clicked.connect(self._go_email_input)

        # Auto-email after "before" data collection
        if (dc_enabled and dc_timing == 'before'
                and getattr(ev, 'data_collect_auto_email', True)
                and gmail_ok):
            collected = getattr(self, '_dc_collected_data', {})
            email_addr = collected.get('email', '')
            if email_addr:
                self._auto_send_email(email_addr)
                self._sharing_print_status.setText(t("photo_emailed", email=email_addr))
                self._sharing_print_status.setStyleSheet(
                    f"color: {config.COLOR_SUCCESS}; font-size: 14px;")
                self._sharing_print_status.show()
                # Hide email button since it's auto-sent
                self._sharing_email_btn.setVisible(False)
                self._dc_collected_data = {}  # Clear after sending

        # Grey out QR and Email buttons if no internet
        has_internet = getattr(self, '_has_internet', True)
        if not has_internet and (qr_visible or email_visible):
            disabled_style = (
                "QPushButton { background: rgba(255,255,255,0.05); color: #666666; "
                "border: none; border-radius: 16px; padding: 16px; font-size: 18px; }"
            )
            if qr_visible:
                self._sharing_qr_btn.setStyleSheet(disabled_style)
                self._sharing_qr_btn.setEnabled(False)
            if email_visible:
                self._sharing_email_btn.setStyleSheet(disabled_style)
                self._sharing_email_btn.setEnabled(False)
            self._no_wifi_label.show()
        else:
            # Restore normal styles
            if qr_visible:
                self._sharing_qr_btn.setStyleSheet(
                    f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: white; "
                    f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
                    f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
                    f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
                )
                self._sharing_qr_btn.setEnabled(True)
            if email_visible:
                self._sharing_email_btn.setStyleSheet(
                    f"QPushButton {{ background: rgba(255,255,255,0.12); color: white; "
                    f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
                    f"QPushButton:hover {{ background: rgba(255,255,255,0.18); }}"
                    f"QPushButton:pressed {{ background: rgba(255,255,255,0.25); }}"
                )
                self._sharing_email_btn.setEnabled(True)
            self._no_wifi_label.hide()

        # Hide QR overlay
        self._qr_overlay.hide()

        # Start in confirm-panel (page 0): "Zijn de foto's goed gelukt?"
        # Auto-print, QR-prep en countdown gebeuren PAS na de print-vraag
        # (zie _on_review_print_yes / _no). We bewaren de gewenste
        # auto-print en qr_on settings hier:
        self._review_pending_auto_print = bool(print_on and auto_print)
        self._review_pending_qr = bool(qr_on)
        if hasattr(self, '_review_panel_stack'):
            self._review_panel_stack.setCurrentIndex(0)

        # Adapt layout for current orientation
        self._review_is_portrait = None  # Force re-evaluation
        self._adapt_review_layout()

        self.stack.setCurrentIndex(self.pages["review"])
        QTimer.singleShot(200, self._display_review_strip)
        # NO second re-render: printer GDI can break fullscreen between 200-500ms

        # Start Google Drive upload in background (non-blocking)
        self._start_gdrive_upload()

    # ── Tussen-scherm handlers ──────────────────────────────────────────

    def _on_review_photos_ok(self):
        """User klikt 'Ja' op 'foto's goed gelukt?' → naar print-vraag panel.

        Printen uitgeschakeld → de print-vraag heeft geen zin; sla 'm
        over en ga direct naar het deel-paneel met de QR-code (zelfde
        pad als 'Nee, geen print').
        """
        if not self.effective_print_enabled:
            print("[REVIEW] Print-vraag overgeslagen — printen staat uit")
            self._on_review_print_no()
            return
        if hasattr(self, '_review_panel_stack'):
            self._review_panel_stack.setCurrentIndex(1)

    def _on_review_photos_redo(self):
        """User klikt 'Nee, begin opnieuw' → reset sessie + auto-start nieuwe.

        Annuleert pending timers, gooit foto's weg, en gaat via _go_direct_capture
        terug naar de standaard photobooth-flow (live view + auto-countdown,
        zonder losse 'foto maken' knop). Event session_count wordt NIET dubbel
        bijgewerkt omdat _go_direct_capture geen session-increment doet.
        """
        print("[REVIEW] User koos 'Nee, opnieuw' — sessie reset")
        # Stop pending timers
        try:
            if hasattr(self, 'review_timer'):
                self.review_timer.stop()
            if hasattr(self, '_sharing_countdown_timer'):
                self._sharing_countdown_timer.stop()
            if hasattr(self, 'done_timer'):
                self.done_timer.stop()
        except Exception:
            pass
        # Hide QR overlay if it's showing
        if hasattr(self, '_qr_overlay'):
            self._qr_overlay.hide()
        # Reset strip-state (de capture- en photo-state worden door
        # _go_direct_capture zelf gereset)
        self.strip_path = None
        self._single_strip_path = None
        self._display_strip_path = None
        self._display_single_strip_path = None
        self._boomerang_path = None
        self._session_prints_used = 0
        self._qr_ready = False
        # Direct-capture flow → live view start + auto-countdown
        # (zelfde flow als normale sessie-start, geen 'foto maken' knop)
        self._go_direct_capture()

    def _on_review_print_yes(self):
        """User klikt 'Ja, print' → switch naar action panel + start print."""
        if hasattr(self, '_review_panel_stack'):
            self._review_panel_stack.setCurrentIndex(2)
        # Start auto-print indien event auto-print aan heeft staan
        if getattr(self, '_review_pending_auto_print', False):
            self._sharing_print_status.setText(t("printing"))
            self._sharing_print_status.show()
            QTimer.singleShot(800, self._sharing_do_auto_print)
        else:
            # auto_print stond uit — manuele print-knop blijft beschikbaar
            self._sharing_print_status.hide()
        # QR-code voorbereiden + countdown starten zoals oude flow.
        # Inline QR-box of wifi-tip nu meteen op de juiste state zetten
        # zodat de gast direct ziet wat er beschikbaar is.
        self._update_inline_qr(None, "", ready=False)
        if getattr(self, '_review_pending_qr', False):
            QTimer.singleShot(200, self._prepare_qr_code)
        self._start_sharing_countdown()

    def _on_review_print_no(self):
        """User klikt 'Nee, geen print' → action panel zonder auto-print."""
        if hasattr(self, '_review_panel_stack'):
            self._review_panel_stack.setCurrentIndex(2)
        self._sharing_print_status.hide()
        # QR-code voorbereiden + countdown starten zoals oude flow.
        # Inline QR-box of wifi-tip nu meteen op de juiste state zetten
        # zodat de gast direct ziet wat er beschikbaar is.
        self._update_inline_qr(None, "", ready=False)
        if getattr(self, '_review_pending_qr', False):
            QTimer.singleShot(200, self._prepare_qr_code)
        self._start_sharing_countdown()

    def _display_review_strip(self):
        if not self.strip_path or not os.path.exists(self.strip_path):
            return
        # Show single strip on sharing screen if enabled. Gebruik de display-
        # paden zodat een geroteerde versie (template met alle frames 90/270)
        # rechtop op het reviewscherm verschijnt.
        ev = self.active_event
        display_path = self.display_strip_path
        if ev and ev.share_single_strip and self.display_single_strip_path:
            if os.path.exists(self.display_single_strip_path):
                display_path = self.display_single_strip_path
        # Cache the pixmap to avoid re-reading from disk on every resize
        if getattr(self, '_cached_strip_path', '') != display_path:
            self._cached_strip_pixmap = QPixmap(display_path)
            self._cached_strip_path = display_path
        pixmap = self._cached_strip_pixmap
        if pixmap.isNull():
            return
        # ALWAYS use screen geometry for scaling — never container/window width.
        # Windows DPI scaling can corrupt window geometry (912→1237px).
        # Screen geometry is always correct regardless of window state.
        screen = self.screen()
        if screen:
            sg = screen.geometry()
            sw, sh = sg.width(), sg.height()
        else:
            sw, sh = 912, 1368  # Safe fallback
        # Clamp all review widgets to screen width so they never exceed
        # the visible area even if the window geometry is corrupted
        self._review_wrapper.setMaximumWidth(sw)
        self._review_photo_container.setMaximumWidth(sw)
        self._review_action_panel.setMaximumWidth(sw)
        # Calculate available space for the strip preview.
        # Count visible buttons to determine how much space they need.
        # Scale strip to fit the photo container area.
        # Use the actual container size if available, otherwise calculate from screen.
        container_w = self._review_photo_container.width()
        container_h = self._review_photo_container.height()
        if container_w < 100 or container_h < 100:
            # Container not laid out yet — estimate from screen
            visible_btns = sum(1 for b in [self._sharing_print_btn, self._sharing_qr_btn,
                                            self._sharing_email_btn, self._sharing_done_btn]
                               if b.isVisible())
            btn_height = visible_btns * 56 + 80
            container_w = sw
            container_h = max(200, sh - btn_height)
        # Use 95% of container to leave some padding
        target = QSize(int(container_w * 0.95), int(container_h * 0.95))
        scaled = pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # Reset any fixed size from previous calls
        self.review_strip_label.setMinimumSize(0, 0)
        self.review_strip_label.setMaximumSize(16777215, 16777215)
        self.review_strip_label.setPixmap(scaled)

    def _update_print_remaining(self):
        """Update the prints remaining indicator."""
        ev = self.active_event
        auto_print = ev.auto_print if ev else True

        if auto_print:
            # Auto-print mode: extra_prints_allowed determines manual prints
            extra = ev.extra_prints_allowed if ev else 0
            # Auto-print already used some, subtract from extra allowance
            auto_copies = self.effective_print_copies
            manual_used = max(0, self._session_prints_used - auto_copies)
            remaining = max(0, extra - manual_used)
            if extra > 0:
                self._sharing_prints_remaining.setText(
                    f"Nog {remaining} extra print{'s' if remaining != 1 else ''}"
                )
            else:
                self._sharing_prints_remaining.setText("")
        else:
            # Manual mode: max_prints determines total
            max_prints = ev.max_prints if ev else 1
            remaining = max(0, max_prints - self._session_prints_used)
            if max_prints > 1:
                self._sharing_prints_remaining.setText(
                    f"Nog {remaining} van {max_prints} prints"
                )
            else:
                self._sharing_prints_remaining.setText("")
        self._sharing_print_btn.setEnabled(remaining > 0)

    def _sharing_do_print(self):
        """Show print dialog popup on sharing screen."""
        ev = self.active_event
        auto_print = ev.auto_print if ev else True

        if auto_print:
            extra = ev.extra_prints_allowed if ev else 0
            auto_copies = self.effective_print_copies
            manual_used = max(0, self._session_prints_used - auto_copies)
            remaining = max(0, extra - manual_used)
        else:
            max_prints = ev.max_prints if ev else 1
            remaining = max(0, max_prints - self._session_prints_used)

        if remaining <= 0:
            return

        # Bij precies 1 mogelijke print: skip de popup met disabled +/− knoppen
        # (gebruikers vonden dat verwarrend tijdens events). Direct printen
        # alsof gebruiker op "Print" geklikt zou hebben in de popup.
        if remaining == 1:
            self._sharing_print_btn.setEnabled(False)
            self._sharing_print_status.setText(t("printing"))
            self._sharing_print_status.show()
            self._do_print_job(copies=1)
            return

        # Pause sharing countdown while dialog is open
        self._sharing_countdown_timer.stop()

        # Build popup overlay
        overlay = QWidget(self._review_page)
        overlay.setStyleSheet("background: rgba(0,0,0,0.7);")
        overlay.setGeometry(self._review_page.rect())

        popup = QWidget(overlay)
        popup.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_BG}; border-radius: 24px; }}"
        )
        popup.setFixedSize(480, 320)
        popup_lay = QVBoxLayout(popup)
        popup_lay.setContentsMargins(40, 30, 40, 30)
        popup_lay.setSpacing(20)

        title = QLabel(t("how_many_prints"))
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Plus Jakarta Sans", 16, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        popup_lay.addWidget(title)

        # Copies selector row
        copies_row = QHBoxLayout()
        copies_row.setSpacing(24)
        copies_row.addStretch()

        self._print_popup_copies = 1

        minus_btn = QPushButton("−")
        minus_btn.setFixedSize(52, 52)
        minus_btn.setFont(QFont("DM Sans", 20, QFont.Bold))
        minus_btn.setCursor(Qt.PointingHandCursor)
        minus_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_CARD_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 26px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
        )
        copies_row.addWidget(minus_btn)

        copies_label = QLabel("1")
        copies_label.setAlignment(Qt.AlignCenter)
        copies_label.setFont(QFont("Plus Jakarta Sans", 28, QFont.Bold))
        copies_label.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        copies_label.setFixedWidth(60)
        copies_row.addWidget(copies_label)

        plus_btn = QPushButton("+")
        plus_btn.setFixedSize(52, 52)
        plus_btn.setFont(QFont("DM Sans", 20, QFont.Bold))
        plus_btn.setCursor(Qt.PointingHandCursor)
        plus_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_CARD_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 26px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
        )
        copies_row.addWidget(plus_btn)
        copies_row.addStretch()
        popup_lay.addLayout(copies_row)

        def _update_copies(delta):
            self._print_popup_copies = max(1, min(remaining, self._print_popup_copies + delta))
            copies_label.setText(str(self._print_popup_copies))
            minus_btn.setEnabled(self._print_popup_copies > 1)
            plus_btn.setEnabled(self._print_popup_copies < remaining)

        minus_btn.clicked.connect(lambda: _update_copies(-1))
        plus_btn.clicked.connect(lambda: _update_copies(+1))
        minus_btn.setEnabled(False)
        plus_btn.setEnabled(remaining > 1)

        popup_lay.addSpacing(4)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        cancel_btn = QPushButton(t("print_cancel"))
        cancel_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedHeight(50)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_CARD_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 14px; padding: 0 28px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
        )
        btn_row.addWidget(cancel_btn, 1)

        print_btn = QPushButton(t("print_confirm"))
        print_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        print_btn.setCursor(Qt.PointingHandCursor)
        print_btn.setFixedHeight(50)
        print_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 14px; padding: 0 28px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        btn_row.addWidget(print_btn, 1)
        popup_lay.addLayout(btn_row)

        def _close_popup():
            overlay.deleteLater()
            self._start_sharing_countdown()

        def _do_print():
            copies = self._print_popup_copies
            overlay.deleteLater()
            self._sharing_print_btn.setEnabled(False)
            self._sharing_print_status.setText(t("printing"))
            self._sharing_print_status.show()
            self._do_print_job(copies=copies)
            # Countdown NIET herstarten als de inline pakket-delay loopt of
            # er een print wacht op een printer-fix — anders eindigt de
            # sessie vóór de print verstuurd is en gaat die verloren.
            delay_active = (
                getattr(self, '_inline_print_delay_timer', None) is not None
                and self._inline_print_delay_timer.isActive()
            )
            pending = getattr(self, '_pending_print_copies', None) is not None
            if not delay_active and not pending:
                self._start_sharing_countdown()

        cancel_btn.clicked.connect(_close_popup)
        print_btn.clicked.connect(_do_print)

        # Center popup in overlay
        popup.move(
            (overlay.width() - popup.width()) // 2,
            (overlay.height() - popup.height()) // 2,
        )
        overlay.show()
        overlay.raise_()

    def _sharing_do_auto_print(self):
        """Auto-print (triggered automatically when auto_print is on)."""
        copies = self.effective_print_copies
        self._do_print_job(copies=copies)

    def _resolve_dnp_profile_key(self, template):
        """Bepaal welk DNP printer-profiel bij dit template hoort.

        Template flags zijn LEIDEND — printer_mode op het event wordt
        genegeerd zodat de operator zich geen zorgen hoeft te maken over
        de printer-modus instelling. Het gekozen template bepaalt alles.

        Mapping:
          - template.is_triple_strip → PROFILE_4X6_CUT  (3 strips, auto-cut)
          - template.is_4x3_strip    → PROFILE_4X3      (half-size paper)
          - anders                   → PROFILE_4X6_NOCUT (vol vel, geen cut)

        Args:
            template: Template-object (uit linked of preset library).

        Returns:
            profile_key string, of None bij ontbrekend template.
        """
        # Verhuurophalen: HiTi P525L gebruikt het legacy enkel-profiel
        # DEVMODE-blob ("Printer instellen" in Geavanceerd) — geen DNP-
        # profielen. profile_key None = legacy pad in printer.py.
        if self.backend_brand == 'huren':
            return None
        from printer import PROFILE_4X6_CUT, PROFILE_4X6_NOCUT, PROFILE_4X3
        if not template:
            return None
        if getattr(template, 'is_triple_strip', False):
            return PROFILE_4X6_CUT
        if getattr(template, 'is_4x3_strip', False):
            return PROFILE_4X3
        return PROFILE_4X6_NOCUT

    def _print_phase(self, ev) -> str:
        """Bepaal de print-fase t.o.v. de event-datum:
            'test' = vóór de event-datum → max config.TEST_PRINT_LIMIT prints
            'open' = op en NA de event-datum → onbeperkt (geen sluiting meer)
            'none' = geen/onparseerbare datum → geen limiet (altijd open)
        """
        raw = (getattr(ev, 'linked_event_date', '') or
               getattr(ev, 'date', '') or '')[:10]
        if not raw:
            return 'none'
        try:
            from datetime import date as _date
            parts = raw.split('-')
            event_day = _date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            return 'none'  # onparseerbare datum → geen limiet
        # Vóór de event-datum = testfase (max TEST_PRINT_LIMIT). Op en na de
        # event-datum mag er onbeperkt geprint worden — geen sluiting meer.
        if _date.today() < event_day:
            return 'test'
        return 'open'

    def _is_before_event_date(self, ev) -> bool:
        """Compat-helper: True als we in de test-fase zitten (vóór event-datum)."""
        return self._print_phase(ev) == 'test'

    def _test_print_allowed(self, ev, copies: int) -> bool:
        """Mag deze print door?
            'open'/'none' → ja (op/na de event-datum: onbeperkt)
            'test'        → alleen als test-teller + copies binnen de limiet blijft
        """
        phase = self._print_phase(ev)
        if phase in ('open', 'none'):
            return True
        limit = int(getattr(config, 'TEST_PRINT_LIMIT', 10))
        used = int(getattr(ev, 'test_prints_used', 0) or 0)
        return used + int(copies) <= limit

    def _compute_print_delay_sec(self, ev) -> int:
        """Print-vertraging in seconden vóór de daadwerkelijke print begint:
            premium pakket  → 5 sec
            standaard/overig → 30 sec
            0 sec bij Verhuurophalen (geen pakketten) OF wanneer de
            printer-storingsmeldingen uit staan (bv. tijdelijke niet-DNP
            printer zoals een Canon CP1500 → direct printen).
        """
        package = (getattr(ev, 'linked_package', '') or '').lower() if ev else ''
        delay_sec = {"premium": 5, "standard": 30}.get(package, 30)
        if getattr(self, 'backend_brand', '') == 'huren':
            return 0
        if not self._printer_status_enabled():
            return 0
        return delay_sec

    def _do_print_job(self, copies=1):
        """Execute a print job (shared between auto-print and manual print).

        Event-quotum handhaving: als event.event_print_quota > 0 wordt
        gecontroleerd of er nog ruimte is. Bij overschrijding: toon
        "Maximum prints bereikt" en print niet.

        Pre-print check: vraagt direct verse DNP-status; als blocking
        fout (klep open / papier op / lint op / etc.) wordt de print
        NIET verstuurd en verschijnt de fullscreen error-overlay zodat
        de operator de fout eerst kan verhelpen. Dit voorkomt half
        gestarte print-jobs die alsnog wegtellen op de teller.
        """
        if not self.strip_path:
            self._sharing_print_status.setText(t("no_strip"))
            return

        # Printen uitgeschakeld → nooit een job starten (vangnet — de
        # print-knoppen horen al verborgen te zijn, maar auto-print of
        # een gemiste code-route mag hier niet doorheen glippen).
        if not self.effective_print_enabled:
            print("[PRINTER] Print-job genegeerd — printen staat uit")
            return

        # Pre-print DNP status-check. We gebruiken de laatst gepolde status
        # (max ~2 sec oud — de poller draait elke 2s mét USB cross-check)
        # in plaats van een synchrone force_refresh: die blokkeerde de
        # GUI-thread 0,5-8 sec op precies het moment dat de gast op Print
        # drukt, en deelt COM-objecten met de poller-thread (apartment-
        # unsafe).
        st = None
        # Storingsmeldingen uit → geen pre-print statuscheck/overlay; laat de
        # print gewoon doorgaan (werkt zo met elke printer, bv. HiTi P310W).
        if not self._printer_status_enabled():
            st = None
        elif getattr(self, '_dnp_poller', None) is not None:
            try:
                st = self._dnp_poller.get()
            except Exception as e:
                print(f"[PRINT-PRECHECK] poller.get fout (niet kritiek): {e}")
        if st is None and self._printer_status_enabled():
            st = getattr(self, '_dnp_last_status', None)
        if st is not None and st.is_blocking():
            print(f"[PRINT-PRECHECK] Blokkerende fout — print uitgesteld "
                  f"(level={st.level.value}, code={st.code}, label={st.label!r}). "
                  f"Auto-retry zodra fout opgelost.")
            # Onthoud dat er een pending print is — _on_dnp_status_change_main
            # zal 'm automatisch sturen zodra de printer weer OK is.
            self._pending_print_copies = copies
            # Toon overlay (verschijnt of update bestaande)
            self._show_dnp_error_overlay(st)
            # Print-knop status text
            if hasattr(self, '_sharing_print_status'):
                self._sharing_print_status.setText(
                    "Printer-fout — print start automatisch na verhelpen"
                )
                self._sharing_print_status.show()
            return

        # Eventlimiet-check (per-event quotum, onafhankelijk van session-limits)
        ev = self.active_event
        if ev:
            quota = int(getattr(ev, 'event_print_quota', 0) or 0)
            used = int(getattr(ev, 'event_prints_used', 0) or 0)
            if quota > 0 and used + copies > quota:
                self._sharing_print_status.setText(t("event_limit_reached"))
                self._sharing_print_status.show()
                if hasattr(self, '_sharing_print_btn'):
                    self._sharing_print_btn.setEnabled(False)
                print(f"[PRINT-QUOTA] Blokkeer: used={used}, quota={quota}, gevraagd={copies}")
                return

        # Print-venster t.o.v. de event-datum:
        #   test → max config.TEST_PRINT_LIMIT test-prints vóór de event-datum
        #   open → op en na de event-datum: onbeperkt (geen sluiting meer)
        if ev and not self._test_print_allowed(ev, copies):
            limit = int(getattr(config, 'TEST_PRINT_LIMIT', 10))
            msg = (f"Testlimiet bereikt ({limit} prints). Printen kan "
                   f"weer vanaf de event-datum.")
            self._sharing_print_status.setText(msg)
            self._sharing_print_status.show()
            if hasattr(self, '_sharing_print_btn'):
                self._sharing_print_btn.setEnabled(False)
            print(f"[TEST-PRINT] Geblokkeerd (testlimiet {limit}) — "
                  f"event-datum {getattr(ev, 'linked_event_date', '')!r}")
            return

        # Pakket-afhankelijke print-delay (zie _compute_print_delay_sec).
        # Geen apart fullscreen overlay meer — alles inline op het sharing-
        # scherm. Tijdens de delay blijft de QR-code zichtbaar zodat de
        # gast die alvast kan scannen.
        package = (getattr(ev, 'linked_package', '') or '').lower() if ev else ''
        delay_sec = self._compute_print_delay_sec(ev)
        print(f"[PRINTER] Pakket={package or 'onbekend'}, print-delay={delay_sec}s, copies={copies}")
        self._start_inline_print_delay(copies=copies, delay_sec=delay_sec)

    def _play_printer_busy_sound(self, total_duration_sec: float):
        """Speel printer-busy geluid af voor `total_duration_sec` seconden.

        Gebruikt QMediaPlayer (Windows Media Foundation) om de MP3 in
        sounds/printer_busy.mp3 af te spelen. Stopt automatisch na de
        opgegeven duur via QTimer. Veilig om herhaald aan te roepen —
        eventuele vorige player wordt eerst gestopt.
        """
        try:
            from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
            from PyQt5.QtCore import QUrl
        except Exception as e:
            print(f"[AUDIO] QMediaPlayer niet beschikbaar: {e}")
            return
        # Stop eventuele oude player
        self._stop_printer_busy_sound()
        # Vind sound-file in bundle of source dir
        sound_path = os.path.join(config.BUNDLE_DIR, "sounds", "printer_busy.mp3")
        if not os.path.isfile(sound_path):
            sound_path = os.path.join(config.BASE_DIR, "sounds", "printer_busy.mp3")
        if not os.path.isfile(sound_path):
            print(f"[AUDIO] Sound-file niet gevonden: {sound_path}")
            return
        try:
            self._printer_busy_player = QMediaPlayer(self)
            self._printer_busy_player.setMedia(
                QMediaContent(QUrl.fromLocalFile(sound_path))
            )
            self._printer_busy_player.setVolume(70)
            self._printer_busy_player.play()
            # Stop-timer na de gevraagde duur
            self._printer_busy_stop_timer = QTimer(self)
            self._printer_busy_stop_timer.setSingleShot(True)
            self._printer_busy_stop_timer.timeout.connect(self._stop_printer_busy_sound)
            self._printer_busy_stop_timer.start(int(total_duration_sec * 1000))
            print(f"[AUDIO] Printer-busy geluid gestart ({total_duration_sec}s)")
        except Exception as e:
            print(f"[AUDIO] Afspelen mislukt: {e}")

    def _stop_printer_busy_sound(self):
        """Stop eventuele printer-busy audio + cancel z'n stop-timer."""
        player = getattr(self, '_printer_busy_player', None)
        if player is not None:
            try:
                player.stop()
                player.deleteLater()
            except Exception:
                pass
            self._printer_busy_player = None
        timer = getattr(self, '_printer_busy_stop_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._printer_busy_stop_timer = None

    def _start_inline_print_delay(self, copies: int, delay_sec: int):
        """Toon op de sharing-screen 'Foto wordt geprint' + cancel/redo
        knoppen ipv print-knop, en start de delay-timer.

        Speelt tegelijk een printer-busy geluid af (delay + 3s marge) zodat
        de echte printer naadloos het geluid overneemt — gast hoort
        continu een 'er gebeurt iets' indicator."""
        # Verberg print-knop + remaining-indicator
        if hasattr(self, '_sharing_print_btn'):
            self._sharing_print_btn.hide()
        if hasattr(self, '_sharing_prints_remaining'):
            self._sharing_prints_remaining.hide()
        # Status-tekst tonen
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText("🖨  Foto wordt geprint...")
            self._sharing_print_status.setStyleSheet(
                f"color: white; background: transparent; font-weight: bold;"
            )
            self._sharing_print_status.show()
        # Annuleer + Opnieuw knoppen tonen
        if hasattr(self, '_sharing_cancel_print_btn'):
            self._sharing_cancel_print_btn.show()
        if hasattr(self, '_sharing_redo_print_btn'):
            self._sharing_redo_print_btn.show()
        # Pauzeer ook de auto-print-na-timeout countdown — anders zou de
        # review_timer alsnog _sharing_do_print kunnen retriggeren.
        try:
            if hasattr(self, '_sharing_countdown_timer'):
                self._sharing_countdown_timer.stop()
            if hasattr(self, 'review_timer'):
                self.review_timer.stop()
        except Exception:
            pass
        # Bewaar copies + start delay-timer
        self._inline_print_copies = copies
        self._inline_print_delay_timer = QTimer(self)
        self._inline_print_delay_timer.setSingleShot(True)
        self._inline_print_delay_timer.timeout.connect(
            self._on_inline_print_delay_done
        )
        self._inline_print_delay_timer.start(int(delay_sec * 1000))
        # Printer-busy sound is uitgeschakeld (user-keuze 2026-06-06).
        # De _play_printer_busy_sound / _stop_printer_busy_sound methods
        # + sounds/printer_busy.mp3 blijven beschikbaar voor evt. later.
        print(f"[PRINTER] Inline delay gestart: {delay_sec}s (geen audio)")

    def _on_inline_print_delay_done(self):
        """Delay afgelopen — verberg cancel/redo knoppen + stuur de print."""
        copies = getattr(self, '_inline_print_copies', 1)
        self._cleanup_inline_print_widgets()
        # Status-tekst aanpassen tot daadwerkelijke print
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText("🖨  Print verzonden")
            self._sharing_print_status.show()
        self._actually_send_print(copies)

    def _on_inline_print_cancel(self):
        """User klikt 'Annuleer print' op sharing-screen tijdens de delay.

        Print wordt niet verzonden, print-optie verdwijnt voor deze sessie,
        maar QR-code blijft zichtbaar zodat de gast nog steeds kan downloaden."""
        print("[PRINTER] Inline print geannuleerd door gebruiker")
        self._stop_printer_busy_sound()
        self._cleanup_inline_print_widgets()
        # Print-knop blijft verborgen (gast koos niet voor printen)
        if hasattr(self, '_sharing_print_btn'):
            self._sharing_print_btn.hide()
        if hasattr(self, '_sharing_prints_remaining'):
            self._sharing_prints_remaining.hide()
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText("Print geannuleerd")
            self._sharing_print_status.show()
            QTimer.singleShot(3000, self._sharing_print_status.hide)
        # Hervat de auto-done timeout zodat gast niet vast komt te zitten
        try:
            if hasattr(self, '_start_sharing_countdown'):
                self._start_sharing_countdown()
        except Exception:
            pass

    def _on_inline_print_redo(self):
        """User klikt 'Foto's opnieuw maken' tijdens de delay — restart sessie."""
        print("[PRINTER] Inline print geannuleerd, opnieuw fotograferen")
        self._stop_printer_busy_sound()
        self._cleanup_inline_print_widgets()
        # Hergebruik bestaande 'opnieuw'-flow van review-scherm
        self._on_review_photos_redo()

    def _cleanup_inline_print_widgets(self):
        """Verberg cancel/redo knoppen + stop delay-timer (idempotent)."""
        if hasattr(self, '_inline_print_delay_timer') and self._inline_print_delay_timer is not None:
            try:
                self._inline_print_delay_timer.stop()
            except Exception:
                pass
            self._inline_print_delay_timer = None
        if hasattr(self, '_sharing_cancel_print_btn') and self._sharing_cancel_print_btn is not None:
            try: self._sharing_cancel_print_btn.hide()
            except Exception: pass
        if hasattr(self, '_sharing_redo_print_btn') and self._sharing_redo_print_btn is not None:
            try: self._sharing_redo_print_btn.hide()
            except Exception: pass

    def _actually_send_print(self, copies):
        """Stuur de print naar de driver (gebeurt na de pakket-delay)."""
        profile_key = self._resolve_dnp_profile_key(self.selected_template)
        # Pauzeer DNP status-poller tijdens print om USB-conflict te vermijden.
        # Resume gebeurt in _on_print_complete_with_quota + _on_print_failed.
        if getattr(self, '_dnp_poller', None) is not None:
            try:
                self._dnp_poller.pause(True)
                print("[DNP-STATUS] Poller gepauzeerd voor print")
            except Exception as e:
                print(f"[DNP-STATUS] Pause-fout (niet kritiek): {e}")
        self.print_thread = SubprocessPrintThread(
            self.strip_path, config.PRINTER_NAME, copies,
            profile_key=profile_key,
            skip_status_check=not self._printer_status_enabled())
        self.print_thread.print_complete.connect(
            lambda c=copies: self._on_print_complete_with_quota(c)
        )
        self.print_thread.print_failed.connect(self._on_print_failed)
        self.print_thread.print_status.connect(self._on_print_status)
        # Keep-alive: het subprocess kan tot 120s blokkeren. Als een
        # volgende sessie self.print_thread overschrijft terwijl de oude
        # nog draait, wordt de QThread ge-garbage-collect → harde crash
        # ("QThread: Destroyed while thread is still running").
        if not hasattr(self, '_print_threads_alive'):
            self._print_threads_alive = []
        self._print_threads_alive.append(self.print_thread)
        self.print_thread.finished.connect(
            lambda th=self.print_thread: self._print_threads_alive.remove(th)
            if th in self._print_threads_alive else None
        )
        self.print_thread.start()
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText(t("checking_printer"))
            self._sharing_print_status.show()
        print(f"[PRINTER] Printen: {copies} kopie(ën)")

    def _show_print_delay_overlay(self, copies, delay_sec):
        """Fullscreen 'Foto wordt geprint' spinner-overlay met countdown.

        Toont een spinning indicator + 'Annuleer' (terug naar idle) en
        'Maak foto's opnieuw' (restart sessie). Pas na `delay_sec`
        seconden wordt de eigenlijke print verzonden via
        _actually_send_print.

        Pakket-onderscheid:
          - premium: 5 sec (snelle service)
          - standard: 20 sec (langere wachttijd als upgrade-prikkel)
        """
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QProgressBar,
        )

        overlay = QWidget(self)
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setStyleSheet("background: rgba(15,15,18,0.97);")

        lay = QVBoxLayout(overlay)
        lay.setContentsMargins(60, 60, 60, 60)
        lay.setSpacing(28)
        lay.addStretch()

        # Spinner (indeterminate progress bar — visueel als ring/balk)
        spinner = QProgressBar()
        spinner.setRange(0, 0)
        spinner.setTextVisible(False)
        spinner.setFixedHeight(8)
        spinner.setMaximumWidth(480)
        spinner.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.12); border: none; "
            "border-radius: 4px; }"
            "QProgressBar::chunk { background: white; border-radius: 4px; }"
        )
        lay.addWidget(spinner, alignment=Qt.AlignCenter)

        title = QLabel("Foto wordt geprint")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("DM Sans", 38, QFont.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        lay.addWidget(title)

        subtitle = QLabel("Even geduld — we bereiden je print voor")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("DM Sans", 17))
        subtitle.setStyleSheet("color: rgba(255,255,255,0.65); background: transparent;")
        subtitle.setWordWrap(True)
        lay.addWidget(subtitle)

        lay.addStretch()

        # Knoppen-rij onderaan: [Opnieuw fotograferen — stretch — Annuleer]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        redo = QPushButton("📸  Maak foto's opnieuw")
        redo.setFixedHeight(58)
        redo.setFont(QFont("DM Sans", 15, QFont.Bold))
        redo.setCursor(Qt.PointingHandCursor)
        redo.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.16); color: white; "
            "border: 1px solid rgba(255,255,255,0.3); border-radius: 14px; "
            "padding: 10px 28px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.26); }"
        )
        redo.clicked.connect(self._on_print_delay_redo)
        btn_row.addWidget(redo)

        btn_row.addStretch()

        cancel = QPushButton("✕  Annuleer")
        cancel.setFixedHeight(58)
        cancel.setFont(QFont("DM Sans", 15, QFont.Bold))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            "QPushButton { background: #C0392B; color: white; border: none; "
            "border-radius: 14px; padding: 10px 32px; }"
            "QPushButton:hover { background: #A93223; }"
        )
        cancel.clicked.connect(self._on_print_delay_cancel)
        btn_row.addWidget(cancel)
        lay.addLayout(btn_row)

        overlay.show()
        overlay.raise_()
        self._print_delay_overlay = overlay

        # Timer voor de delay
        self._print_delay_timer = QTimer(self)
        self._print_delay_timer.setSingleShot(True)
        self._print_delay_timer.timeout.connect(
            lambda c=copies: self._on_print_delay_done(c)
        )
        self._print_delay_timer.start(int(delay_sec * 1000))

    def _on_print_delay_done(self, copies):
        """Delay afgelopen — verberg overlay en stuur de echte print."""
        self._hide_print_delay_overlay()
        self._actually_send_print(copies)

    def _on_print_delay_cancel(self):
        """User klikt Annuleer — abort print, terug naar idle."""
        print("[PRINTER] Print geannuleerd door gebruiker tijdens delay")
        if hasattr(self, '_print_delay_timer') and self._print_delay_timer is not None:
            try: self._print_delay_timer.stop()
            except Exception: pass
        self._hide_print_delay_overlay()
        # Sessie afronden — terug naar idle/welcome
        self._go_idle()

    def _on_print_delay_redo(self):
        """User klikt 'Maak foto's opnieuw' — abort print, restart sessie."""
        print("[PRINTER] Print geannuleerd, opnieuw fotograferen")
        if hasattr(self, '_print_delay_timer') and self._print_delay_timer is not None:
            try: self._print_delay_timer.stop()
            except Exception: pass
        self._hide_print_delay_overlay()
        # Hergebruik de bestaande "opnieuw"-flow van review-scherm
        self._on_review_photos_redo()

    def _hide_print_delay_overlay(self):
        """Sluit het spinner-overlay netjes."""
        if hasattr(self, '_print_delay_overlay') and self._print_delay_overlay is not None:
            try:
                self._print_delay_overlay.hide()
                self._print_delay_overlay.deleteLater()
            except Exception:
                pass
            self._print_delay_overlay = None
        if hasattr(self, '_print_delay_timer') and self._print_delay_timer is not None:
            try: self._print_delay_timer.stop()
            except Exception: pass
            self._print_delay_timer = None

    def _on_print_complete_with_quota(self, copies):
        """Wrapper rond _on_print_complete die ook het event-quotum bijwerkt."""
        # Resume DNP status-poller — print is klaar, USB weer vrij
        if getattr(self, '_dnp_poller', None) is not None:
            try:
                self._dnp_poller.pause(False)
            except Exception:
                pass
        ev = self.active_event
        if ev:
            _ev_dirty = False
            quota = int(getattr(ev, 'event_print_quota', 0) or 0)
            if quota > 0:
                ev.event_prints_used = int(getattr(ev, 'event_prints_used', 0) or 0) + int(copies)
                _ev_dirty = True
                print(f"[PRINT-QUOTA] Used: {ev.event_prints_used}/{quota}")
            # Test-print-teller alleen ophogen zolang we vóór de event-datum
            # zijn (op/na de event-datum geldt de limiet niet).
            if self._is_before_event_date(ev):
                ev.test_prints_used = int(getattr(ev, 'test_prints_used', 0) or 0) + int(copies)
                _ev_dirty = True
                print(f"[TEST-PRINT] Used: {ev.test_prints_used}/"
                      f"{getattr(config, 'TEST_PRINT_LIMIT', 10)} (vóór event-datum)")
            if _ev_dirty:
                try:
                    ev.save(config.EVENTS_DIR)
                except Exception as ex:
                    print(f"[PRINT-QUOTA] Save fout: {ex}")
                # UI bijwerken als de Print-tab open staat
                if hasattr(self, '_evlimit_status_label'):
                    try:
                        self._refresh_event_limit_ui()
                    except Exception:
                        pass
        # Roep originele handler aan
        self._on_print_complete(copies)

    def _start_printing(self):
        """Legacy method — redirects to sharing screen print."""
        self._sharing_do_print()

    def _go_after_review(self):
        """Legacy method — go to done."""
        self.review_timer.stop()
        self._go_done()

    def _on_print_complete(self, copies=1):
        """Print finished — update sharing screen status.

        copies telt per kopie mee in de sessie-limiet: een job van 3
        kopieën verbruikt 3 prints, niet 1 (anders kan een gast met
        max_prints=3 in totaal 6 fysieke prints krijgen).
        """
        self._session_prints_used += max(1, int(copies))
        ev = self.active_event
        auto_print = ev.auto_print if ev else True

        if auto_print:
            self._sharing_print_status.setText("✓ " + t("printed"))
            QTimer.singleShot(3000, lambda: self._sharing_print_status.hide()
                              if self.state == State.REVIEW else None)
        else:
            self._sharing_print_status.setText("✓ " + t("printed"))
            self._update_print_remaining()
            QTimer.singleShot(2000, lambda: self._sharing_print_status.hide()
                              if self.state == State.REVIEW else None)

        print(f"[PRINTER] Print voltooid ({self._session_prints_used} totaal deze sessie)")

    def _prepare_qr_code(self):
        """Prepare QR code data in background so it's ready when user taps QR button."""
        try:
            from qr_generator import generate_session_url, generate_qr_pixmap
            from web_server import register_session

            session_id = self.session_id or self._new_session_id()
            template_name = self.selected_template.name if self.selected_template else ""

            register_session(
                session_id, self.display_strip_path, self.photos, template_name,
                boomerang_path=getattr(self, '_boomerang_path', None),
            )

            cloud_url = getattr(self, '_cloud_url', '')
            if cloud_url:
                # Cloud URL ready — show real QR
                url = cloud_url
                qr_pixmap = generate_qr_pixmap(url, size=360)
                self.qr_label.setPixmap(qr_pixmap)
                self.qr_url_label.setText(url)
                self.qr_label.show()
                self.qr_url_label.show()
                self._stop_qr_spinner()
                self._qr_ready = True
                # Inline-QR ook updaten (nieuwe sharing-screen layout)
                self._update_inline_qr(qr_pixmap, url, ready=True)
                print(f"[QR] Voorbereid met cloud URL: {url}")
            else:
                # Cloud upload still in progress — show animated spinner
                self.qr_label.hide()
                self.qr_url_label.hide()
                self._start_qr_spinner()
                self._qr_ready = False
                self._update_inline_qr(None, "", ready=False)
                print("[QR] Cloud upload nog bezig, spinner getoond")
                # Register local session anyway (for fallback)
                generate_session_url(session_id, config.WEB_SERVER_PORT)
                # Vangnet-poll: werk de inline QR bij zodra de cloud-URL
                # binnenkomt. Voorheen startte deze poll alleen via de
                # (verwijderde) QR-knop — daardoor kon de spinner eeuwig
                # blijven draaien als de upload trager was dan de gast.
                if not hasattr(self, '_qr_poll_timer') or self._qr_poll_timer is None \
                        or not self._qr_poll_timer.isActive():
                    self._qr_poll_timer = QTimer()
                    self._qr_poll_timer.timeout.connect(self._poll_qr_cloud_url)
                    self._qr_poll_timer.start(500)
        except Exception as e:
            print(f"[QR] Fout bij voorbereiden: {e}")
            self._qr_ready = False

    def _update_inline_qr(self, pixmap, url, ready: bool):
        """Werk de INLINE QR-display bij. Toont of de pixmap of de
        'uploading...' fallback. Beslist ook QR-box vs no-wifi-tip op
        basis van wifi/gallery_enabled.

        - ready=True + wifi → QR-box zichtbaar, tip verborgen
        - ready=False + wifi → spinner-fallback in QR-box
        - geen wifi of gallery uit → tip-box zichtbaar, QR verborgen
        """
        if not hasattr(self, '_inline_qr_box') or self._inline_qr_box is None:
            return
        ev = self.active_event
        wifi_ok = bool(getattr(self, '_has_internet', True))
        gallery_ok = bool(getattr(ev, 'gallery_enabled', True)) if ev else True
        # Bij gallery_enabled=False is QR niet beschikbaar — verberg allebei
        if not gallery_ok:
            try:
                self._inline_qr_box.setVisible(False)
                self._inline_no_wifi_tip.setVisible(False)
            except Exception:
                pass
            return
        # Wifi-conditie bepaalt QR-box vs tip-box
        show_qr_box = wifi_ok
        try:
            self._inline_qr_box.setVisible(show_qr_box)
            self._inline_no_wifi_tip.setVisible(not show_qr_box)
        except Exception:
            pass
        if not show_qr_box:
            return
        if ready and pixmap is not None:
            try:
                self._inline_qr_label.setPixmap(pixmap)
                self._inline_qr_label.show()
                self._inline_qr_loading.hide()
                self._inline_qr_prompt.show()
            except Exception:
                pass
        else:
            try:
                self._inline_qr_label.clear()
                self._inline_qr_label.hide()
                self._inline_qr_loading.show()
                self._inline_qr_prompt.hide()
            except Exception:
                pass

    def _sharing_show_qr(self):
        """Show QR code overlay on sharing screen."""
        # Check if cloud URL arrived
        cloud_url = getattr(self, '_cloud_url', '')
        if cloud_url:
            # Cloud URL available — show real QR
            if self.qr_url_label.text() != cloud_url or not self._qr_ready:
                try:
                    from qr_generator import generate_qr_pixmap
                    qr_pixmap = generate_qr_pixmap(cloud_url, size=360)
                    self.qr_label.setPixmap(qr_pixmap)
                    self.qr_url_label.setText(cloud_url)
                except Exception:
                    pass
            self.qr_label.show()
            self.qr_url_label.show()
            self._stop_qr_spinner()
            self._qr_ready = True
        else:
            # Still uploading — show animated spinner, start polling
            self.qr_label.hide()
            self.qr_url_label.hide()
            self._start_qr_spinner()
            # Poll every 500ms for cloud URL
            if not hasattr(self, '_qr_poll_timer') or not self._qr_poll_timer.isActive():
                self._qr_poll_timer = QTimer()
                self._qr_poll_timer.timeout.connect(self._poll_qr_cloud_url)
                self._qr_poll_timer.start(500)

        # Position and show the QR overlay centered on the left area
        self._position_qr_overlay()
        self._qr_overlay.show()
        self._qr_overlay.raise_()

    def _poll_qr_cloud_url(self):
        """Poll for cloud URL and update QR when ready."""
        cloud_url = getattr(self, '_cloud_url', '')
        if cloud_url:
            self._qr_poll_timer.stop()
            try:
                from qr_generator import generate_qr_pixmap
                qr_pixmap = generate_qr_pixmap(cloud_url, size=360)
                self.qr_label.setPixmap(qr_pixmap)
                self.qr_url_label.setText(cloud_url)
                self.qr_label.show()
                self.qr_url_label.show()
                self._stop_qr_spinner()
                self._qr_ready = True
                # Inline QR óók updaten
                self._update_inline_qr(qr_pixmap, cloud_url, ready=True)
                print("[QR] Cloud URL ontvangen, QR code getoond")
            except Exception as e:
                print(f"[QR] Fout bij updaten QR: {e}")
        elif self.state != State.REVIEW:
            # User left sharing screen, stop polling
            self._qr_poll_timer.stop()
            self._stop_qr_spinner()

    def _animate_qr_spinner(self):
        """Animate the QR loading label dots."""
        self._qr_spinner_dot_count = (self._qr_spinner_dot_count + 1) % 4
        dots = "." * (self._qr_spinner_dot_count or 1)
        self._qr_loading_label.setText(f"\u23f3  {t('uploading')}{dots}")

    def _start_qr_spinner(self):
        """Start the QR loading dot animation."""
        self._qr_spinner_dot_count = 0
        self._qr_loading_label.setText("\u23f3  " + t("uploading") + "...")
        self._qr_loading_label.show()
        self._qr_spinner_timer.start()

    def _stop_qr_spinner(self):
        """Stop the QR loading dot animation."""
        self._qr_spinner_timer.stop()
        self._qr_loading_label.hide()

    def _adapt_review_layout(self):
        """Adjust review page styling for current orientation.

        No widget reparenting — layout is fixed VBoxLayout built once.
        Only adjusts margins, font sizes and button heights."""
        portrait = self._is_portrait()

        # Stack die de 3 panelen bevat (confirm / print-vraag / action).
        # Krijgt zelfde size-constraints als het oude action panel kreeg.
        _stack = getattr(self, '_review_panel_stack', None)

        if portrait:
            # Clamp all widgets to screen width — window geometry can be
            # corrupted by Windows DPI scaling (912→1237px).
            screen = self.screen()
            max_w = screen.geometry().width() if screen else 912
            self._review_action_panel.setMinimumSize(0, 0)
            self._review_action_panel.setMaximumWidth(max_w)
            if _stack:
                _stack.setMinimumSize(0, 0)
                _stack.setMaximumWidth(max_w)
            self._review_photo_container.setMinimumSize(0, 0)
            self._review_photo_container.setMaximumWidth(max_w)
            self._review_wrapper.setMaximumWidth(max_w)
            self.review_strip_label.setMaximumWidth(max_w)
            # Margins to keep buttons narrower and centered
            side_margin = max(20, (max_w - 480) // 2) if max_w > 520 else 20
            self._review_photo_container.layout().setContentsMargins(0, 0, 0, 0)
            self._review_action_panel.layout().setContentsMargins(side_margin, 8, side_margin, 12)
            self._review_action_panel.layout().setSpacing(8)
            # Confirm + print-vraag panels: ook side-margins
            for p in (getattr(self, '_review_confirm_panel', None),
                      getattr(self, '_review_print_question_panel', None)):
                if p and p.layout():
                    p.layout().setContentsMargins(side_margin, 8, side_margin, 12)
                    p.layout().setSpacing(10)
            # Ensure vertical layout for portrait
            wrap_lay = self._review_wrapper.layout()
            if wrap_lay:
                wrap_lay.setDirection(wrap_lay.TopToBottom)
                wrap_lay.setStretch(0, 3)  # photo container
                wrap_lay.setStretch(1, 0)  # panel stack
            self._review_action_panel.setMaximumWidth(16777215)  # reset landscape constraint
            if _stack:
                _stack.setMaximumWidth(16777215)
            for btn in [self._sharing_print_btn, self._sharing_qr_btn,
                        self._sharing_email_btn, self._sharing_done_btn,
                        getattr(self, '_review_confirm_yes_btn', None),
                        getattr(self, '_review_confirm_no_btn', None),
                        getattr(self, '_review_print_yes_btn', None),
                        getattr(self, '_review_print_no_btn', None)]:
                if btn is None:
                    continue
                btn.setMinimumHeight(48)
                btn.setMaximumHeight(56)
                btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        else:
            # Landscape — buttons on the RIGHT side, photo on LEFT
            screen = self.screen()
            max_w = screen.geometry().width() if screen else 1920
            max_h = screen.geometry().height() if screen else 1080
            self._review_photo_container.setMinimumSize(0, 0)
            self._review_photo_container.setMaximumSize(16777215, 16777215)
            self._review_action_panel.setMinimumSize(0, 0)
            self._review_action_panel.setMaximumWidth(int(max_w * 0.35))
            self._review_action_panel.setMaximumHeight(16777215)
            if _stack:
                _stack.setMinimumSize(0, 0)
                _stack.setMaximumWidth(int(max_w * 0.35))
                _stack.setMaximumHeight(16777215)
            self._review_photo_container.layout().setContentsMargins(10, 10, 0, 10)
            self._review_action_panel.layout().setContentsMargins(16, 12, 16, 12)
            self._review_action_panel.layout().setSpacing(8)
            for p in (getattr(self, '_review_confirm_panel', None),
                      getattr(self, '_review_print_question_panel', None)):
                if p and p.layout():
                    p.layout().setContentsMargins(16, 12, 16, 12)
                    p.layout().setSpacing(10)
            # Change wrapper to horizontal layout
            wrap_lay = self._review_wrapper.layout()
            if wrap_lay:
                wrap_lay.setDirection(wrap_lay.LeftToRight)
                wrap_lay.setStretch(0, 3)  # photo container (left)
                wrap_lay.setStretch(1, 1)  # panel stack (right)
            for btn in [self._sharing_print_btn, self._sharing_qr_btn,
                        self._sharing_email_btn, self._sharing_done_btn,
                        getattr(self, '_review_confirm_yes_btn', None),
                        getattr(self, '_review_confirm_no_btn', None),
                        getattr(self, '_review_print_yes_btn', None),
                        getattr(self, '_review_print_no_btn', None)]:
                if btn is None:
                    continue
                btn.setMinimumHeight(48)
                btn.setMaximumHeight(60)
                btn.setFont(QFont("DM Sans", 14, QFont.Bold))

        # Ensure overlays stay on top
        self._sharing_countdown_bar.raise_()
        self._qr_overlay.raise_()

        print(f"[UI] Review layout adjusted: {'portrait' if portrait else 'landscape'}")
        # Re-scale strip after margins settle
        QTimer.singleShot(50, self._display_review_strip)

    def _position_qr_overlay(self):
        """Position the QR overlay centered, adapts to orientation."""
        page = self.stack.widget(self.pages["review"])
        pw = page.width()
        ph = page.height()

        if self._is_portrait():
            # Portrait: center on full width
            ow = min(460, pw - 30)
            oh = min(580, ph - 30)
            x = (pw - ow) // 2
            y = (ph - oh) // 2
        else:
            # Landscape: center in photo area (exclude right panel)
            photo_w = pw - 360
            ow, oh = min(480, photo_w - 30), min(580, ph - 30)
            x = (photo_w - ow) // 2
            y = (ph - oh) // 2

        self._qr_overlay.setGeometry(max(0, x), max(0, y), ow, oh)

    def _show_qr_code(self):
        """Legacy method — now shows QR overlay on sharing screen."""
        if self.state != State.REVIEW:
            self._go_review()
        self._sharing_show_qr()

    def _start_cloud_upload(self):
        """Upload photo strip to Cloudflare R2 in background."""
        self._cloud_url = ''  # Reset
        if not self._is_logged_in():
            print("[CLOUD] Overgeslagen — niet ingelogd")
            return
        if not getattr(config, 'CLOUD_UPLOAD_ENABLED', False):
            return
        if not self.strip_path:
            return
        try:
            from cloud_storage import CloudUploadThread

            # Use single strip for sharing if enabled. Display-paden geven
            # gedraaide versie wanneer template alle frames 90/270 heeft.
            share_strip = self.display_single_strip_path or self.display_strip_path
            # Don't include boomerang here — it's uploaded separately after creation
            ev = self.active_event
            compress = ev.compress_sharing if ev else False
            # QR-branding tekst meesturen als toggle aan staat + niet leeg.
            # Worker leest dit uit customMetadata.branding_text en toont 't
            # in de footer van de gallery-pagina (i.p.v. "Powered by Bootharoo").
            branding_text = ""
            qr_brand_on = bool(ev and getattr(ev, 'qr_branding_enabled', False))
            if qr_brand_on:
                branding_text = (getattr(ev, 'qr_branding_text', '') or '').strip()
            # Diagnose-log: helpt bij troubleshooten waarom branding niet zichtbaar
            # is in de gallery. Toggle uit / tekst leeg / wel meegestuurd?
            if not qr_brand_on:
                print("[CLOUD] QR-branding toggle staat UIT — geen bedrijfsgegevens meegestuurd")
            elif not branding_text:
                print("[CLOUD] QR-branding toggle staat AAN maar tekst is leeg — geen branding")
            else:
                preview = branding_text.replace('\n', ' | ')[:80]
                print(f"[CLOUD] QR-branding meegestuurd ({len(branding_text)} chars): {preview}")
            self._cloud_upload_thread = CloudUploadThread(
                share_strip,
                photo_paths=self.photos,
                boomerang_path=None,  # Boomerang uploaded via _on_boomerang_complete
                session_id=self.session_id,
                compress=compress,
                branding_text=branding_text,
            )
            self._cloud_upload_thread.upload_complete.connect(
                self._on_cloud_upload_complete
            )
            self._cloud_upload_thread.start()
            print("[CLOUD] Upload gestart in achtergrond")
        except ImportError as ie:
            print(f"[CLOUD] Import fout: {ie}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"[CLOUD] Fout bij starten upload: {e}")

    def _on_cloud_upload_complete(self, url):
        """Called when cloud upload finishes."""
        if url:
            self._cloud_url = url
            print(f"[CLOUD] Upload voltooid: {url}")

            # Update QR code if sharing screen is showing
            if self.state == State.REVIEW:
                try:
                    from qr_generator import generate_qr_pixmap
                    qr_pixmap = generate_qr_pixmap(url, size=360)
                    self.qr_label.setPixmap(qr_pixmap)
                    self.qr_url_label.setText(url)
                    self.qr_label.show()
                    self.qr_url_label.show()
                    self._stop_qr_spinner()
                    self._qr_ready = True
                    # INLINE QR ook updaten — zonder deze call bleef de
                    # spinner op het deelscherm eeuwig draaien wanneer de
                    # upload pas klaar was NA _prepare_qr_code (race die
                    # vrijwel altijd verloren wordt sinds de print-vraag
                    # wordt overgeslagen bij printen-uit).
                    self._update_inline_qr(qr_pixmap, url, ready=True)
                    print("[CLOUD] QR code bijgewerkt naar cloud URL")
                except Exception as e:
                    print(f"[CLOUD] Kon QR niet bijwerken: {e}")
        else:
            # Upload mislukt → stop poll-timer + spinner, val terug op lokale URL
            # (web_server is al geregistreerd in _prepare_qr_code), zodat de QR
            # niet eeuwig blijft hangen op "uploaden...".
            print("[CLOUD] Upload mislukt, fallback naar lokale QR")
            try:
                if hasattr(self, '_qr_poll_timer') and self._qr_poll_timer.isActive():
                    self._qr_poll_timer.stop()
            except Exception:
                pass
            try:
                from qr_generator import generate_session_url, generate_qr_pixmap
                if self.session_id:
                    local_url = generate_session_url(
                        self.session_id, config.WEB_SERVER_PORT
                    )
                    if local_url and self.state == State.REVIEW:
                        qr_pixmap = generate_qr_pixmap(local_url, size=360)
                        self.qr_label.setPixmap(qr_pixmap)
                        self.qr_url_label.setText(local_url)
                        self.qr_label.show()
                        self.qr_url_label.show()
                        self._stop_qr_spinner()
                        self._qr_ready = True
                        # Inline QR ook naar de lokale fallback-URL
                        self._update_inline_qr(qr_pixmap, local_url, ready=True)
                        print(f"[CLOUD] Fallback QR (lokaal) getoond: {local_url}")
            except Exception as e:
                print(f"[CLOUD] Fallback QR mislukt: {e}")
                try:
                    self._stop_qr_spinner()
                except Exception:
                    pass

    def _start_gdrive_upload(self):
        """Upload photos to Google Drive in the background (if enabled)."""
        if not getattr(config, "GDRIVE_ENABLED", False):
            return
        try:
            from gdrive_uploader import GDriveUploader, GDriveUploadThread

            if not self._gdrive_uploader:
                self._gdrive_uploader = GDriveUploader()

            session_id = self.session_id or "unknown"
            self._gdrive_thread = GDriveUploadThread(
                self._gdrive_uploader,
                session_id,
                self.display_strip_path,
                self.photos,
                config.GDRIVE_FOLDER_NAME,
            )
            self._gdrive_thread.upload_complete.connect(self._on_gdrive_complete)
            self._gdrive_thread.start()
            print("[GDRIVE] Upload gestart in achtergrond")
        except ImportError:
            print("[GDRIVE] pydrive2 niet geinstalleerd")
        except Exception as e:
            print(f"[GDRIVE] Fout bij starten upload: {e}")

    def _on_gdrive_complete(self, success):
        if success:
            print("[GDRIVE] Upload voltooid")
        else:
            print("[GDRIVE] Upload (deels) mislukt")

    def _start_sharing_countdown(self):
        """Start the countdown bar on the sharing screen (uses event timeout)."""
        sharing_secs = max(5, self.active_event.sharing_timeout if self.active_event else 30)
        self._sharing_countdown_total_ms = sharing_secs * 1000
        self._sharing_countdown_elapsed_ms = 0
        self._sharing_countdown_bar.setValue(100)
        self._position_countdown_bar()
        self._sharing_countdown_timer.start()

    def _on_sharing_countdown_tick(self):
        """Update the sharing countdown progress bar."""
        self._sharing_countdown_elapsed_ms += 100
        progress = max(0, 100 - int(
            self._sharing_countdown_elapsed_ms * 100 / self._sharing_countdown_total_ms
        ))
        self._sharing_countdown_bar.setValue(progress)
        if self._sharing_countdown_elapsed_ms >= self._sharing_countdown_total_ms:
            self._sharing_countdown_timer.stop()
            self._go_done()

    def _go_done(self):
        """Skip thank-you screen, go directly to idle."""
        self.review_timer.stop()
        self._sharing_countdown_timer.stop()
        if hasattr(self, '_qr_overlay'):
            self._qr_overlay.hide()
        self._go_idle()

    def _on_print_status(self, status_msg):
        """Show print status update on sharing screen."""
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText(status_msg)
            self._sharing_print_status.show()

    def _on_print_failed(self, error_msg):
        """Handle print failure — echte fout tonen.

        Sinds v1.99.95 emit SubprocessPrintThread dit signaal alleen bij
        ÉCHTE fouten (exitcode != 0, timeout, exception). Geen quotum
        afboeken, geen vals 'Geprint!' meer — de gast en operator moeten
        weten dat er niks uit de printer komt.
        """
        # Resume DNP status-poller — print is afgerond (succes of falen)
        if getattr(self, '_dnp_poller', None) is not None:
            try:
                self._dnp_poller.pause(False)
            except Exception:
                pass
        print(f"[PRINTER] PRINT MISLUKT: {error_msg}")
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText(
                "⚠ Print mislukt — vraag de beheerder om hulp"
            )
            self._sharing_print_status.setStyleSheet(
                f"color: {config.COLOR_DANGER}; font-size: 14px;"
            )
            self._sharing_print_status.show()
        # Force een verse status-poll zodat de error-overlay verschijnt
        # als de printer een echt probleem heeft (paper out, offline, ...)
        if getattr(self, '_dnp_poller', None) is not None:
            try:
                threading.Thread(
                    target=self._dnp_poller.force_refresh, daemon=True
                ).start()
            except Exception:
                pass

    def _show_error(self, message):
        self.state = State.ERROR
        self._stop_live_view()
        self.error_message.setText(message)
        self.stack.setCurrentIndex(self.pages["error"])
        # Position lock button in bottom-right corner
        if hasattr(self, '_error_lock_btn'):
            page = self.stack.widget(self.pages["error"])
            pw, ph = page.width(), page.height()
            self._error_lock_btn.move(pw - 70, ph - 70)
        # Start auto-recovery timer — try to reconnect every 5s
        if not hasattr(self, '_recovery_timer'):
            self._recovery_timer = QTimer(self)
            self._recovery_timer.timeout.connect(self._try_error_recovery)
        self._recovery_timer.start(5000)

    def _try_error_recovery(self):
        """Periodically try to reconnect camera while error screen is shown (non-blocking)."""
        if self.state != State.ERROR:
            self._recovery_timer.stop()
            return
        # Check if already reconnected
        if self.camera.is_connected():
            print("[UI] Auto-recovery gelukt! Terug naar idle.")
            self._recovery_timer.stop()
            self._auto_reconnect_attempts = 0
            self._go_idle()
            return
        # Send reconnect command (non-blocking, result checked next timer tick)
        if not getattr(self, '_recovery_in_progress', False):
            self._recovery_in_progress = True
            print("[UI] Auto-recovery: reconnect poging...")
            worker = getattr(self.camera, '_worker', None)
            if worker and hasattr(worker, 'send_command'):
                worker.send_command("disconnect")
            QTimer.singleShot(1000, self._recovery_do_connect)

    def _recovery_do_connect(self):
        """Second step of recovery: send connect command."""
        self._recovery_in_progress = False
        if self.state != State.ERROR:
            return
        if hasattr(self.camera, '_connect_event'):
            self.camera._connect_event.clear()
        worker = getattr(self.camera, '_worker', None)
        if worker and hasattr(worker, 'send_command'):
            worker.send_command("connect")

    # ── Live View ─────────────────────────────

    def _start_live_view(self):
        self._stop_live_view()
        if not self.camera.is_connected():
            print("[UI] Live view: camera niet verbonden, probeer reconnect...")
            try:
                self.camera.connect()
            except Exception as e:
                print(f"[UI] Reconnect mislukt: {e}")
            if not self.camera.is_connected():
                print("[UI] Live view: reconnect mislukt")
                return
        # Connect worker signals (once only, using UniqueConnection)
        worker = getattr(self.camera, '_worker', None)
        if worker:
            try:
                worker.frame_ready.connect(
                    self._on_live_frame, Qt.UniqueConnection)
            except (TypeError, AttributeError):
                pass  # Already connected or signal doesn't exist
            try:
                if hasattr(worker, 'connection_lost'):
                    worker.connection_lost.connect(
                        self._on_connection_lost, Qt.UniqueConnection)
            except (TypeError, AttributeError):
                pass
        self._lv_connected = True
        self._lv_warmup_frames = 0  # Show frames immediately
        self.camera.start_live_view()

    def _stop_live_view(self, blocking=True):
        """Stop live view."""
        try:
            self.camera.stop_live_view()
        except Exception as e:
            print(f"[UI] Stop live view fout: {e}")

    def _on_live_frame(self, frame_data):
        # Frozen: keep showing last frame, ignore new frames
        if getattr(self, '_live_view_frozen', False):
            return

        # Skip first frames while camera exposure settles (prevents overexposed flash)
        warmup = getattr(self, '_lv_warmup_frames', 0)
        if warmup > 0:
            self._lv_warmup_frames = warmup - 1
            return

        # Early return BEFORE decode — don't waste CPU on frames we won't show
        if self.state == State.PREVIEW:
            if self.stack.currentIndex() == self.pages.get("preview", -1):
                label = self.countdown_live_label
            else:
                label = self.live_view_label
        elif self.state in (State.COUNTDOWN, State.CAPTURE):
            if getattr(self, '_showing_preview', False):
                return
            label = self.countdown_live_label
        else:
            return

        target_w = label.width()
        target_h = label.height()
        if target_w <= 0 or target_h <= 0:
            return

        # Decode JPEG directly to QPixmap (skip QImage intermediate copy)
        pixmap = QPixmap()
        if not pixmap.loadFromData(frame_data):
            return

        # Apply camera mirror + rotation from event settings
        ev = self.active_event
        if ev:
            if ev.camera_mirror or ev.camera_rotation:
                from PyQt5.QtGui import QTransform
                transform = QTransform()
                if ev.camera_mirror:
                    transform = transform.scale(-1, 1)
                if ev.camera_rotation:
                    transform = transform.rotate(ev.camera_rotation)
                pixmap = pixmap.transformed(transform, Qt.FastTransformation)

        # Buffer frames for boomerang GIF during countdown (every 3rd frame)
        if getattr(self, '_frame_buffer', None) and self.state == State.COUNTDOWN:
            self._fb_skip = getattr(self, '_fb_skip', 0) + 1
            if self._fb_skip % 3 == 0:
                try:
                    self._frame_buffer.add_frame(frame_data)
                except Exception:
                    pass

        # Crop live view to match template frame aspect ratio
        # This ensures the photographer sees exactly what will be printed
        template = self.selected_template
        if template and template.frames:
            # Use current photo's frame (or first frame if all same size)
            idx = min(self.current_photo_num, len(template.frames) - 1)
            frame = template.frames[idx]
            fw, fh = frame.width, frame.height
            # Apply frame rotation to aspect ratio
            if getattr(frame, 'rotation', 0) in (90, 270, -90, -270):
                fw, fh = fh, fw
            if fw > 0 and fh > 0:
                frame_ratio = fw / fh  # target aspect ratio
                pw, ph = pixmap.width(), pixmap.height()
                cam_ratio = pw / ph if ph > 0 else 1.0
                if abs(cam_ratio - frame_ratio) > 0.05:
                    # Crop center of camera image to match frame aspect ratio
                    if cam_ratio > frame_ratio:
                        # Camera is wider: crop sides
                        new_w = int(ph * frame_ratio)
                        x_offset = (pw - new_w) // 2
                        pixmap = pixmap.copy(x_offset, 0, new_w, ph)
                    else:
                        # Camera is taller: crop top/bottom
                        new_h = int(pw / frame_ratio)
                        y_offset = (ph - new_h) // 2
                        pixmap = pixmap.copy(0, y_offset, pw, new_h)

        # Scale to logical label size (no DPR multiplication)
        scaled = pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio,
                               Qt.FastTransformation)

        # Free old pixmap before setting new one (prevents memory buildup at 25fps)
        old_pixmap = self._last_live_pixmap
        label.setPixmap(scaled)
        self._last_live_pixmap = scaled
        del pixmap
        if old_pixmap is not None and old_pixmap is not scaled:
            del old_pixmap

    def _on_connection_lost(self):
        # Ignore during capture — false positive from live view watchdog
        if self.state in (State.CAPTURE, State.COUNTDOWN):
            print(f"[UI] Connection lost genegeerd (state={self.state})")
            return
        print("[UI] Connection lost signal ontvangen, probeer auto-reconnect...")
        self.camera._connected = False
        self.camera._live_view_started = False
        # Try auto-reconnect before showing error
        self._auto_reconnect_attempts = getattr(self, '_auto_reconnect_attempts', 0) + 1
        if self._auto_reconnect_attempts <= 3:
            print(f"[UI] Auto-reconnect poging {self._auto_reconnect_attempts}/3...")
            QTimer.singleShot(2000, self._try_auto_reconnect)
        else:
            self._auto_reconnect_attempts = 0
            self._show_error(t("camera_lost"))

    def _try_auto_reconnect(self):
        """Try to reconnect camera automatically (non-blocking)."""
        # Send disconnect + connect commands (non-blocking)
        self.camera._worker.send_command("disconnect")
        # After 1.5s, send connect and check result after 5s
        QTimer.singleShot(1500, self._auto_reconnect_connect)

    def _auto_reconnect_connect(self):
        """Send connect command for auto-reconnect."""
        self.camera._connect_event.clear()
        self.camera._worker.send_command("connect")
        # Check result after 5 seconds
        QTimer.singleShot(5000, self._auto_reconnect_check)

    def _auto_reconnect_check(self):
        """Check if auto-reconnect succeeded."""
        if self.camera.is_connected():
            print("[UI] Auto-reconnect gelukt!")
            self._auto_reconnect_attempts = 0
            if self.state in (State.PREVIEW, State.COUNTDOWN):
                self.camera.start_live_view()
        else:
            # Try again or show error
            self._on_connection_lost()

    # ── Utilities ─────────────────────────────

    def _update_status(self):
        parts = []
        if self.camera.is_connected():
            parts.append("Camera: OK")
        else:
            parts.append("Camera: --")
        printers = get_available_printers()
        hiti = [p for p in printers if "hiti" in p.lower() or "p525" in p.lower()]
        if hiti:
            parts.append(f"Printer: {hiti[0]}")
        else:
            parts.append("Printer: --")
        self.status_label.setText("  |  ".join(parts))

    def _go_fullscreen(self):
        """Enter fullscreen mode."""
        if not self.isFullScreen():
            self.showFullScreen()

    def _restore_fullscreen_flags(self):
        """Restore frameless + topmost window flags and go fullscreen (after settings)."""
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowTitle("Photobooth")
        self.showFullScreen()

    def _is_portrait(self):
        """Check if screen is in portrait orientation."""
        return self.height() > self.width()

    def changeEvent(self, event):
        """Restore fullscreen if Windows tablet gesture pulls the window out of fullscreen.
        Does NOT restore fullscreen when in settings — settings run in fullscreen already."""
        from PyQt5.QtCore import QEvent
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if not self.isFullScreen() and self.isVisible():
                # Only restore if we're not intentionally in a non-fullscreen state
                if getattr(self, 'state', None) not in (State.SETTINGS,):
                    print(
                        f"[WINDOW] Staat veranderd naar niet-volledigscherm — herstel fullscreen",
                        flush=True
                    )
                    QTimer.singleShot(50, self.showFullScreen)

    def moveEvent(self, event):
        """Log any window move for diagnostics — no correction, logging only."""
        super().moveEvent(event)
        old = event.oldPos()
        new = event.pos()
        if old != new:
            print(
                f"[MOVE] venster verplaatst: ({old.x()},{old.y()}) → ({new.x()},{new.y()})"
                f"  state={self.state}  grootte={self.width()}x{self.height()}",
                flush=True
            )

    def resizeEvent(self, event):
        old = event.oldSize()
        new = event.size()
        pos = self.pos()
        print(
            f"[RESIZE] {old.width()}x{old.height()} → {new.width()}x{new.height()}"
            f"  pos=({pos.x()},{pos.y()})  state={self.state}",
            flush=True
        )
        super().resizeEvent(event)
        if self.state in (State.PREVIEW, State.COUNTDOWN, State.CAPTURE):
            self._position_session_overlays()
        elif self.state == State.REVIEW:
            self._adapt_review_layout()
            self._display_review_strip()
            self._position_countdown_bar()
            if self._qr_overlay.isVisible():
                self._position_qr_overlay()
        elif self.state == State.IDLE:
            self._position_idle_lock()
            self._position_idle_wifi_tip()
        # Reposition printer-fout overlay + QR-code bij elke resize
        if getattr(self, '_dnp_error_overlay', None) is not None:
            try:
                self._dnp_error_overlay.setGeometry(
                    0, 0, self.width(), self.height()
                )
                qr_box = getattr(self, '_dnp_overlay_qr_box', None)
                if qr_box is not None:
                    margin = 40
                    qr_box.move(self.width() - qr_box.width() - margin, margin)
                    qr_box.raise_()
            except Exception:
                pass

    def _position_countdown_bar(self):
        """Position countdown bar at top of review page, full screen width."""
        if hasattr(self, '_sharing_countdown_bar'):
            screen = self.screen()
            pw = screen.geometry().width() if screen else self._review_page.width()
            self._sharing_countdown_bar.setGeometry(0, 0, pw, 6)
            self._sharing_countdown_bar.raise_()

    def _position_idle_lock(self):
        """Position lock button in bottom-right corner of idle page."""
        if not hasattr(self, '_idle_lock_btn'):
            return
        page = self.stack.widget(self.pages.get("idle", 0))
        # Use screen geometry mapped to page coords — robust against layout shifts
        # (page.height() can be wrong if QStackedWidget is forced larger than screen
        #  by a sizeHint from a live-view pixmap on another page)
        from PyQt5.QtCore import QPoint
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            # Map screen bottom-right corner into page coordinate space
            br_global = QPoint(sg.right(), sg.bottom())
            br_page = page.mapFromGlobal(br_global)
            pw = br_page.x() + 1
            ph = br_page.y() + 1
        else:
            pw, ph = page.width(), page.height()
        ls = self._idle_lock_btn.width()
        if pw < 10 or ph < 10:
            return
        self._idle_lock_btn.move(pw - ls - 10, ph - ls - 10)
        self._idle_lock_btn.raise_()
        self._idle_lock_btn.show()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            try:
                if self.state == State.SETTINGS:
                    print("[UI] Escape: terug naar idle", flush=True)
                    self._go_idle()
                elif self.state == State.IDLE:
                    print("[UI] Escape: naar settings", flush=True)
                    self._go_settings()
                else:
                    # During capture/review: go back to idle
                    print(f"[UI] Escape: terug naar idle (was {self.state})", flush=True)
                    self._go_idle()
            except Exception as e:
                print(f"[UI] Escape fout: {e}", flush=True)
                import traceback; traceback.print_exc()
            return
        elif key == Qt.Key_F11:
            if self.isFullScreen():
                self.showMaximized()
            else:
                self._go_fullscreen()
        elif key == Qt.Key_F12:
            self._open_editor()
        elif key == Qt.Key_Space and self.state == State.PREVIEW:
            self._start_countdown()

    def _open_editor(self):
        """Open the template editor window (F12)."""
        try:
            from template_editor import TemplateEditorWindow
            self._editor = TemplateEditorWindow()
            self._editor.show()
        except Exception as e:
            print(f"[EDITOR] Fout bij openen editor: {type(e).__name__}: {e}")

    # ── SETTINGS PAGE ──────────────────────────

    # ── Settings / Operator Panel ───────────────

    def _settings_card(self, title=None):
        """Create a card widget with optional title for settings sections."""
        card = QWidget()
        card.setStyleSheet(f"background: {config.COLOR_CARD_BG}; border-radius: 14px;")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(22, 18, 22, 18)
        card_lay.setSpacing(14)
        if title:
            title_lbl = QLabel(title)
            title_lbl.setFont(QFont("DM Sans", 15, QFont.Bold))
            title_lbl.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            card_lay.addWidget(title_lbl)
        return card, card_lay

    def _settings_tab_scroll(self):
        """Create a scroll area for a settings tab with kinetic scrolling."""
        from PyQt5.QtWidgets import QScroller
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            f"QScrollBar:vertical {{ background: {config.COLOR_BG}; width: 14px; border-radius: 7px; }}"
            f"QScrollBar::handle:vertical {{ background: {config.COLOR_BORDER}; border-radius: 7px; min-height: 60px; }}"
        )
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        content = QWidget()
        content.setStyleSheet(f"background: {config.COLOR_BG};")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 10, 10, 10)
        content_lay.setSpacing(14)
        scroll.setWidget(content)
        return scroll, content_lay

    # Index van de Geavanceerd-tab in tab_names (Event=0, Layout=1,
    # Print=2, Geavanceerd=3). Achter een aparte code.
    _ADVANCED_TAB_INDEX = 3

    def _switch_settings_tab(self, index):
        """Switch the active settings tab.

        Geavanceerd (index 3) zit achter config.ADVANCED_TAB_CODE: bij de
        eerste keer openen deze settings-sessie wordt de code gevraagd.
        Fout/annuleer → blijf op de huidige tab. Eenmaal ontgrendeld blijft
        het open tot settings volledig verlaten wordt (_advanced_unlocked
        wordt gereset in _go_idle)."""
        if index == self._ADVANCED_TAB_INDEX and not getattr(
                self, '_advanced_unlocked', False):
            code = getattr(config, 'ADVANCED_TAB_CODE', '') or ''
            if code:
                entered, ok = PinDialog.get_pin(self, "Code Geavanceerd")
                if not ok or (entered or '').strip() != code:
                    if ok:
                        self._show_error("Onjuiste code.")
                    return  # blijf op huidige tab
            self._advanced_unlocked = True

        self._settings_tab_stack.setCurrentIndex(index)
        for i, btn in enumerate(self._settings_tab_buttons):
            if i == index:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
                    f"border: none; border-radius: 24px; padding: 14px 28px; font-size: 15px; "
                    f"font-weight: bold; min-height: 48px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT}; "
                    f"border: 2px solid {config.COLOR_BORDER}; border-radius: 24px; padding: 14px 28px; "
                    f"font-size: 15px; min-height: 48px; }}"
                    f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
                    f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; }}"
                )

    def _build_settings_page(self):
        """Operator panel: event dropdown + tabbed settings interface."""
        page = QWidget()
        page.setStyleSheet(f"background: {config.COLOR_BG};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(30, 20, 30, 16)
        lay.setSpacing(0)

        # Title row: AFSLUITEN + INSTELLINGEN + LAUNCH EVENT
        title_row = QHBoxLayout()
        quit_btn = QPushButton(t("quit"))
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        quit_btn.setStyleSheet(
            "QPushButton { background: #cc3333; color: #ffffff; "
            "border: none; border-radius: 10px; padding: 10px 20px; "
            "font-size: 12px; min-height: 0; }"
            "QPushButton:hover { background: #dd4444; }"
        )
        quit_btn.clicked.connect(self._on_quit)
        title_row.addWidget(quit_btn)
        title_row.addStretch()
        title_row.addWidget(self._make_title(t("settings_title"), 30))
        title_row.addStretch()
        launch_btn = QPushButton(t("launch_event"))
        launch_btn.setCursor(Qt.PointingHandCursor)
        launch_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        launch_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: #ffffff; "
            f"border: none; border-radius: 10px; padding: 12px 28px; "
            f"font-size: 14px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        launch_btn.clicked.connect(self._launch_event)
        title_row.addWidget(launch_btn)
        lay.addLayout(title_row)
        lay.addSpacing(10)

        # Event dropdown is now inside Startscherm tab (below)
        # Create combo here so it's accessible, but don't add to this layout
        self._event_combo = QComboBox()
        self._event_combo.setFont(QFont("DM Sans", 14))
        self._event_combo.setMinimumHeight(44)
        self._event_combo.setStyleSheet(
            f"QComboBox {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 8px; padding: 8px 14px; font-size: 14px; }}"
            f"QComboBox:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
            f"QComboBox::drop-down {{ border: none; width: 30px; }}"
            f"QComboBox::down-arrow {{ image: none; border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; selection-background-color: {config.COLOR_PRIMARY}; "
            f"font-size: 14px; }}"
        )
        self._event_combo.currentIndexChanged.connect(self._on_event_dropdown_changed)

        # ── Tab bar (single row, no Canva — Canva is in Layout card) ──
        tab_bar_container = QVBoxLayout()
        tab_bar_container.setSpacing(0)
        tab_bar_container.setContentsMargins(0, 0, 0, 0)
        # Verhuur-versie: Delen + Betalingen + Camera tabs verborgen (camera
        # settings zijn verplaatst naar Geavanceerd).
        tab_names = [t("tab_event"), t("tab_layout"), t("tab_print"), t("tab_advanced")]
        self._settings_tab_buttons = []

        screen = self.screen()
        is_portrait = screen.geometry().height() > screen.geometry().width() if screen else False
        tab_font_size = 10 if is_portrait else 14

        # Indices van tabs die verborgen moeten worden (Layout-tab heeft geen
        # functie meer sinds cloud-templates — templates komen uit portaal).
        # Code blijft staan (tab_stack pagina wordt nog geappend) zodat
        # template-grid logica niet hoeft worden weggesneden.
        _HIDDEN_TAB_INDICES = {1}  # 1 = Layout

        row_lay = QHBoxLayout()
        row_lay.setSpacing(4)
        for i, name in enumerate(tab_names):
            btn = QPushButton(name)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", tab_font_size, QFont.Bold))
            btn.clicked.connect(lambda _, idx=i: self._switch_settings_tab(idx))
            self._settings_tab_buttons.append(btn)
            if i in _HIDDEN_TAB_INDICES:
                btn.setVisible(False)
            else:
                row_lay.addWidget(btn)
        row_lay.addStretch()
        tab_bar_container.addLayout(row_lay)

        lay.addLayout(tab_bar_container)
        lay.addSpacing(10)

        # ── Stacked widget for tab content ──
        self._settings_tab_stack = QStackedWidget()
        self._settings_tab_stack.setStyleSheet("background: transparent;")

        # Common styles
        combo_style = (
            f"QComboBox {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 13px; }}"
            f"QComboBox::drop-down {{ width: 30px; }}"
            f"QComboBox QAbstractItemView {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"selection-background-color: {config.COLOR_PRIMARY}; min-height: 30px; }}"
        )
        toggle_style = (
            f"QCheckBox {{ color: {config.COLOR_TEXT}; spacing: 10px; font-size: 14px; background: transparent; }}"
            f"QCheckBox::indicator {{ width: 44px; height: 24px; border-radius: 12px; "
            f"border: 2px solid {config.COLOR_BORDER}; background: {config.COLOR_BORDER}; }}"
            f"QCheckBox::indicator:checked {{ background: {config.COLOR_PRIMARY}; "
            f"border-color: {config.COLOR_PRIMARY}; }}"
        )
        label_style = f"color: {config.COLOR_TEXT}; background: transparent;"
        dim_label_style = f"color: {config.COLOR_TEXT_DIM}; background: transparent;"

        # ════════════════════════════════════════════
        # TAB 0: Startscherm
        # ════════════════════════════════════════════
        tab0_scroll, tab0_lay = self._settings_tab_scroll()

        # Card: Event selection (moved here from top of settings)
        card_event, card_event_lay = self._settings_card(t("tab_event"))
        event_row = QHBoxLayout()
        event_row.setSpacing(10)
        event_row.addWidget(self._event_combo, stretch=1)

        btn_style_primary = (
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px 18px; "
            f"font-size: 13px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        btn_style_danger = (
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: #ffffff; "
            f"border: none; border-radius: 8px; padding: 8px 18px; "
            f"font-size: 13px; min-height: 0; }}"
            f"QPushButton:hover {{ background: #d44637; }}"
        )

        new_event_btn = QPushButton(t("new_event"))
        new_event_btn.setCursor(Qt.PointingHandCursor)
        new_event_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        new_event_btn.setStyleSheet(btn_style_primary)
        new_event_btn.clicked.connect(self._on_event_create_new)
        event_row.addWidget(new_event_btn)
        self._new_event_btn = new_event_btn  # voor verbergen in Linked-modus

        del_event_btn = QPushButton(t("delete").upper())
        del_event_btn.setCursor(Qt.PointingHandCursor)
        del_event_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        del_event_btn.setStyleSheet(btn_style_danger)
        del_event_btn.clicked.connect(self._on_event_delete)
        event_row.addWidget(del_event_btn)
        self._del_event_btn = del_event_btn  # voor verbergen in Linked-modus
        # Wrap event_row in een QWidget zodat we 'm als geheel kunnen hide()
        self._event_picker_row = QWidget()
        self._event_picker_row.setLayout(event_row)
        card_event_lay.addWidget(self._event_picker_row)

        # Originele addLayout vervangen door addWidget hierboven — voorkomt
        # dubbele toevoeging. (volgende regel die addLayout(event_row) was
        # blijft hieronder als no-op verwijderd door deze patch).

        # Photo storage toggle + open folder button
        photo_storage_row = QHBoxLayout()
        photo_storage_row.setSpacing(8)
        self._save_photos_toggle = ToggleSwitch(t("option_save_photos_locally"))
        self._save_photos_toggle.setFont(QFont("DM Sans", 11))
        self._save_photos_toggle.setStyleSheet(toggle_style)
        self._save_photos_toggle.setChecked(True)
        self._save_photos_toggle.toggled.connect(self._on_save_photos_toggled)
        photo_storage_row.addWidget(self._save_photos_toggle)

        open_photos_btn = QPushButton(t("btn_open_saved_photos"))
        open_photos_btn.setCursor(Qt.PointingHandCursor)
        open_photos_btn.setFont(QFont("DM Sans", 9))
        open_photos_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_PRIMARY}; "
            f"border: none; padding: 2px 0; text-decoration: underline; font-size: 9px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        open_photos_btn.clicked.connect(self._open_photos_folder)
        photo_storage_row.addWidget(open_photos_btn)
        photo_storage_row.addStretch()
        card_event_lay.addLayout(photo_storage_row)
        # Verhuur: toggle + "Opgeslagen foto's"-link verbergen, foto's worden
        # altijd lokaal opgeslagen (zie save_photos_locally hardcoded in
        # booth_settings._apply_verhuur_overrides).
        self._save_photos_toggle.setVisible(False)
        open_photos_btn.setVisible(False)

        tab0_lay.addWidget(card_event)
        self._card_event = card_event  # voor verbergen in verhuur (alleen Gekoppeld-kaart op deze tab)

        # Card: Idle background
        card_bg, card_bg_lay = self._settings_card(t("card_idle_bg"))

        # Radio buttons: standaard / custom
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        radio_style = (
            f"QRadioButton {{ color: {config.COLOR_TEXT}; font-size: 14px; spacing: 8px; background: transparent; }}"
            f"QRadioButton::indicator {{ width: 20px; height: 20px; }}"
        )
        self._idle_radio_default = QRadioButton(t("bg_default"))
        self._idle_radio_default.setFont(QFont("DM Sans", 13))
        self._idle_radio_default.setStyleSheet(radio_style)
        self._idle_radio_default.setCursor(Qt.PointingHandCursor)
        card_bg_lay.addWidget(self._idle_radio_default)

        # Default preview
        self._idle_default_container = QWidget()
        self._idle_default_container.setStyleSheet("background: transparent;")
        default_lay = QHBoxLayout(self._idle_default_container)
        default_lay.setContentsMargins(28, 4, 0, 8)
        default_lay.setSpacing(10)
        self._bg_preview_label = QLabel()
        self._bg_preview_label.setFixedSize(120, 80)
        self._bg_preview_label.setAlignment(Qt.AlignCenter)
        self._bg_preview_label.setStyleSheet(
            f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 6px;"
        )
        self._bg_preview_label.setText(t("bg_default"))
        self._bg_preview_label.setFont(QFont("DM Sans", 10))
        default_lay.addWidget(self._bg_preview_label)
        self._idle_default_info = QLabel("")
        self._idle_default_info.setFont(QFont("DM Sans", 11))
        self._idle_default_info.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        self._idle_default_info.setWordWrap(True)
        default_lay.addWidget(self._idle_default_info, 1)
        default_lay.addStretch()
        card_bg_lay.addWidget(self._idle_default_container)

        self._idle_radio_custom = QRadioButton(t("bg_custom"))
        self._idle_radio_custom.setFont(QFont("DM Sans", 13))
        self._idle_radio_custom.setStyleSheet(radio_style)
        self._idle_radio_custom.setCursor(Qt.PointingHandCursor)
        card_bg_lay.addWidget(self._idle_radio_custom)

        # Custom: file picker + resolution instruction
        self._idle_custom_container = QWidget()
        self._idle_custom_container.setStyleSheet("background: transparent;")
        custom_lay = QVBoxLayout(self._idle_custom_container)
        custom_lay.setContentsMargins(28, 4, 0, 8)
        custom_lay.setSpacing(8)
        custom_row = QHBoxLayout()
        custom_row.setSpacing(10)
        self._custom_bg_preview = QLabel()
        self._custom_bg_preview.setFixedSize(120, 80)
        self._custom_bg_preview.setAlignment(Qt.AlignCenter)
        self._custom_bg_preview.setStyleSheet(
            f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 6px;"
        )
        self._custom_bg_preview.setText(t("bg_none"))
        self._custom_bg_preview.setFont(QFont("DM Sans", 10))
        custom_row.addWidget(self._custom_bg_preview)
        bg_btn = QPushButton(t("choose_image"))
        bg_btn.setCursor(Qt.PointingHandCursor)
        bg_btn.setFont(QFont("DM Sans", 13))
        bg_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 10px 20px; "
            f"font-size: 13px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        bg_btn.clicked.connect(self._change_idle_background)
        custom_row.addWidget(bg_btn)
        custom_row.addStretch()
        custom_lay.addLayout(custom_row)
        self._idle_resolution_hint = QLabel("")
        self._idle_resolution_hint.setFont(QFont("DM Sans", 11))
        self._idle_resolution_hint.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; background: transparent; padding: 4px 0;"
        )
        self._idle_resolution_hint.setWordWrap(True)
        custom_lay.addWidget(self._idle_resolution_hint)
        card_bg_lay.addWidget(self._idle_custom_container)

        # Radio group
        self._idle_mode_group = QButtonGroup(self)
        self._idle_mode_group.addButton(self._idle_radio_default, 0)
        self._idle_mode_group.addButton(self._idle_radio_custom, 1)
        self._idle_radio_default.setChecked(True)
        self._idle_radio_default.toggled.connect(self._on_idle_mode_changed)
        self._idle_radio_custom.toggled.connect(self._on_idle_mode_changed)

        tab0_lay.addWidget(card_bg)
        self._card_idle_bg = card_bg  # voor verbergen in Linked-modus
        # tab0_lay zelf onthouden voor het verplaatsen van Gekoppeld-kaart
        self._tab0_lay = tab0_lay

        # Card: Intro screen (shown before countdown)
        card_intro, card_intro_lay = self._settings_card(t("card_intro"))

        # Hidden preview label kept for compatibility with _update_intro_preview
        self._intro_preview_label = QLabel()
        self._intro_preview_label.hide()

        # Intro screen duration
        intro_dur_row = QHBoxLayout()
        intro_dur_row.setSpacing(10)
        intro_dur_label = QLabel(t("intro_duration"))
        intro_dur_label.setFont(QFont("DM Sans", 13))
        intro_dur_label.setStyleSheet(label_style)
        intro_dur_row.addWidget(intro_dur_label)
        self._intro_duration_spin = self._make_touch_spin(1, 10, 2,
                                                          "s", on_change=self._on_intro_duration_changed, step=1)
        intro_dur_row.addWidget(self._intro_duration_spin)
        intro_dur_row.addStretch()
        card_intro_lay.addLayout(intro_dur_row)

        # Intro text toggle + input
        self._intro_text_toggle = ToggleSwitch(t("intro_show_text"))
        self._intro_text_toggle.setStyleSheet(toggle_style)
        self._intro_text_toggle.setFont(QFont("DM Sans", 13))
        self._intro_text_toggle.setChecked(True)
        self._intro_text_toggle.toggled.connect(self._on_intro_text_toggled)
        card_intro_lay.addWidget(self._intro_text_toggle)

        intro_text_row = QHBoxLayout()
        intro_text_row.setSpacing(10)
        intro_text_lbl = QLabel(t("intro_text_label"))
        intro_text_lbl.setFont(QFont("DM Sans", 13))
        intro_text_lbl.setStyleSheet(label_style)
        intro_text_row.addWidget(intro_text_lbl)
        from PyQt5.QtWidgets import QLineEdit
        self._intro_text_input = QLineEdit()
        self._intro_text_input.setFont(QFont("DM Sans", 13))
        self._intro_text_input.setMinimumHeight(40)
        self._intro_text_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 6px 12px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._intro_text_input.setText(t("intro_default_text"))
        self._intro_text_input.textChanged.connect(self._on_intro_text_changed)
        intro_text_row.addWidget(self._intro_text_input)
        card_intro_lay.addLayout(intro_text_row)
        tab0_lay.addWidget(card_intro)
        # Verhuur: intro-scherm verbergen (geen instelbare intro tekst/duur).
        card_intro.setVisible(False)

        tab0_lay.addStretch()
        self._settings_tab_stack.addWidget(tab0_scroll)

        # ════════════════════════════════════════════
        # TAB 1: Layout
        # ════════════════════════════════════════════
        tab1_scroll, tab1_lay = self._settings_tab_scroll()

        # Card: Layout selection
        card_layout, card_layout_lay = self._settings_card(t("card_layout"))
        active_row = QHBoxLayout()
        active_row.setSpacing(12)
        self.settings_active_label = QLabel(t("no_layout"))
        self.settings_active_label.setFont(QFont("DM Sans", 13))
        self.settings_active_label.setStyleSheet(dim_label_style)
        active_row.addWidget(self.settings_active_label)
        self._edit_layout_btn = QPushButton("\u270f " + t("edit_layout"))
        self._edit_layout_btn.setCursor(Qt.PointingHandCursor)
        self._edit_layout_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._edit_layout_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px 20px; "
            f"font-size: 12px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._edit_layout_btn.clicked.connect(self._open_layout_editor)
        self._edit_layout_btn.setVisible(False)
        active_row.addWidget(self._edit_layout_btn)
        active_row.addStretch()
        card_layout_lay.addLayout(active_row)

        # Background image row
        bg_row = QHBoxLayout()
        bg_row.setSpacing(12)
        self._layout_bg_label = QLabel(t("bg_white"))
        self._layout_bg_label.setFont(QFont("DM Sans", 12))
        self._layout_bg_label.setStyleSheet(dim_label_style)
        bg_row.addWidget(self._layout_bg_label)
        self._layout_bg_btn = QPushButton(t("bg_change"))
        self._layout_bg_btn.setCursor(Qt.PointingHandCursor)
        self._layout_bg_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        self._layout_bg_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 6px 16px; "
            f"font-size: 11px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._layout_bg_btn.clicked.connect(self._on_layout_bg_change)
        self._layout_bg_btn.setVisible(False)
        bg_row.addWidget(self._layout_bg_btn)
        self._layout_bg_remove_btn = QPushButton(t("delete"))
        self._layout_bg_remove_btn.setCursor(Qt.PointingHandCursor)
        self._layout_bg_remove_btn.setFont(QFont("DM Sans", 11))
        self._layout_bg_remove_btn.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; "
            "border: none; border-radius: 8px; padding: 6px 12px; "
            "font-size: 11px; min-height: 0; }"
            "QPushButton:hover { background: #e74c3c; }"
        )
        self._layout_bg_remove_btn.clicked.connect(self._on_layout_bg_remove)
        self._layout_bg_remove_btn.setVisible(False)
        bg_row.addWidget(self._layout_bg_remove_btn)
        # Canva button (opens canva.com)
        canva_btn = QPushButton("Canva \u2197")
        canva_btn.setCursor(Qt.PointingHandCursor)
        canva_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        canva_btn.setStyleSheet(
            f"QPushButton {{ background: #7d2ae8; color: white; "
            f"border: none; border-radius: 8px; padding: 6px 14px; "
            f"font-size: 10px; min-height: 0; }}"
            f"QPushButton:hover {{ background: #6b21c8; }}"
        )
        canva_btn.clicked.connect(lambda: __import__('PyQt5.QtGui', fromlist=['QDesktopServices']).QDesktopServices.openUrl(
            __import__('PyQt5.QtCore', fromlist=['QUrl']).QUrl("https://www.canva.com")
        ))
        bg_row.addWidget(canva_btn)
        self._canva_btn = canva_btn  # voor verbergen in Linked-modus
        bg_row.addStretch()
        card_layout_lay.addLayout(bg_row)

        # ── Printer-modus selector (3 knoppen: 4x3 / 4x6 / 3 strips) ──
        # Vervangt de oude "Printer-modus" radio in Geavanceerd. Bepaalt
        # welke templates zichtbaar zijn in de grid hieronder + welk
        # DNP printer-profiel gebruikt wordt bij printen.
        pmode_card, pmode_card_lay = self._settings_card("Printer-modus")
        pmode_btn_row = QHBoxLayout()
        pmode_btn_row.setSpacing(8)
        from PyQt5.QtWidgets import QButtonGroup as _BG
        self._printer_mode_group = _BG(self)
        self._printer_mode_group.setExclusive(True)

        def _make_mode_btn(label, mode_val, hint):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", 12, QFont.Bold))
            btn.setMinimumHeight(56)
            btn.setToolTip(hint)
            btn.setStyleSheet(
                f"QPushButton {{ background: {config.COLOR_INPUT_BG}; "
                f"color: {config.COLOR_TEXT}; border: 2px solid {config.COLOR_BORDER}; "
                f"border-radius: 10px; padding: 8px 14px; font-size: 12px; }}"
                f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
                f"QPushButton:checked {{ background: {config.COLOR_PRIMARY}; "
                f"color: {config.COLOR_TEXT_ON_PRIMARY}; "
                f"border: 2px solid {config.COLOR_PRIMARY_HOVER}; }}"
            )
            btn._mode_value = mode_val
            btn.toggled.connect(
                lambda checked, m=mode_val: checked and self._on_printer_mode_changed_v2(m)
            )
            return btn

        self._printer_mode_btn_4x3 = _make_mode_btn(
            "4x3", "4x3", "1 grote print op 4x3 paper (half-size, geen cut)")
        self._printer_mode_btn_4x6 = _make_mode_btn(
            "4x6", "4x6", "Canon dubbele strip op 4x6 paper (cut tussen 2 helften)")
        self._printer_mode_btn_3strips = _make_mode_btn(
            "3 strips", "3strips", "DNP 4x6 paper met 2-inch cut (3 strips van 5x10cm)")

        for btn in (self._printer_mode_btn_4x3,
                    self._printer_mode_btn_4x6,
                    self._printer_mode_btn_3strips):
            self._printer_mode_group.addButton(btn)
            pmode_btn_row.addWidget(btn, 1)

        # Default selectie (wordt overschreven bij _load_settings_for_event)
        self._printer_mode_btn_3strips.setChecked(True)
        pmode_card_lay.addLayout(pmode_btn_row)
        card_layout_lay.addWidget(pmode_card)
        # Printer-modus card verbergen: wordt nu automatisch afgeleid uit
        # het gekozen template (is_triple_strip → 3strips cut, is_4x3_strip →
        # 4x3 paper, anders → 4x6 nocut). Widgets blijven bestaan zodat
        # _load_settings_for_event/_on_printer_mode_changed_v2 niet crashen.
        pmode_card.setVisible(False)

        # Layout categories container
        self._layout_categories_container = QVBoxLayout()
        self._layout_categories_container.setSpacing(4)
        card_layout_lay.addLayout(self._layout_categories_container)
        tab1_lay.addWidget(card_layout)

        tab1_lay.addStretch()
        self._settings_tab_stack.addWidget(tab1_scroll)

        # ════════════════════════════════════════════
        # TAB 2: Printen — Sub-tabs: Printer koppelen / Printerinstellingen
        # ════════════════════════════════════════════
        tab2_scroll, tab2_lay = self._settings_tab_scroll()

        # Card: Printer
        card_printer, card_printer_lay = self._settings_card(t("card_printer"))

        # Keep _cut_checkbox as hidden for compatibility
        self._cut_checkbox = QCheckBox()
        self._cut_checkbox.hide()

        # --- Printen aan/uit toggle ---
        self._print_enabled_toggle = ToggleSwitch(t("print_enabled"))
        self._print_enabled_toggle.setFont(QFont("DM Sans", 13))
        self._print_enabled_toggle.setStyleSheet(toggle_style)
        self._print_enabled_toggle.setChecked(True)
        self._print_enabled_toggle.toggled.connect(self._on_print_enabled_toggled)
        card_printer_lay.addWidget(self._print_enabled_toggle)

        # --- Container voor printer-koppel content (verberg als printen uit staat) ---
        # Bevat de "Printer koppelen"-secties direct binnen de eerste card.
        self._printer_settings_container = QWidget()
        connect_lay = QVBoxLayout(self._printer_settings_container)
        connect_lay.setContentsMargins(0, 8, 0, 0)
        connect_lay.setSpacing(10)

        # Printer selection row
        printer_row = QHBoxLayout()
        printer_row.setSpacing(12)
        printer_label = QLabel(t("printer_label"))
        printer_label.setFont(QFont("DM Sans", 13, QFont.Bold))
        printer_label.setStyleSheet(label_style)
        printer_row.addWidget(printer_label)
        self._printer_name_label = QLabel(config.PRINTER_NAME or t("printer_not_selected"))
        self._printer_name_label.setFont(QFont("DM Sans", 12))
        self._printer_name_label.setStyleSheet(dim_label_style)
        printer_row.addWidget(self._printer_name_label)
        # Compact knopstijl voor printer-acties (override global min-height: 60px + font 28px)
        small_btn_style = (
            f"QPushButton {{ background: {{bg}}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 18px; "
            f"font-size: 13px; min-height: 0; min-width: 0; }}"
            f"QPushButton:hover {{ background: {{hov}}; }}"
        )
        select_printer_btn = QPushButton(t("printer_change"))
        select_printer_btn.setCursor(Qt.PointingHandCursor)
        select_printer_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        select_printer_btn.setFixedHeight(36)
        select_printer_btn.setStyleSheet(
            small_btn_style.replace("{bg}", config.COLOR_SECONDARY)
                           .replace("{hov}", config.COLOR_SECONDARY_HOVER)
        )
        select_printer_btn.clicked.connect(self._on_select_printer)
        printer_row.addWidget(select_printer_btn)
        printer_row.addStretch()
        connect_lay.addLayout(printer_row)

        # Test print row (de oude "Driver instellingen: PRINTER INSTELLEN"
        # is vervangen door de DNP-profielen-card hieronder, die per profiel
        # capture-knoppen toont in plaats van één globaal DEVMODE-bestand).
        # _devmode_status_label blijft een hidden stub voor backwards-compat
        # met _update_devmode_status (wordt niet meer in UI getoond).
        from PyQt5.QtWidgets import QLabel as _QL
        self._devmode_status_label = _QL("")
        self._devmode_status_label.setVisible(False)

        test_row = QHBoxLayout()
        test_row.setSpacing(12)
        test_print_btn = QPushButton(t("test_print"))
        test_print_btn.setCursor(Qt.PointingHandCursor)
        test_print_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        test_print_btn.setFixedHeight(36)
        test_print_btn.setStyleSheet(
            small_btn_style.replace("{bg}", config.COLOR_SECONDARY)
                           .replace("{hov}", config.COLOR_SECONDARY_HOVER)
        )
        test_print_btn.clicked.connect(self._on_test_print)
        test_row.addWidget(test_print_btn)
        test_row.addStretch()
        connect_lay.addLayout(test_row)

        # --- Printer-storingsmeldingen aan/uit (standaard AAN) ---
        # Leest de printerstatus uit (online/offline/papier-op) en toont
        # storingsmeldingen. Zet uit bij een tijdelijke niet-DNP printer
        # (bv. Canon CP1500) om valse meldingen te voorkomen. De keuze wordt
        # onthouden in settings.json.
        connect_lay.addSpacing(6)
        self._printer_status_toggle = ToggleSwitch("Printer-storingsmeldingen")
        self._printer_status_toggle.setFont(QFont("DM Sans", 13))
        self._printer_status_toggle.setStyleSheet(toggle_style)
        self._printer_status_toggle.setChecked(self._printer_status_enabled())
        self._printer_status_toggle.toggled.connect(self._on_printer_status_toggled)
        connect_lay.addWidget(self._printer_status_toggle)

        _ps_hint = QLabel(
            "Zet uit bij een tijdelijke niet-ondersteunde printer\n"
            "(bijv. Canon CP1500) om valse storingsmeldingen te voorkomen."
        )
        _ps_hint.setFont(QFont("DM Sans", 10))
        _ps_hint.setStyleSheet(dim_label_style)
        _ps_hint.setWordWrap(True)
        connect_lay.addWidget(_ps_hint)

        # DNP printer-profielen staan NIET inline op deze tab (was te makkelijk
        # toegankelijk voor klanten). Capture-UI zit nu achter een knop in
        # Geavanceerd → 'DNP printer-instellingen' die een dialog opent.

        # Bestel printpapier knop onderaan sub-tab 1
        connect_lay.addSpacing(8)
        order_paper_btn = QPushButton("🛒  " + t("order_paper"))
        order_paper_btn.setCursor(Qt.PointingHandCursor)
        order_paper_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        order_paper_btn.setFixedHeight(42)
        order_paper_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; "
            f"padding: 8px 22px; font-size: 13px; font-weight: bold; "
            f"min-height: 0; min-width: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
        )
        order_paper_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        order_paper_btn.clicked.connect(self._on_order_paper)
        connect_lay.addWidget(order_paper_btn)
        # Verhuur: "Bestel printpapier" knop verbergen.
        order_paper_btn.setVisible(False)

        # _printer_settings_container is nu de wrapper voor de Printer-koppel content
        # (inhoud is hierboven direct via connect_lay toegevoegd).
        card_printer_lay.addWidget(self._printer_settings_container)
        tab2_lay.addWidget(card_printer)

        # ════════════════════════════════════════════
        # Tweede card: Printerinstellingen (los vlak onder de eerste)
        # ════════════════════════════════════════════
        self._print_settings_card, settings_lay = self._settings_card(t("print_subtab_settings"))
        settings_lay.setSpacing(10)

        # Auto print toggle
        self._auto_print_toggle = ToggleSwitch(t("auto_print"))
        self._auto_print_toggle.setFont(QFont("DM Sans", 13))
        self._auto_print_toggle.setStyleSheet(toggle_style)
        self._auto_print_toggle.setChecked(True)
        self._auto_print_toggle.toggled.connect(self._on_auto_print_toggled)
        settings_lay.addWidget(self._auto_print_toggle)

        # Auto-copies container (hidden when auto-print off)
        self._auto_copies_container = QWidget()
        acc_lay = QVBoxLayout(self._auto_copies_container)
        acc_lay.setContentsMargins(0, 0, 0, 0)
        acc_lay.setSpacing(8)

        auto_copies_row = QHBoxLayout()
        auto_copies_row.setSpacing(12)
        auto_copies_label = QLabel(t("auto_copies"))
        auto_copies_label.setFont(QFont("DM Sans", 13))
        auto_copies_label.setStyleSheet(label_style)
        auto_copies_row.addWidget(auto_copies_label)
        self._auto_copies_spin = self._make_touch_spin(1, 5, 1, on_change=self._on_auto_copies_changed)
        auto_copies_row.addWidget(self._auto_copies_spin)
        auto_copies_row.addStretch()
        acc_lay.addLayout(auto_copies_row)

        settings_lay.addWidget(self._auto_copies_container)

        # Extra prints (auto-print ON) / Max prints (auto-print OFF)
        self._prints_limit_container = QWidget()
        plc_lay = QVBoxLayout(self._prints_limit_container)
        plc_lay.setContentsMargins(0, 0, 0, 0)
        plc_lay.setSpacing(8)

        self._extra_prints_row = QWidget()
        epr_lay = QHBoxLayout(self._extra_prints_row)
        epr_lay.setContentsMargins(0, 0, 0, 0)
        epr_lay.setSpacing(12)
        extra_label = QLabel(t("extra_prints"))
        extra_label.setFont(QFont("DM Sans", 13))
        extra_label.setStyleSheet(label_style)
        epr_lay.addWidget(extra_label)
        self._extra_prints_spin = self._make_touch_spin(0, 5, 0, on_change=self._on_extra_prints_changed)
        epr_lay.addWidget(self._extra_prints_spin)
        epr_lay.addStretch()
        plc_lay.addWidget(self._extra_prints_row)

        self._max_prints_row = QWidget()
        mpr_lay = QHBoxLayout(self._max_prints_row)
        mpr_lay.setContentsMargins(0, 0, 0, 0)
        mpr_lay.setSpacing(12)
        max_label = QLabel(t("max_prints"))
        max_label.setFont(QFont("DM Sans", 13))
        max_label.setStyleSheet(label_style)
        mpr_lay.addWidget(max_label)
        self._max_prints_spin = self._make_touch_spin(1, 10, 1, on_change=self._on_max_prints_changed)
        mpr_lay.addWidget(self._max_prints_spin)
        mpr_lay.addStretch()
        plc_lay.addWidget(self._max_prints_row)

        settings_lay.addWidget(self._prints_limit_container)

        # ── NIEUW: Event-limiet sectie ──
        settings_lay.addSpacing(8)
        self._build_event_limit_section(settings_lay, label_style)

        settings_lay.addStretch()
        tab2_lay.addWidget(self._print_settings_card)
        # Verhuur: hele Printerinstellingen sub-kaart verbergen (auto-print +
        # 1 kopie zijn hardcoded via booth_settings.load).
        self._print_settings_card.setVisible(False)

        tab2_lay.addStretch()
        self._settings_tab_stack.addWidget(tab2_scroll)

        # ════════════════════════════════════════════
        # TAB 3: Camera
        # ════════════════════════════════════════════
        tab3_scroll, tab3_lay = self._settings_tab_scroll()

        # Card: Timing
        card_timing, card_timing_lay = self._settings_card(t("card_timing"))
        cd_row = QHBoxLayout()
        cd_row.setSpacing(10)
        cd_label = QLabel(t("countdown_sec"))
        cd_label.setFont(QFont("DM Sans", 13))
        cd_label.setStyleSheet(label_style)
        cd_row.addWidget(cd_label)
        self._countdown_spin = self._make_touch_spin(3, 10, 3, on_change=self._on_countdown_changed)
        cd_row.addWidget(self._countdown_spin)
        cd_row.addStretch()
        card_timing_lay.addLayout(cd_row)

        delay_row = QHBoxLayout()
        delay_row.setSpacing(10)
        delay_label = QLabel(t("pause_between"))
        delay_label.setFont(QFont("DM Sans", 13))
        delay_label.setStyleSheet(label_style)
        delay_row.addWidget(delay_label)
        self._delay_spin = self._make_touch_spin(0, 10, 2, on_change=self._on_delay_changed)
        delay_row.addWidget(self._delay_spin)
        delay_row.addStretch()
        card_timing_lay.addLayout(delay_row)

        sharing_row = QHBoxLayout()
        sharing_row.setSpacing(10)
        sharing_label = QLabel(t("sharing_timeout"))
        sharing_label.setFont(QFont("DM Sans", 13))
        sharing_label.setStyleSheet(label_style)
        sharing_row.addWidget(sharing_label)
        self._sharing_timeout_spin = self._make_touch_spin(10, 120, 30, on_change=self._on_sharing_timeout_changed)
        sharing_row.addWidget(self._sharing_timeout_spin)
        sharing_row.addStretch()
        card_timing_lay.addLayout(sharing_row)
        tab3_lay.addWidget(card_timing)
        card_timing.setVisible(False)  # verhuur: hardcoded countdown/pauze/QR-tijd

        # Card: Camera Modus
        card_cam_mode, card_cam_mode_lay = self._settings_card(t("card_camera_mode"))
        self._card_cam_mode = card_cam_mode  # verhuur: verplaatst naar Geavanceerd

        radio_style = f"QRadioButton {{ font-size: 13px; spacing: 8px; }} QRadioButton::indicator {{ width: 18px; height: 18px; }}"
        combo_style = (
            f"QComboBox {{ background: white; border: 1px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 6px 12px; font-size: 12px; min-width: 180px; }}"
            f"QComboBox::drop-down {{ border: none; padding-right: 8px; }}"
        )
        small_btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )

        cam_mode_row = QHBoxLayout()
        cam_mode_row.setSpacing(20)
        self._cam_dslr_radio = QRadioButton(t("camera_dslr"))
        self._cam_dslr_radio.setFont(QFont("DM Sans", 12))
        self._cam_dslr_radio.setStyleSheet(radio_style)
        self._cam_webcam_radio = QRadioButton(t("camera_webcam"))
        self._cam_webcam_radio.setFont(QFont("DM Sans", 12))
        self._cam_webcam_radio.setStyleSheet(radio_style)
        self._cam_dslr_radio.setChecked(True)
        cam_mode_row.addWidget(self._cam_dslr_radio)
        cam_mode_row.addWidget(self._cam_webcam_radio)
        cam_mode_row.addStretch()
        card_cam_mode_lay.addLayout(cam_mode_row)
        # Verhuur: alleen webcam mogelijk — verberg de keuze-radios.
        self._cam_dslr_radio.setVisible(False)
        self._cam_webcam_radio.setVisible(False)

        # Webcam selection (hidden by default)
        self._webcam_select_row = QWidget()
        wsr = QHBoxLayout(self._webcam_select_row)
        wsr.setContentsMargins(0, 6, 0, 0)
        wsr.setSpacing(10)
        self._webcam_status_label = QLabel(t("no_webcam_selected"))
        self._webcam_status_label.setFont(QFont("DM Sans", 12))
        self._webcam_status_label.setStyleSheet(dim_label_style)
        wsr.addWidget(self._webcam_status_label, stretch=1)
        choose_cam_btn = QPushButton(t("btn_choose_webcam"))
        choose_cam_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        choose_cam_btn.setCursor(Qt.PointingHandCursor)
        choose_cam_btn.setStyleSheet(small_btn_style)
        choose_cam_btn.clicked.connect(self._open_webcam_dialog)
        wsr.addWidget(choose_cam_btn)
        test_cam_btn = QPushButton("Test")
        test_cam_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        test_cam_btn.setCursor(Qt.PointingHandCursor)
        test_cam_btn.setStyleSheet(small_btn_style)
        test_cam_btn.clicked.connect(self._test_webcam_diagnostic)
        wsr.addWidget(test_cam_btn)
        card_cam_mode_lay.addWidget(self._webcam_select_row)
        # Verhuur: webcam-modus is hardcoded → picker altijd zichtbaar.
        self._webcam_select_row.setVisible(True)

        # Connect radio buttons
        self._cam_dslr_radio.toggled.connect(self._on_camera_mode_changed)

        tab3_lay.addWidget(card_cam_mode)

        # Card: Camera Instellingen (mirror + rotation)
        card_cam_set, card_cam_set_lay = self._settings_card(t("card_camera_settings"))
        self._card_cam_set = card_cam_set  # verhuur: verplaatst naar Geavanceerd

        self._cam_mirror_cb = ToggleSwitch(t("camera_mirror"))
        self._cam_mirror_cb.setFont(QFont("DM Sans", 12))
        self._cam_mirror_cb.setStyleSheet(toggle_style)
        self._cam_mirror_cb.toggled.connect(self._on_camera_settings_changed)
        card_cam_set_lay.addWidget(self._cam_mirror_cb)

        rot_row = QHBoxLayout()
        rot_row.setSpacing(10)
        rot_label = QLabel(t("camera_rotation"))
        rot_label.setFont(QFont("DM Sans", 12))
        rot_label.setStyleSheet(dim_label_style)
        rot_row.addWidget(rot_label)
        self._cam_rotation_combo = QComboBox()
        self._cam_rotation_combo.setFont(QFont("DM Sans", 12))
        self._cam_rotation_combo.setStyleSheet(combo_style)
        self._cam_rotation_combo.setMinimumWidth(80)
        self._cam_rotation_combo.setMaximumWidth(120)
        self._cam_rotation_combo.addItems(["0°", "90°", "180°", "270°"])
        self._cam_rotation_combo.currentIndexChanged.connect(self._on_camera_settings_changed)
        rot_row.addWidget(self._cam_rotation_combo)
        rot_row.addStretch()
        card_cam_set_lay.addLayout(rot_row)
        # Verhuur: rotatie hardcoded op 0° — verberg de keuze.
        rot_label.setVisible(False)
        self._cam_rotation_combo.setVisible(False)

        # Positie live view — radio buttons (default: midden centreren = ongewijzigd)
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        lvp_row = QHBoxLayout()
        lvp_row.setSpacing(10)
        lvp_label = QLabel(t("camera_live_view_position"))
        lvp_label.setFont(QFont("DM Sans", 12))
        lvp_label.setStyleSheet(dim_label_style)
        lvp_label.setAlignment(Qt.AlignTop)
        lvp_label.setFixedWidth(160)
        lvp_row.addWidget(lvp_label)

        lvp_col = QVBoxLayout()
        lvp_col.setSpacing(4)
        lvp_radio_style = (
            f"QRadioButton {{ color: {config.COLOR_TEXT}; font-size: 12px; padding: 4px; }}"
            f"QRadioButton::indicator {{ width: 18px; height: 18px; }}"
        )
        self._live_view_pos_group = QButtonGroup(self)
        self._live_view_pos_radios = {}
        for value, key in [
            ("center", "camera_live_view_center"),
            ("top",    "camera_live_view_top"),
        ]:
            rb = QRadioButton(t(key))
            rb.setFont(QFont("DM Sans", 12))
            rb.setStyleSheet(lvp_radio_style)
            rb.toggled.connect(
                lambda checked, v=value: self._on_live_view_position_changed(v) if checked else None
            )
            self._live_view_pos_radios[value] = rb
            self._live_view_pos_group.addButton(rb)
            lvp_col.addWidget(rb)
        # Default selectie — wordt overschreven door _load_event_to_settings later
        self._live_view_pos_radios["center"].setChecked(True)
        lvp_row.addLayout(lvp_col, 1)
        card_cam_set_lay.addLayout(lvp_row)
        # Verhuur: live-view-positie hardcoded op 'center' — verberg de keuze.
        lvp_label.setVisible(False)
        for _rb in self._live_view_pos_radios.values():
            _rb.setVisible(False)

        tab3_lay.addWidget(card_cam_set)

        tab3_lay.addStretch()
        # Verhuur: Camera-tab niet meer in tab-bar. De zichtbare onderdelen
        # (mirror + webcam picker) worden hieronder naar Geavanceerd verplaatst.
        tab3_scroll.setParent(None)

        # ════════════════════════════════════════════
        # TAB 4: Delen
        # ════════════════════════════════════════════
        tab4_scroll, tab4_lay = self._settings_tab_scroll()

        card_share, card_share_lay = self._settings_card(t("card_sharing"))

        # Pro-only banner (shown for Starter plan)
        self._share_pro_banner = QLabel("⭐ " + t("pro_only_msg"))
        self._share_pro_banner.setFont(QFont("DM Sans", 12))
        self._share_pro_banner.setWordWrap(True)
        self._share_pro_banner.setStyleSheet(
            f"color: {config.COLOR_PRIMARY_HOVER}; background: {config.COLOR_ACCENT}; "
            f"border-radius: 8px; padding: 10px 14px;"
        )
        card_share_lay.addWidget(self._share_pro_banner)

        self._qr_toggle = ToggleSwitch(t("qr_toggle"))
        self._qr_toggle.setFont(QFont("DM Sans", 13))
        self._qr_toggle.setStyleSheet(toggle_style)
        self._qr_toggle.setChecked(False)
        self._qr_toggle.toggled.connect(self._on_qr_toggled)
        card_share_lay.addWidget(self._qr_toggle)

        # QR-branding container (alleen zichtbaar als QR aan staat)
        self._qr_branding_container = QWidget()
        qrb_lay = QVBoxLayout(self._qr_branding_container)
        qrb_lay.setContentsMargins(28, 4, 0, 4)  # iets inspringen onder QR-toggle
        qrb_lay.setSpacing(6)

        self._qr_branding_toggle = ToggleSwitch(t("qr_branding_toggle"))
        self._qr_branding_toggle.setFont(QFont("DM Sans", 11))
        self._qr_branding_toggle.setStyleSheet(toggle_style)
        self._qr_branding_toggle.toggled.connect(self._on_qr_branding_toggled)
        qrb_lay.addWidget(self._qr_branding_toggle)

        # Multi-line text editor voor bedrijfsgegevens
        from PyQt5.QtWidgets import QTextEdit
        self._qr_branding_text = QTextEdit()
        self._qr_branding_text.setFont(QFont("DM Sans", 11))
        self._qr_branding_text.setFixedHeight(80)
        self._qr_branding_text.setPlaceholderText(t("qr_branding_placeholder"))
        self._qr_branding_text.setStyleSheet(
            f"QTextEdit {{ background: {config.COLOR_INPUT_BG}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 6px; "
            f"padding: 6px 10px; color: {config.COLOR_TEXT}; }}"
            f"QTextEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._qr_branding_text.textChanged.connect(self._on_qr_branding_text_changed)
        qrb_lay.addWidget(self._qr_branding_text)
        # Tekstveld default verborgen — alleen tonen als toggle aan staat
        self._qr_branding_text.setVisible(False)

        card_share_lay.addWidget(self._qr_branding_container)
        # Container default verborgen — alleen tonen als QR aan staat
        self._qr_branding_container.setVisible(False)

        self._email_toggle = ToggleSwitch(t("email_toggle"))
        self._email_toggle.setFont(QFont("DM Sans", 13))
        self._email_toggle.setStyleSheet(toggle_style)
        self._email_toggle.setChecked(False)
        self._email_toggle.toggled.connect(self._on_email_toggled)
        card_share_lay.addWidget(self._email_toggle)

        # Data collection row
        collect_row = QHBoxLayout()
        collect_row.setSpacing(8)
        self._email_collect_toggle = ToggleSwitch(t("data_collect_toggle"))
        self._email_collect_toggle.setFont(QFont("DM Sans", 11))
        self._email_collect_toggle.setStyleSheet(toggle_style)
        self._email_collect_toggle.setChecked(False)
        self._email_collect_toggle.toggled.connect(self._on_email_collect_toggled)
        collect_row.addWidget(self._email_collect_toggle)
        self._data_collect_settings_btn = QPushButton(t("data_collect_setup"))
        self._data_collect_settings_btn.setCursor(Qt.PointingHandCursor)
        self._data_collect_settings_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        self._data_collect_settings_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 4px 12px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._data_collect_settings_btn.clicked.connect(self._open_data_collect_dialog)
        collect_row.addWidget(self._data_collect_settings_btn)

        open_csv_btn = QPushButton(t("btn_open_saved_data"))
        open_csv_btn.setCursor(Qt.PointingHandCursor)
        open_csv_btn.setFont(QFont("DM Sans", 9))
        open_csv_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_PRIMARY}; "
            f"border: none; padding: 2px 0; text-decoration: underline; font-size: 9px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        open_csv_btn.clicked.connect(self._open_collected_data_folder)
        collect_row.addWidget(open_csv_btn)

        collect_row.addStretch()
        card_share_lay.addLayout(collect_row)
        tab4_lay.addWidget(card_share)

        # ── Gmail Account Card ──
        card_gmail, card_gmail_lay = self._settings_card(t("card_gmail"))
        self._gmail_card = card_gmail

        input_style = (
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 8px 12px; color: {config.COLOR_TEXT}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )

        # Email address
        email_row = QHBoxLayout()
        email_row.setSpacing(8)
        email_lbl = QLabel(t("gmail_address"))
        email_lbl.setFont(QFont("DM Sans", 10))
        email_lbl.setStyleSheet(label_style)
        email_lbl.setFixedWidth(110)
        email_row.addWidget(email_lbl)
        self._smtp_email_input = QLineEdit()
        self._smtp_email_input.setFont(QFont("DM Sans", 10))
        self._smtp_email_input.setPlaceholderText(t("gmail_placeholder"))
        self._smtp_email_input.setFixedHeight(32)
        self._smtp_email_input.setStyleSheet(input_style)
        email_row.addWidget(self._smtp_email_input, 1)
        card_gmail_lay.addLayout(email_row)

        # App password
        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)
        pw_lbl = QLabel(t("app_password"))
        pw_lbl.setFont(QFont("DM Sans", 10))
        pw_lbl.setStyleSheet(label_style)
        pw_lbl.setFixedWidth(110)
        pw_row.addWidget(pw_lbl)
        self._smtp_password_input = QLineEdit()
        self._smtp_password_input.setFont(QFont("DM Sans", 10))
        self._smtp_password_input.setPlaceholderText(t("app_password_placeholder"))
        self._smtp_password_input.setEchoMode(QLineEdit.Password)
        self._smtp_password_input.setFixedHeight(32)
        self._smtp_password_input.setStyleSheet(input_style)
        pw_row.addWidget(self._smtp_password_input, 1)
        card_gmail_lay.addLayout(pw_row)

        # Help link (small, subtle)
        help_btn = QPushButton(t("how_app_password"))
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.setFont(QFont("DM Sans", 9))
        help_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_PRIMARY}; "
            f"border: none; padding: 2px 0; text-align: left; text-decoration: underline; font-size: 9px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        help_btn.clicked.connect(self._show_gmail_help)
        card_gmail_lay.addWidget(help_btn)

        # Status label
        self._gmail_status_label = QLabel("")
        self._gmail_status_label.setFont(QFont("DM Sans", 9))
        self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        card_gmail_lay.addWidget(self._gmail_status_label)

        # Buttons row (compact, consistent sizing)
        gmail_btn_row = QHBoxLayout()
        gmail_btn_row.setSpacing(8)
        gmail_btn_row.setContentsMargins(0, 4, 0, 4)

        self._gmail_link_btn = QPushButton(t("save_and_test"))
        self._gmail_link_btn.setCursor(Qt.PointingHandCursor)
        self._gmail_link_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        self._gmail_link_btn.setFixedHeight(34)
        self._gmail_link_btn.setMaximumWidth(180)
        self._gmail_link_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 16px; font-size: 10px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        self._gmail_link_btn.clicked.connect(self._on_gmail_save_test)
        gmail_btn_row.addWidget(self._gmail_link_btn)

        self._gmail_unlink_btn = QPushButton(t("delete"))
        self._gmail_unlink_btn.setCursor(Qt.PointingHandCursor)
        self._gmail_unlink_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        self._gmail_unlink_btn.setFixedHeight(34)
        self._gmail_unlink_btn.setMaximumWidth(140)
        self._gmail_unlink_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: #ffffff; "
            f"border: none; border-radius: 6px; padding: 6px 16px; font-size: 10px; }}"
            f"QPushButton:pressed {{ background: #A93226; }}"
        )
        self._gmail_unlink_btn.clicked.connect(self._on_gmail_unlink)
        gmail_btn_row.addWidget(self._gmail_unlink_btn)

        gmail_btn_row.addStretch()
        card_gmail_lay.addLayout(gmail_btn_row)

        tab4_lay.addWidget(card_gmail)

        # ── Email Content Card ──
        card_email_content, card_email_lay = self._settings_card(t("card_email_content"))
        self._email_content_card = card_email_content

        # Subject field
        subj_row = QHBoxLayout()
        subj_row.setSpacing(10)
        subj_label = QLabel(t("email_subject_label"))
        subj_label.setFont(QFont("DM Sans", 13))
        subj_label.setStyleSheet(label_style)
        subj_row.addWidget(subj_label)
        self._email_subject_input = QLineEdit()
        self._email_subject_input.setFont(QFont("DM Sans", 13))
        self._email_subject_input.setPlaceholderText(t("email_subject_default"))
        self._email_subject_input.setMinimumHeight(40)
        self._email_subject_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 4px 10px; color: {config.COLOR_TEXT}; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        self._email_subject_input.editingFinished.connect(self._on_email_subject_changed)
        subj_row.addWidget(self._email_subject_input, stretch=1)
        card_email_lay.addLayout(subj_row)

        # Body text field
        body_label = QLabel(t("email_body_label"))
        body_label.setFont(QFont("DM Sans", 13))
        body_label.setStyleSheet(label_style)
        card_email_lay.addWidget(body_label)

        self._email_body_input = QTextEdit()
        self._email_body_input.setFont(QFont("DM Sans", 12))
        self._email_body_input.setMinimumHeight(100)
        self._email_body_input.setMaximumHeight(150)
        self._email_body_input.setPlaceholderText(t("email_body_default"))
        self._email_body_input.setStyleSheet(
            f"QTextEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 6px 10px; color: {config.COLOR_TEXT}; font-size: 12px; }}"
            f"QTextEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        card_email_lay.addWidget(self._email_body_input)

        # Save body button (since QTextEdit has no editingFinished)
        save_body_btn = QPushButton(t("save_text"))
        save_body_btn.setCursor(Qt.PointingHandCursor)
        save_body_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        save_body_btn.setMinimumHeight(36)
        save_body_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        save_body_btn.clicked.connect(self._on_email_body_changed)
        card_email_lay.addWidget(save_body_btn, alignment=Qt.AlignLeft)

        tab4_lay.addWidget(card_email_content)

        # ── Email Attachments Card ──
        card_attach, card_attach_lay = self._settings_card(t("card_email_attachments"))
        self._email_attach_card = card_attach

        self._email_send_strip_cb = ToggleSwitch(t("attach_strip"))
        self._email_send_strip_cb.setFont(QFont("DM Sans", 13))
        self._email_send_strip_cb.setStyleSheet(toggle_style)
        self._email_send_strip_cb.setChecked(True)
        self._email_send_strip_cb.toggled.connect(self._on_email_attach_changed)
        card_attach_lay.addWidget(self._email_send_strip_cb)

        self._share_single_strip_cb = ToggleSwitch(t("share_single_strip"))
        self._share_single_strip_cb.setFont(QFont("DM Sans", 13))
        self._share_single_strip_cb.setStyleSheet(toggle_style)
        self._share_single_strip_cb.setChecked(False)
        self._share_single_strip_cb.toggled.connect(self._on_email_attach_changed)
        card_attach_lay.addWidget(self._share_single_strip_cb)

        self._email_send_originals_cb = ToggleSwitch(t("attach_originals"))
        self._email_send_originals_cb.setFont(QFont("DM Sans", 13))
        self._email_send_originals_cb.setStyleSheet(toggle_style)
        self._email_send_originals_cb.setChecked(False)
        self._email_send_originals_cb.toggled.connect(self._on_email_attach_changed)
        card_attach_lay.addWidget(self._email_send_originals_cb)

        self._email_send_gif_cb = ToggleSwitch(t("attach_gif"))
        self._email_send_gif_cb.setFont(QFont("DM Sans", 13))
        self._email_send_gif_cb.setStyleSheet(toggle_style)
        self._email_send_gif_cb.setChecked(True)
        self._email_send_gif_cb.toggled.connect(self._on_email_attach_changed)
        card_attach_lay.addWidget(self._email_send_gif_cb)

        # Separator
        sep = QLabel("")
        sep.setFixedHeight(8)
        card_attach_lay.addWidget(sep)

        self._compress_sharing_cb = ToggleSwitch(t("compress_sharing"))
        self._compress_sharing_cb.setFont(QFont("DM Sans", 13))
        self._compress_sharing_cb.setStyleSheet(toggle_style)
        self._compress_sharing_cb.setChecked(False)
        self._compress_sharing_cb.toggled.connect(self._on_email_attach_changed)
        card_attach_lay.addWidget(self._compress_sharing_cb)

        compress_desc = QLabel(t("compress_sharing_desc"))
        compress_desc.setFont(QFont("DM Sans", 10))
        compress_desc.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        compress_desc.setWordWrap(True)
        card_attach_lay.addWidget(compress_desc)

        tab4_lay.addWidget(card_attach)

        tab4_lay.addStretch()
        # Verhuur-versie: tab Delen NIET toegevoegd aan stack (widgets bestaan
        # nog voor backwards compat met code-referenties elders).
        tab4_scroll.setParent(None)

        # ════════════════════════════════════════════
        # TAB 5: Betalingen
        # ════════════════════════════════════════════
        tab5_pay_scroll, tab5_pay_lay = self._settings_tab_scroll()

        # ── Betaalmethode keuze (radio) ──
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        method_card, method_card_lay = self._settings_card(t("payment_method_label"))
        self._payment_method_group = QButtonGroup(self)
        self._payment_method_radios = {}
        for value, label_key in [
            ("none",    "payment_method_none"),
            ("stripe",  "payment_method_stripe"),
            ("sumup",   "payment_method_sumup"),
            ("voucher", "payment_method_voucher"),
            ("custom",  "payment_method_custom"),
        ]:
            rb = QRadioButton(t(label_key))
            rb.setFont(QFont("DM Sans", 12))
            rb.setStyleSheet(f"color: {config.COLOR_TEXT}; padding: 4px;")
            rb.toggled.connect(lambda checked, v=value: self._on_payment_method_changed(v) if checked else None)
            self._payment_method_radios[value] = rb
            self._payment_method_group.addButton(rb)
            method_card_lay.addWidget(rb)
        tab5_pay_lay.addWidget(method_card)

        # ── Stripe Payment Card ──
        card_payment, card_payment_lay = self._settings_card(t("payment_title"))
        self._payment_card = card_payment

        self._payment_toggle = ToggleSwitch(t("payment_enable"))
        self._payment_toggle.setFont(QFont("DM Sans", 13))
        self._payment_toggle.setStyleSheet(toggle_style)
        self._payment_toggle.setChecked(False)
        self._payment_toggle.toggled.connect(self._on_payment_toggled)
        card_payment_lay.addWidget(self._payment_toggle)

        # Status label
        self._payment_status_label = QLabel("")
        self._payment_status_label.setFont(QFont("DM Sans", 11))
        self._payment_status_label.setWordWrap(True)
        card_payment_lay.addWidget(self._payment_status_label)

        # Account link button (small)
        account_btn = QPushButton(t("payment_setup_btn"))
        account_btn.setCursor(Qt.PointingHandCursor)
        account_btn.setFont(QFont("DM Sans", 9))
        account_btn.setMaximumWidth(250)
        account_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        account_btn.clicked.connect(lambda: __import__('webbrowser').open('https://bootharoo.com/account'))
        card_payment_lay.addWidget(account_btn)

        # Payment screen text
        pay_text_row = QHBoxLayout()
        pay_text_row.setSpacing(8)
        pay_text_lbl = QLabel(t("payment_screen_text_label"))
        pay_text_lbl.setFont(QFont("DM Sans", 10))
        pay_text_lbl.setStyleSheet(label_style)
        pay_text_lbl.setFixedWidth(110)
        pay_text_row.addWidget(pay_text_lbl)
        self._payment_screen_text = QLineEdit()
        self._payment_screen_text.setFont(QFont("DM Sans", 10))
        self._payment_screen_text.setPlaceholderText(t("payment_scan_default"))
        self._payment_screen_text.setFixedHeight(32)
        self._payment_screen_text.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 4px 8px; color: {config.COLOR_TEXT}; font-size: 10px; }}"
        )
        self._payment_screen_text.textChanged.connect(self._on_payment_text_changed)
        pay_text_row.addWidget(self._payment_screen_text, 1)
        card_payment_lay.addLayout(pay_text_row)

        # Payment idle background
        pay_bg_row = QHBoxLayout()
        pay_bg_row.setSpacing(8)
        pay_bg_lbl = QLabel(t("payment_bg_label"))
        pay_bg_lbl.setFont(QFont("DM Sans", 10))
        pay_bg_lbl.setStyleSheet(label_style)
        pay_bg_lbl.setFixedWidth(110)
        pay_bg_row.addWidget(pay_bg_lbl)
        self._payment_bg_label = QLabel(t("payment_bg_default"))
        self._payment_bg_label.setFont(QFont("DM Sans", 9))
        self._payment_bg_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        pay_bg_row.addWidget(self._payment_bg_label)
        pay_bg_btn = QPushButton(t("payment_bg_choose"))
        pay_bg_btn.setCursor(Qt.PointingHandCursor)
        pay_bg_btn.setFont(QFont("DM Sans", 9))
        pay_bg_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        pay_bg_btn.clicked.connect(self._on_payment_bg_change)
        pay_bg_row.addWidget(pay_bg_btn)
        pay_bg_row.addStretch()
        card_payment_lay.addLayout(pay_bg_row)

        tab5_pay_lay.addWidget(card_payment)

        # ── Clixibo Betaalterminal Card ──
        card_terminal, card_terminal_lay = self._settings_card(t("clixibo_payment_terminal"))
        self._sumup_card = card_terminal

        sumup_row = QHBoxLayout()
        sumup_row.setSpacing(8)
        self._sumup_toggle = ToggleSwitch(t("option_enable_payment_terminal"))
        self._sumup_toggle.setFont(QFont("DM Sans", 11))
        self._sumup_toggle.setStyleSheet(toggle_style)
        self._sumup_toggle.setChecked(getattr(self.active_event, 'sumup_enabled', False) if self.active_event else False)
        self._sumup_toggle.stateChanged.connect(self._on_sumup_toggled)
        sumup_row.addWidget(self._sumup_toggle)
        sumup_config_btn = QPushButton(t("btn_configure"))
        sumup_config_btn.setCursor(Qt.PointingHandCursor)
        sumup_config_btn.setFont(QFont("DM Sans", 9))
        sumup_config_btn.setFixedHeight(26)
        sumup_config_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 4px; padding: 2px 10px; font-size: 9px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        sumup_config_btn.clicked.connect(self._open_sumup_config)
        sumup_row.addWidget(sumup_config_btn)
        self._sumup_status_label = QLabel("")
        self._sumup_status_label.setFont(QFont("DM Sans", 9))
        self._sumup_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        sumup_row.addWidget(self._sumup_status_label)
        sumup_row.addStretch()
        card_terminal_lay.addLayout(sumup_row)
        self._update_sumup_status()

        tab5_pay_lay.addWidget(card_terminal)

        # ── Voucher Card ──
        # Twee aparte UI-states:
        #   STATE A "config":  geen codes nog -> aantal/lengte/charset + Genereer
        #   STATE B "result":  codes aanwezig -> stats + Bekijk/Exporteer/Reset
        card_voucher, card_voucher_lay = self._settings_card(t("voucher_card_title"))
        self._voucher_card = card_voucher

        label_style_local = f"color: {config.COLOR_TEXT};"
        primary_btn_style = (
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        secondary_btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        danger_btn_style = (
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: #ffffff; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #A93226; }}"
        )

        # ── STATE A: configuratie ─────────────────────────────────
        self._voucher_state_config = QWidget()
        config_lay = QVBoxLayout(self._voucher_state_config)
        config_lay.setContentsMargins(0, 0, 0, 0)
        config_lay.setSpacing(8)

        # Aantal vouchers — slimme +/- met preset-stappen
        count_row = QHBoxLayout()
        count_lbl = QLabel(t("voucher_count_label"))
        count_lbl.setFont(QFont("DM Sans", 11))
        count_lbl.setStyleSheet(label_style_local)
        count_lbl.setFixedWidth(160)
        count_row.addWidget(count_lbl)
        self._voucher_count_steps = [10, 50, 100, 200, 500, 1000, 5000, 10000]
        self._voucher_count_spin = self._make_step_spin(
            self._voucher_count_steps, default_idx=0, suffix=""
        )
        count_row.addWidget(self._voucher_count_spin)
        count_row.addStretch()
        config_lay.addLayout(count_row)

        # Aantal tekens — gewoon 1-bij-1
        midlen_row = QHBoxLayout()
        midlen_lbl = QLabel(t("voucher_middle_length"))
        midlen_lbl.setFont(QFont("DM Sans", 11))
        midlen_lbl.setStyleSheet(label_style_local)
        midlen_lbl.setFixedWidth(160)
        midlen_row.addWidget(midlen_lbl)
        self._voucher_midlen_spin = self._make_touch_spin(2, 12, 6)
        midlen_row.addWidget(self._voucher_midlen_spin)
        midlen_row.addStretch()
        config_lay.addLayout(midlen_row)

        # Type tekens — 3 radio buttons onder elkaar
        charset_row = QHBoxLayout()
        charset_lbl = QLabel(t("voucher_charset"))
        charset_lbl.setFont(QFont("DM Sans", 11))
        charset_lbl.setStyleSheet(label_style_local)
        charset_lbl.setFixedWidth(160)
        charset_lbl.setAlignment(Qt.AlignTop)
        charset_row.addWidget(charset_lbl)

        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        radio_col = QVBoxLayout()
        radio_col.setSpacing(4)
        radio_style = (
            f"QRadioButton {{ color: {config.COLOR_TEXT}; font-size: 12px; padding: 4px; }}"
            f"QRadioButton::indicator {{ width: 18px; height: 18px; }}"
        )
        self._voucher_charset_group = QButtonGroup(self)
        self._voucher_charset_radios = {}
        for value, key in [
            ("alphanum", "voucher_charset_alphanum"),
            ("letters",  "voucher_charset_letters"),
            ("digits",   "voucher_charset_digits"),
        ]:
            rb = QRadioButton(t(key))
            rb.setFont(QFont("DM Sans", 11))
            rb.setStyleSheet(radio_style)
            self._voucher_charset_radios[value] = rb
            self._voucher_charset_group.addButton(rb)
            radio_col.addWidget(rb)
        # Default selecteer "alphanum"
        self._voucher_charset_radios["alphanum"].setChecked(True)
        charset_row.addLayout(radio_col, 1)
        config_lay.addLayout(charset_row)

        # Genereer-knop
        gen_row = QHBoxLayout()
        gen_btn = QPushButton(t("voucher_generate"))
        gen_btn.setCursor(Qt.PointingHandCursor)
        gen_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        gen_btn.setMinimumHeight(40)
        gen_btn.setStyleSheet(primary_btn_style)
        gen_btn.clicked.connect(self._on_voucher_generate)
        gen_row.addWidget(gen_btn)
        gen_row.addStretch()
        config_lay.addLayout(gen_row)

        card_voucher_lay.addWidget(self._voucher_state_config)

        # ── STATE B: resultaat ───────────────────────────────────
        self._voucher_state_result = QWidget()
        result_lay = QVBoxLayout(self._voucher_state_result)
        result_lay.setContentsMargins(0, 0, 0, 0)
        result_lay.setSpacing(8)

        title_lbl = QLabel(t("voucher_generated_title"))
        title_lbl.setFont(QFont("DM Sans", 13, QFont.Bold))
        title_lbl.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        result_lay.addWidget(title_lbl)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(20)
        self._voucher_total_label = QLabel("")
        self._voucher_total_label.setFont(QFont("DM Sans", 11))
        self._voucher_total_label.setStyleSheet(label_style_local)
        stats_row.addWidget(self._voucher_total_label)
        self._voucher_used_label = QLabel("")
        self._voucher_used_label.setFont(QFont("DM Sans", 11))
        self._voucher_used_label.setStyleSheet(label_style_local)
        stats_row.addWidget(self._voucher_used_label)
        self._voucher_avail_label = QLabel("")
        self._voucher_avail_label.setFont(QFont("DM Sans", 11))
        self._voucher_avail_label.setStyleSheet(label_style_local)
        stats_row.addWidget(self._voucher_avail_label)
        stats_row.addStretch()
        result_lay.addLayout(stats_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        view_btn = QPushButton(t("voucher_view"))
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setFont(QFont("DM Sans", 10))
        view_btn.setMinimumHeight(36)
        view_btn.setStyleSheet(secondary_btn_style)
        view_btn.clicked.connect(self._on_voucher_view)
        action_row.addWidget(view_btn)

        export_btn = QPushButton(t("voucher_export_btn"))
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setFont(QFont("DM Sans", 10))
        export_btn.setMinimumHeight(36)
        export_btn.setStyleSheet(secondary_btn_style)
        export_btn.clicked.connect(self._on_voucher_export_choose)
        action_row.addWidget(export_btn)

        reset_btn = QPushButton(t("voucher_clear_all"))
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFont(QFont("DM Sans", 10))
        reset_btn.setMinimumHeight(36)
        reset_btn.setStyleSheet(danger_btn_style)
        reset_btn.clicked.connect(self._on_voucher_clear_all)
        action_row.addWidget(reset_btn)

        action_row.addStretch()
        result_lay.addLayout(action_row)

        card_voucher_lay.addWidget(self._voucher_state_result)

        # Initial state hidden — _refresh_voucher_ui() bepaalt welke
        self._voucher_state_config.setVisible(False)
        self._voucher_state_result.setVisible(False)

        tab5_pay_lay.addWidget(card_voucher)

        # ── Custom flow Card (verborgen functie) ──
        card_custom, card_custom_lay = self._settings_card(t("custom_card_title"))
        self._custom_card = card_custom
        card_custom_lay.setSpacing(10)

        # Section: keuzescherm
        choice_section = QLabel(t("custom_choice_section"))
        choice_section.setFont(QFont("DM Sans", 12, QFont.Bold))
        choice_section.setStyleSheet(f"color: {config.COLOR_PRIMARY};")
        card_custom_lay.addWidget(choice_section)

        # Keuzescherm achtergrond rij
        choice_bg_row = QHBoxLayout()
        choice_bg_lbl = QLabel(t("custom_bg_label"))
        choice_bg_lbl.setFont(QFont("DM Sans", 11))
        choice_bg_lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
        choice_bg_lbl.setFixedWidth(120)
        choice_bg_row.addWidget(choice_bg_lbl)
        self._custom_choice_bg_label = QLabel(t("custom_bg_default"))
        self._custom_choice_bg_label.setFont(QFont("DM Sans", 10))
        self._custom_choice_bg_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        choice_bg_row.addWidget(self._custom_choice_bg_label, 1)
        choice_bg_btn = QPushButton(t("custom_bg_choose"))
        choice_bg_btn.setCursor(Qt.PointingHandCursor)
        choice_bg_btn.setFont(QFont("DM Sans", 9))
        choice_bg_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        choice_bg_btn.clicked.connect(lambda: self._on_custom_bg_choose("choice"))
        choice_bg_row.addWidget(choice_bg_btn)
        choice_bg_clear_btn = QPushButton(t("custom_bg_clear"))
        choice_bg_clear_btn.setCursor(Qt.PointingHandCursor)
        choice_bg_clear_btn.setFont(QFont("DM Sans", 9))
        choice_bg_clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
        )
        choice_bg_clear_btn.clicked.connect(lambda: self._on_custom_bg_clear("choice"))
        choice_bg_row.addWidget(choice_bg_clear_btn)
        card_custom_lay.addLayout(choice_bg_row)

        # Keuzescherm timeout rij
        choice_to_row = QHBoxLayout()
        choice_to_lbl = QLabel(t("custom_timeout_label"))
        choice_to_lbl.setFont(QFont("DM Sans", 11))
        choice_to_lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
        choice_to_lbl.setFixedWidth(120)
        choice_to_row.addWidget(choice_to_lbl)
        self._custom_choice_timeout_spin = self._make_touch_spin(
            5, 300, 30, suffix="s",
            on_change=self._on_custom_choice_timeout_changed,
        )
        choice_to_row.addWidget(self._custom_choice_timeout_spin)
        choice_to_row.addStretch()
        card_custom_lay.addLayout(choice_to_row)

        card_custom_lay.addSpacing(8)

        # Section: betaalscherm
        pay_section = QLabel(t("custom_payment_section"))
        pay_section.setFont(QFont("DM Sans", 12, QFont.Bold))
        pay_section.setStyleSheet(f"color: {config.COLOR_PRIMARY};")
        card_custom_lay.addWidget(pay_section)

        # Betaalscherm achtergrond rij
        pay_bg_row = QHBoxLayout()
        pay_bg_lbl = QLabel(t("custom_bg_label"))
        pay_bg_lbl.setFont(QFont("DM Sans", 11))
        pay_bg_lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
        pay_bg_lbl.setFixedWidth(120)
        pay_bg_row.addWidget(pay_bg_lbl)
        self._custom_payment_bg_label = QLabel(t("custom_bg_default"))
        self._custom_payment_bg_label.setFont(QFont("DM Sans", 10))
        self._custom_payment_bg_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        pay_bg_row.addWidget(self._custom_payment_bg_label, 1)
        pay_bg_btn = QPushButton(t("custom_bg_choose"))
        pay_bg_btn.setCursor(Qt.PointingHandCursor)
        pay_bg_btn.setFont(QFont("DM Sans", 9))
        pay_bg_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        pay_bg_btn.clicked.connect(lambda: self._on_custom_bg_choose("payment"))
        pay_bg_row.addWidget(pay_bg_btn)
        pay_bg_clear_btn = QPushButton(t("custom_bg_clear"))
        pay_bg_clear_btn.setCursor(Qt.PointingHandCursor)
        pay_bg_clear_btn.setFont(QFont("DM Sans", 9))
        pay_bg_clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 5px; padding: 4px 10px; font-size: 9px; }}"
        )
        pay_bg_clear_btn.clicked.connect(lambda: self._on_custom_bg_clear("payment"))
        pay_bg_row.addWidget(pay_bg_clear_btn)
        card_custom_lay.addLayout(pay_bg_row)

        # Betaalscherm timeout rij
        pay_to_row = QHBoxLayout()
        pay_to_lbl = QLabel(t("custom_timeout_label"))
        pay_to_lbl.setFont(QFont("DM Sans", 11))
        pay_to_lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
        pay_to_lbl.setFixedWidth(120)
        pay_to_row.addWidget(pay_to_lbl)
        self._custom_payment_timeout_spin = self._make_touch_spin(
            10, 600, 120, suffix="s",
            on_change=self._on_custom_payment_timeout_changed,
        )
        pay_to_row.addWidget(self._custom_payment_timeout_spin)
        pay_to_row.addStretch()
        card_custom_lay.addLayout(pay_to_row)

        tab5_pay_lay.addWidget(card_custom)
        self._custom_card.setVisible(False)

        tab5_pay_lay.addStretch()
        # Verhuur-versie: tab Betalingen NIET toegevoegd aan stack.
        tab5_pay_scroll.setParent(None)

        # ════════════════════════════════════════════
        # TAB 6: Geavanceerd
        # ════════════════════════════════════════════
        tab5_scroll, tab5_lay = self._settings_tab_scroll()
        self._tab5_lay = tab5_lay  # voor verplaatsen Gekoppeld-kaart

        # ── Card: Serienummer (booth-identificatie) ──────────────────
        card_serial, card_serial_lay = self._settings_card("Serienummer booth")
        serial_intro = QLabel(
            "Het unieke serienummer van deze photobooth (letters en cijfers). "
            "Wordt meegestuurd met de cloud-logs zodat zichtbaar is welke "
            "booth bij welke klant draait."
        )
        serial_intro.setFont(QFont("DM Sans", 11))
        serial_intro.setWordWrap(True)
        serial_intro.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        card_serial_lay.addWidget(serial_intro)
        serial_row = QHBoxLayout()
        serial_row.setSpacing(10)
        serial_lbl = QLabel("Serienummer:")
        serial_lbl.setFont(QFont("DM Sans", 13, QFont.Bold))
        serial_lbl.setStyleSheet(label_style)
        serial_row.addWidget(serial_lbl)
        self._serial_input = QLineEdit()
        self._serial_input.setFont(QFont("DM Sans", 14))
        self._serial_input.setMinimumHeight(44)
        self._serial_input.setMaxLength(32)
        self._serial_input.setPlaceholderText("bv. BOOTH-001")
        self._serial_input.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; color: {config.COLOR_TEXT}; "
            f"border: 2px solid {config.COLOR_BORDER}; border-radius: 6px; "
            f"padding: 6px 12px; font-size: 14px; }}"
        )
        self._serial_input.editingFinished.connect(self._on_serial_changed)
        serial_row.addWidget(self._serial_input, stretch=1)
        card_serial_lay.addLayout(serial_row)
        tab5_lay.addWidget(card_serial)

        # ── Card: Software-updates ───────────────────────────────────
        card_upd, card_upd_lay = self._settings_card("Software-updates")
        upd_cur = QLabel(f"Huidige versie: {config.VERSION}")
        upd_cur.setFont(QFont("DM Sans", 12))
        upd_cur.setStyleSheet(f"color: {config.COLOR_TEXT};")
        card_upd_lay.addWidget(upd_cur)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setFont(QFont("DM Sans", 12))
        self._update_status_lbl.setWordWrap(True)
        self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        card_upd_lay.addWidget(self._update_status_lbl)

        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 100)
        self._update_progress.setVisible(False)
        self._update_progress.setFixedHeight(20)
        card_upd_lay.addWidget(self._update_progress)

        upd_btn_row = QHBoxLayout()
        upd_btn_row.setSpacing(10)
        upd_btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 8px; padding: 8px 18px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
            f"QPushButton:disabled {{ background: {config.COLOR_BORDER}; "
            f"color: {config.COLOR_TEXT_DIM}; }}"
        )
        self._update_check_btn = QPushButton("Controleer op updates")
        self._update_check_btn.setCursor(Qt.PointingHandCursor)
        self._update_check_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._update_check_btn.setFixedHeight(40)
        self._update_check_btn.setStyleSheet(upd_btn_style)
        self._update_check_btn.clicked.connect(self._on_check_updates)
        upd_btn_row.addWidget(self._update_check_btn)

        self._update_install_btn = QPushButton("Nu updaten")
        self._update_install_btn.setCursor(Qt.PointingHandCursor)
        self._update_install_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._update_install_btn.setFixedHeight(40)
        self._update_install_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 8px; padding: 8px 18px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        self._update_install_btn.clicked.connect(self._on_do_update)
        self._update_install_btn.setVisible(False)
        upd_btn_row.addWidget(self._update_install_btn)
        upd_btn_row.addStretch()
        card_upd_lay.addLayout(upd_btn_row)
        self._pending_update = None  # dict van de laatste check
        tab5_lay.addWidget(card_upd)

        # Card: Language
        card_lang, card_lang_lay = self._settings_card(t("language_label").rstrip(":"))
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        lang_label = QLabel(t("language_label"))
        lang_label.setFont(QFont("DM Sans", 13))
        lang_label.setStyleSheet(label_style)
        lang_row.addWidget(lang_label)
        self._language_combo = QComboBox()
        self._language_combo.setFont(QFont("DM Sans", 13))
        self._language_combo.setMinimumHeight(44)
        self._language_combo.setStyleSheet(combo_style)
        lang_options = [
            ("nl", "Nederlands"),
            ("en", "English"),
            ("de", "Deutsch"),
            ("fr", "Fran\u00e7ais"),
            ("es", "Espa\u00f1ol"),
            ("it", "Italiano"),
        ]
        for code, name in lang_options:
            self._language_combo.addItem(name, code)
        # Set current language
        current_lang = get_language()
        for i, (code, _) in enumerate(lang_options):
            if code == current_lang:
                self._language_combo.setCurrentIndex(i)
                break
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._language_combo)
        lang_row.addStretch()
        card_lang_lay.addLayout(lang_row)
        tab5_lay.addWidget(card_lang)

        # ── Card: DNP printer-instellingen (verborgen achter knop) ──
        # Capture-UI is bewust verborgen achter een dialog zodat klanten er
        # niet per ongeluk bij komen. Eenmalig per PC instellen.
        card_dnp_btn, card_dnp_btn_lay = self._settings_card("DNP printer-instellingen")
        dnp_btn_intro = QLabel(
            "Eenmalig per PC instellen — capture de DNP driver-instellingen "
            "voor de 3 paper/cut configuraties."
        )
        dnp_btn_intro.setFont(QFont("DM Sans", 11))
        dnp_btn_intro.setWordWrap(True)
        dnp_btn_intro.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        card_dnp_btn_lay.addWidget(dnp_btn_intro)

        dnp_open_btn = QPushButton("Open DNP printer-instellingen…")
        dnp_open_btn.setCursor(Qt.PointingHandCursor)
        dnp_open_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        dnp_open_btn.setFixedHeight(40)
        dnp_open_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 8px; padding: 8px 22px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        dnp_open_btn.clicked.connect(self._open_dnp_profiles_dialog)
        card_dnp_btn_lay.addWidget(dnp_open_btn)
        tab5_lay.addWidget(card_dnp_btn)

        # ── Card: Cloud-uploads (queue status + force retry) ──
        card_cloud, card_cloud_lay = self._settings_card("Cloud-uploads")
        cloud_intro = QLabel(
            "Foto's worden in de achtergrond naar de cloud geüpload. Bij "
            "wifi-uitval blijft de queue intact en probeert hij elke minuut. "
            "Hieronder per event de stand van zaken."
        )
        cloud_intro.setFont(QFont("DM Sans", 11))
        cloud_intro.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        cloud_intro.setWordWrap(True)
        card_cloud_lay.addWidget(cloud_intro)

        # Container waar de per-booking lijst in komt — wordt elke 3s
        # ververst zolang het tabblad open staat.
        self._cloud_uploads_list = QWidget()
        self._cloud_uploads_list.setStyleSheet("background: transparent;")
        cloud_list_lay = QVBoxLayout(self._cloud_uploads_list)
        cloud_list_lay.setContentsMargins(0, 8, 0, 8)
        cloud_list_lay.setSpacing(8)
        card_cloud_lay.addWidget(self._cloud_uploads_list)

        # Actie-knoppen
        cloud_btn_row = QHBoxLayout()
        cloud_btn_row.setSpacing(10)
        retry_all_btn = QPushButton("🔁  Probeer alles opnieuw")
        retry_all_btn.setMinimumHeight(40)
        retry_all_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        retry_all_btn.setCursor(Qt.PointingHandCursor)
        retry_all_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 8px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        retry_all_btn.clicked.connect(self._on_cloud_retry_all_clicked)
        cloud_btn_row.addWidget(retry_all_btn)

        clear_done_btn = QPushButton("🗑  Voltooide opruimen")
        clear_done_btn.setMinimumHeight(40)
        clear_done_btn.setFont(QFont("DM Sans", 12))
        clear_done_btn.setCursor(Qt.PointingHandCursor)
        clear_done_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; "
            f"color: white; border: none; border-radius: 8px; padding: 8px 16px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        clear_done_btn.clicked.connect(self._on_cloud_clear_done_clicked)
        cloud_btn_row.addWidget(clear_done_btn)
        cloud_btn_row.addStretch()
        card_cloud_lay.addLayout(cloud_btn_row)

        tab5_lay.addWidget(card_cloud)

        # Auto-refresh timer voor de cloud-status (alleen wanneer tabblad zichtbaar)
        self._cloud_refresh_timer = QTimer(self)
        self._cloud_refresh_timer.setInterval(3000)
        self._cloud_refresh_timer.timeout.connect(self._refresh_cloud_uploads_ui)
        self._cloud_refresh_timer.start()
        # Eerste refresh direct
        QTimer.singleShot(100, self._refresh_cloud_uploads_ui)

        # Printer-modus (4x3 / 4x6 / 3strips) staat in de Layout-tab boven
        # de template-grid (niet meer hier).

        # ── Card: Booth-modus (Standalone vs Gekoppeld) ──
        card_bmode, card_bmode_lay = self._settings_card("Modus")
        bmode_row = QHBoxLayout()
        bmode_row.setSpacing(20)
        from PyQt5.QtWidgets import QButtonGroup as _BG2
        self._booth_mode_group = _BG2(self)
        self._booth_mode_standalone_radio = QRadioButton("Standalone (huidige flow)")
        self._booth_mode_standalone_radio.setFont(QFont("DM Sans", 13))
        self._booth_mode_linked_radio = QRadioButton("Gekoppeld (event)")
        self._booth_mode_linked_radio.setFont(QFont("DM Sans", 13))
        self._booth_mode_group.addButton(self._booth_mode_standalone_radio)
        self._booth_mode_group.addButton(self._booth_mode_linked_radio)
        self._booth_mode_standalone_radio.setChecked(True)
        self._booth_mode_standalone_radio.toggled.connect(self._on_booth_mode_changed)
        self._booth_mode_linked_radio.toggled.connect(self._on_booth_mode_changed)
        bmode_row.addWidget(self._booth_mode_standalone_radio)
        bmode_row.addWidget(self._booth_mode_linked_radio)
        bmode_row.addStretch()
        card_bmode_lay.addLayout(bmode_row)
        tab5_lay.addWidget(card_bmode)
        # Verhuur is ALTIJD Linked-modus — modus-kaart verbergen.
        # Widgets blijven bestaan voor backwards compat met handlers.
        card_bmode.setVisible(False)

        # ── Card: Gekoppeld event (alleen zichtbaar in Linked-modus) ──
        self._card_linked, card_linked_lay = self._settings_card("Gekoppeld event")

        # ── No-wifi paneel — getoond bovenin als er geen internet is ──
        self._no_wifi_widget = QWidget()
        nw_lay = QVBoxLayout(self._no_wifi_widget)
        nw_lay.setSpacing(14)
        nw_lay.setContentsMargins(8, 12, 8, 12)
        nw_title = QLabel("👋  Welkom!")
        nw_title.setFont(QFont("DM Sans", 18, QFont.Bold))
        nw_title.setAlignment(Qt.AlignCenter)
        nw_title.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        nw_lay.addWidget(nw_title)
        nw_msg = QLabel("Verbind eerst je tablet met WiFi voordat\nje een event kunt koppelen.")
        nw_msg.setFont(QFont("DM Sans", 13))
        nw_msg.setAlignment(Qt.AlignCenter)
        nw_msg.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        nw_msg.setWordWrap(True)
        nw_lay.addWidget(nw_msg)
        nw_arrow = QLabel("↘")
        nw_arrow.setFont(QFont("DM Sans", 64, QFont.Bold))
        nw_arrow.setAlignment(Qt.AlignRight)
        nw_arrow.setStyleSheet(f"color: {config.COLOR_PRIMARY}; background: transparent;")
        nw_lay.addWidget(nw_arrow)
        nw_hint = QLabel("Klik rechtsonder op het WiFi-icoon van Windows")
        nw_hint.setFont(QFont("DM Sans", 11))
        nw_hint.setAlignment(Qt.AlignRight)
        nw_hint.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        nw_hint.setWordWrap(True)
        nw_lay.addWidget(nw_hint)
        nw_polling = QLabel("● Wacht op verbinding…")
        nw_polling.setFont(QFont("DM Sans", 10))
        nw_polling.setAlignment(Qt.AlignCenter)
        nw_polling.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent; margin-top: 8px;")
        nw_lay.addWidget(nw_polling)
        card_linked_lay.addWidget(self._no_wifi_widget)
        self._no_wifi_widget.setVisible(False)  # alleen tonen als geen wifi

        # ── Normale event-content (verborgen tijdens no-wifi) ──
        self._linked_content_widget = QWidget()
        content_lay = QVBoxLayout(self._linked_content_widget)
        content_lay.setSpacing(10)
        content_lay.setContentsMargins(0, 0, 0, 0)
        self._linked_status_label = QLabel("Geen event gekoppeld")
        self._linked_status_label.setFont(QFont("DM Sans", 13))
        self._linked_status_label.setStyleSheet(label_style)
        self._linked_status_label.setWordWrap(True)
        content_lay.addWidget(self._linked_status_label)
        card_linked_lay.addWidget(self._linked_content_widget)

        # Knoppen-rij
        linked_btn_row = QHBoxLayout()
        linked_btn_row.setSpacing(10)
        self._btn_couple_event = QPushButton("📷  Koppel event")
        self._btn_couple_event.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._btn_couple_event.setCursor(Qt.PointingHandCursor)
        self._btn_couple_event.setMinimumHeight(40)
        self._btn_couple_event.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
            f"QPushButton:disabled {{ background: {config.COLOR_BORDER}; color: {config.COLOR_TEXT_DIM}; }}"
        )
        self._btn_couple_event.clicked.connect(self._on_couple_event_clicked)
        linked_btn_row.addWidget(self._btn_couple_event)

        self._btn_refresh_event = QPushButton("🔄  Ververs")
        self._btn_refresh_event.setFont(QFont("DM Sans", 12))
        self._btn_refresh_event.setCursor(Qt.PointingHandCursor)
        self._btn_refresh_event.setMinimumHeight(40)
        self._btn_refresh_event.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: white; "
            f"border: none; border-radius: 8px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._btn_refresh_event.clicked.connect(self._on_refresh_event_clicked)
        self._btn_refresh_event.setVisible(False)
        linked_btn_row.addWidget(self._btn_refresh_event)

        self._btn_unlink_event = QPushButton("Loskoppelen")
        self._btn_unlink_event.setFont(QFont("DM Sans", 12))
        self._btn_unlink_event.setCursor(Qt.PointingHandCursor)
        self._btn_unlink_event.setMinimumHeight(40)
        self._btn_unlink_event.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 8px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ color: {config.COLOR_DANGER}; border-color: {config.COLOR_DANGER}; }}"
        )
        self._btn_unlink_event.clicked.connect(self._on_unlink_event_clicked)
        self._btn_unlink_event.setVisible(False)
        linked_btn_row.addWidget(self._btn_unlink_event)
        linked_btn_row.addStretch()
        content_lay.addLayout(linked_btn_row)

        # Foto-aantal selectie verborgen — de layout/template bepaalt aantal foto's.
        # Widget bestaat nog voor backwards compat van handlers/event-veld.
        self._linked_count_row = QHBoxLayout()
        self._linked_count_label = QLabel("Foto's per strip:")
        self._linked_count_label.setFont(QFont("DM Sans", 12))
        self._linked_count_label.setStyleSheet(label_style)
        self._linked_count_row.addWidget(self._linked_count_label)
        self._linked_count_spin = self._make_touch_spin(
            1, 4, 2, on_change=self._on_linked_count_changed,
        )
        self._linked_count_row.addWidget(self._linked_count_spin)
        self._linked_count_row.addStretch()
        content_lay.addLayout(self._linked_count_row)
        self._linked_count_label.setVisible(False)
        self._linked_count_spin.setVisible(False)

        # Upload-voortgang — alleen tonen als er al iets in queue zit
        self._linked_progress_label = QLabel("")
        self._linked_progress_label.setFont(QFont("DM Sans", 11))
        self._linked_progress_label.setStyleSheet(dim_label_style)
        self._linked_progress_label.setWordWrap(True)
        content_lay.addWidget(self._linked_progress_label)
        self._linked_progress_label.setVisible(False)

        tab5_lay.addWidget(self._card_linked)
        self._card_linked.setVisible(False)  # initieel verborgen (Standalone default)

        # Verhuur: camera-instellingen verplaatst uit Camera-tab naar hier
        if hasattr(self, '_card_cam_mode'):
            self._card_cam_mode.setParent(None)
            tab5_lay.addWidget(self._card_cam_mode)
        if hasattr(self, '_card_cam_set'):
            self._card_cam_set.setParent(None)
            tab5_lay.addWidget(self._card_cam_set)

        # Card: Lock icon
        card_lock, card_lock_lay = self._settings_card(t("card_lock"))
        lock_row = QHBoxLayout()
        lock_row.setSpacing(10)
        size_label = QLabel(t("lock_size"))
        size_label.setFont(QFont("DM Sans", 13))
        size_label.setStyleSheet(label_style)
        lock_row.addWidget(size_label)
        self._lock_size_spin = self._make_touch_spin(20, 120, 60, "px", on_change=self._on_lock_size_changed)
        lock_row.addWidget(self._lock_size_spin)
        lock_row.addStretch()
        card_lock_lay.addLayout(lock_row)

        pin_row = QHBoxLayout()
        pin_row.setSpacing(10)
        pin_label = QLabel(t("pin_code"))
        pin_label.setFont(QFont("DM Sans", 13))
        pin_label.setStyleSheet(label_style)
        pin_row.addWidget(pin_label)
        self._pin_button = QPushButton(t("no_pin"))
        self._pin_button.setCursor(Qt.PointingHandCursor)
        self._pin_button.setFont(QFont("DM Sans", 14))
        self._pin_button.setMaximumWidth(250)
        self._pin_button.setMinimumHeight(44)
        self._pin_button.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 4px 14px; color: {config.COLOR_TEXT}; font-size: 14px; "
            f"text-align: left; }}"
            f"QPushButton:hover {{ border-color: {config.COLOR_PRIMARY}; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_ACCENT}; }}"
        )
        self._pin_button.clicked.connect(self._on_pin_button_clicked)
        pin_row.addWidget(self._pin_button)

        pin_clear_btn = QPushButton(t("clear_pin"))
        pin_clear_btn.setCursor(Qt.PointingHandCursor)
        pin_clear_btn.setFont(QFont("DM Sans", 12))
        pin_clear_btn.setMinimumHeight(44)
        pin_clear_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_DANGER}; color: #ffffff; "
            f"border: none; border-radius: 6px; padding: 4px 14px; font-size: 12px; }}"
            f"QPushButton:pressed {{ background: #A93226; }}"
        )
        pin_clear_btn.clicked.connect(self._on_pin_clear)
        pin_row.addWidget(pin_clear_btn)

        pin_row.addStretch()
        card_lock_lay.addLayout(pin_row)
        tab5_lay.addWidget(card_lock)
        # Verhuur: lock-icon-size + PIN-config verbergen (PIN is hardcoded 1350).
        card_lock.setVisible(False)

        # Card: Account / Abonnement
        card_account, card_account_lay = self._settings_card(t("card_license"))

        # Status indicator (green dot + text or red dot + text)
        self._account_status_label = QLabel("")
        self._account_status_label.setFont(QFont("DM Sans", 14, QFont.Bold))
        self._account_status_label.setStyleSheet(label_style)
        card_account_lay.addWidget(self._account_status_label)

        self._account_email_label = QLabel("")
        self._account_email_label.setFont(QFont("DM Sans", 13))
        self._account_email_label.setStyleSheet(dim_label_style)
        card_account_lay.addWidget(self._account_email_label)

        self._account_plan_label = QLabel("")
        self._account_plan_label.setFont(QFont("DM Sans", 13))
        self._account_plan_label.setStyleSheet(dim_label_style)
        card_account_lay.addWidget(self._account_plan_label)

        self._account_expiry_label = QLabel("")
        self._account_expiry_label.setFont(QFont("DM Sans", 12))
        self._account_expiry_label.setStyleSheet(dim_label_style)
        card_account_lay.addWidget(self._account_expiry_label)

        self._account_key_label = QLabel("")
        self._account_key_label.setFont(QFont("DM Sans", 11))
        self._account_key_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; font-family: monospace; background: transparent;")
        card_account_lay.addWidget(self._account_key_label)

        account_btn_row = QHBoxLayout()
        account_btn_row.setSpacing(10)

        self._account_activate_btn = QPushButton(t("activate"))
        self._account_activate_btn.setCursor(Qt.PointingHandCursor)
        self._account_activate_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._account_activate_btn.setMinimumHeight(44)
        self._account_activate_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px 20px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        self._account_activate_btn.clicked.connect(lambda: self._show_login())
        account_btn_row.addWidget(self._account_activate_btn)

        self._account_logout_btn = QPushButton(t("logout"))
        self._account_logout_btn.setCursor(Qt.PointingHandCursor)
        self._account_logout_btn.setFont(QFont("DM Sans", 9))
        self._account_logout_btn.setMinimumHeight(0)
        self._account_logout_btn.setMaximumWidth(120)
        self._account_logout_btn.setStyleSheet(
            "QPushButton { background: #999999; color: #ffffff; "
            "border: none; border-radius: 6px; padding: 4px 12px; font-size: 9px; }"
            "QPushButton:hover { background: #777777; }"
        )
        self._account_logout_btn.clicked.connect(self._on_logout)
        account_btn_row.addWidget(self._account_logout_btn)

        account_btn_row.addStretch()
        card_account_lay.addLayout(account_btn_row)
        tab5_lay.addWidget(card_account)
        # Verhuur: licentie/account-kaart verbergen (geen login meer).
        card_account.setVisible(False)

        # ── Card: Backend (Hippe / Verhuurophalen) — helemaal onderaan ──
        card_brand, card_brand_lay = self._settings_card("Backend")
        brand_intro = QLabel(
            "Bepaalt met welk boekingssysteem deze booth koppelt. "
            "Hippe = Fotoboothje (DNP QW410). Verhuurophalen = "
            "hippephotoboothhuren.nl (HiTi P525L, 1200×1800 dubbele strip, "
            "geen printer-statusmeldingen)."
        )
        brand_intro.setFont(QFont("DM Sans", 11))
        brand_intro.setWordWrap(True)
        brand_intro.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        card_brand_lay.addWidget(brand_intro)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(14)
        self._brand_hippe_radio = QRadioButton("Hippe (standaard)")
        self._brand_huren_radio = QRadioButton("Verhuurophalen")
        for rb in (self._brand_hippe_radio, self._brand_huren_radio):
            rb.setFont(QFont("DM Sans", 13))
            rb.setStyleSheet(f"color: {config.COLOR_TEXT};")
            brand_row.addWidget(rb)
        brand_row.addStretch()
        card_brand_lay.addLayout(brand_row)
        self._brand_hippe_radio.setChecked(True)
        self._brand_hippe_radio.toggled.connect(
            lambda on: self._on_backend_brand_changed('hippe') if on else None)
        self._brand_huren_radio.toggled.connect(
            lambda on: self._on_backend_brand_changed('huren') if on else None)

        # HiTi-knoppen — alleen relevant (en zichtbaar) in Verhuurophalen-modus
        self._brand_hiti_row = QWidget()
        hiti_row_lay = QHBoxLayout(self._brand_hiti_row)
        hiti_row_lay.setContentsMargins(0, 8, 0, 0)
        hiti_row_lay.setSpacing(10)
        hiti_btn_style = (
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 8px; padding: 8px 18px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        hiti_pick_btn = QPushButton("Printer kiezen…")
        hiti_pick_btn.setCursor(Qt.PointingHandCursor)
        hiti_pick_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        hiti_pick_btn.setFixedHeight(40)
        hiti_pick_btn.setStyleSheet(hiti_btn_style)
        hiti_pick_btn.clicked.connect(self._on_select_printer)
        hiti_row_lay.addWidget(hiti_pick_btn)
        hiti_setup_btn = QPushButton("HiTi driver instellen…")
        hiti_setup_btn.setCursor(Qt.PointingHandCursor)
        hiti_setup_btn.setFont(QFont("DM Sans", 12, QFont.Bold))
        hiti_setup_btn.setFixedHeight(40)
        hiti_setup_btn.setStyleSheet(hiti_btn_style)
        hiti_setup_btn.clicked.connect(self._on_configure_printer)
        hiti_row_lay.addWidget(hiti_setup_btn)
        hiti_row_lay.addStretch()
        card_brand_lay.addWidget(self._brand_hiti_row)
        self._brand_hiti_row.setVisible(False)
        tab5_lay.addWidget(card_brand)

        tab5_lay.addStretch()

        # Serienummer onderaan de Geavanceerd-tab (links) — read-only weergave
        # naast het invoerveld hierboven, zodat 'ie ook hier duidelijk zichtbaar
        # is. Wordt bijgewerkt in _refresh_adv_serial_footer.
        self._adv_serial_footer = QLabel("")
        self._adv_serial_footer.setFont(QFont("DM Sans", 11, QFont.Bold))
        self._adv_serial_footer.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._adv_serial_footer.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; padding: 0 10px;"
        )
        tab5_lay.addWidget(self._adv_serial_footer)

        # App version at bottom of Advanced tab — dynamisch vanuit config.VERSION
        version_label = QLabel(t("version", version=config.VERSION))
        version_label.setFont(QFont("DM Sans", 9))
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; padding: 10px;")
        tab5_lay.addWidget(version_label)
        self._settings_tab_stack.addWidget(tab5_scroll)

        # Add stacked widget to main layout
        lay.addWidget(self._settings_tab_stack, stretch=1)

        # Verhuur: forceer Linked-UI direct na build. Anders blijven Standalone-
        # widgets (event-picker, idle-bg) zichtbaar totdat _load_settings_for_event
        # ze opruimt — wat soms te laat / nooit gebeurt afhankelijk van flow.
        try:
            self._update_linked_card_visibility()
        except Exception as e:
            print(f"[BUILD] _update_linked_card_visibility fout (niet kritiek): {e}")

        # Set initial active tab
        self._switch_settings_tab(0)

        # Bottom bar
        lay.addSpacing(8)
        div3 = QWidget()
        div3.setFixedHeight(1)
        div3.setStyleSheet(f"background: {config.COLOR_BORDER};")
        lay.addWidget(div3)
        lay.addSpacing(8)

        # Terug-knop verwijderd — gebruik Escape of slotje om terug te gaan

        self.stack.addWidget(page)

    def _on_lock_clicked(self):
        """Slotje geklikt → toon info-dialog (event-info + acties) i.p.v.
        direct PIN-prompt. PIN-prompt komt pas bij Loskoppelen of
        Geavanceerde instellingen.
        """
        self._show_event_info_dialog()

    def _on_evinfo_dialog_closed(self, _result):
        """Stop de live-refresh timer wanneer de event-info dialog sluit."""
        try:
            if hasattr(self, '_evinfo_refresh_timer') and self._evinfo_refresh_timer:
                self._evinfo_refresh_timer.stop()
                self._evinfo_refresh_timer = None
        except Exception:
            pass
        self._evinfo_event_lbl = None
        self._evinfo_printer_lbl = None
        self._evinfo_dialog = None

    def _refresh_event_info_labels(self):
        """Update de event + printer regels in de info-dialog. Live update."""
        # Safety: dialog kan tussentijds gesloten zijn
        if not hasattr(self, '_evinfo_event_lbl') or self._evinfo_event_lbl is None:
            return
        if not hasattr(self, '_evinfo_printer_lbl') or self._evinfo_printer_lbl is None:
            return

        # ── Event-regel opbouwen ─────────────────────────────────
        ev = self.active_event
        booking_id = getattr(ev, 'linked_booking_id', '') if ev else ''
        booking_label = getattr(ev, 'linked_booking_label', '') if ev else ''
        if not booking_id:
            self._evinfo_event_lbl.setText("Geen event gekoppeld")
            self._evinfo_event_lbl.setStyleSheet(
                f"color: {config.COLOR_TEXT_DIM}; background: transparent;"
            )
        else:
            # Naam · datum komen al gecombineerd uit linked_booking_label
            # ("ron-debbygroet · 2026-06-05" typisch).
            name_date = booking_label or booking_id[:8]
            upload_part = ""
            try:
                from cloud_uploader import get_status
                s = get_status(booking_id)
                if s["total"] > 0:
                    pct = int(100 * s["uploaded"] / max(1, s["total"]))
                    upload_part = f"  ·  📤 {s['uploaded']}/{s['total']} ({pct}%)"
                    if s["failed"] > 0:
                        upload_part += f"  ·  ⚠ {s['failed']} mislukt"
                else:
                    upload_part = "  ·  📤 nog niets geüpload"
            except Exception:
                pass
            self._evinfo_event_lbl.setText(f"🟢  {name_date}{upload_part}")
            self._evinfo_event_lbl.setStyleSheet(
                f"color: {config.COLOR_TEXT}; background: transparent;"
            )

        # ── Printer-regel opbouwen ───────────────────────────────
        st = getattr(self, '_dnp_last_status', None)
        from dnp_status import StatusLevel
        if st is None:
            text = "⏳  Status nog niet bekend..."
            color = config.COLOR_TEXT_DIM
        elif st.level == StatusLevel.OK:
            text = "✅  Klaar"
            color = config.COLOR_SUCCESS
        elif st.level == StatusLevel.INFO:
            text = f"ℹ️  {st.label}"
            color = config.COLOR_TEXT
        elif st.level == StatusLevel.WARNING:
            text = f"⚠️  {st.label}"
            color = "#B07A00"
        elif st.level == StatusLevel.ERROR:
            if st.connected:
                code = f" (code {st.code})" if st.code is not None else ""
                text = f"❌  {st.label}{code}"
            else:
                text = "❌  Printer niet bereikbaar (USB?)"
            color = config.COLOR_DANGER
        else:  # UNKNOWN
            if st.connected:
                text = "🔧  USB-printer aangesloten, geen libusb-toegang"
            else:
                text = "🔌  Printer niet gevonden op USB"
            color = config.COLOR_TEXT_DIM

        # Voeg prints-over toe achter de status indien beschikbaar
        if (st is not None
                and getattr(st, 'prints_remaining', None) is not None
                and getattr(st, 'prints_total', None)):
            remain = st.prints_remaining
            total = st.prints_total
            pct = int(100 * remain / max(1, total))
            text += f"  ·  {remain}/{total} prints over  ·  {pct}%"

        self._evinfo_printer_lbl.setText(text)
        self._evinfo_printer_lbl.setStyleSheet(
            f"color: {color}; background: transparent;"
        )

    def _show_event_info_dialog(self):
        """Modal met event-info + acties: Ververs / Loskoppel / Geavanceerd.

        Loskoppel + Geavanceerd vragen om PIN. Ververs werkt direct.
        """
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
        )
        ev = self.active_event
        dlg = QDialog(self)
        dlg.setWindowTitle("Event-info")
        dlg.setModal(True)
        dlg.setMinimumWidth(480)
        dlg.setStyleSheet(f"QDialog {{ background: {config.COLOR_CARD_BG}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        # ── Header ──────────────────────────────────────────────────
        title = QLabel("Gekoppeld event")
        title.setFont(QFont("DM Sans", 18, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        lay.addWidget(title)

        # ── Event-block (compact, 1 regel met live update) ─────────
        status_frame = QFrame()
        status_frame.setStyleSheet(
            f"QFrame {{ background: {config.COLOR_INPUT_BG}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 10px; padding: 14px; }}"
        )
        st_lay = QVBoxLayout(status_frame)
        st_lay.setSpacing(6)

        booking_id = getattr(ev, 'linked_booking_id', '') if ev else ''

        # 1 regel: 🟢 naam · datum · 📤 X/Y (Z%)
        self._evinfo_event_lbl = QLabel("")
        self._evinfo_event_lbl.setFont(QFont("DM Sans", 14, QFont.Bold))
        self._evinfo_event_lbl.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        self._evinfo_event_lbl.setWordWrap(True)
        st_lay.addWidget(self._evinfo_event_lbl)
        lay.addWidget(status_frame)

        # ── Printer-block (compact) ────────────────────────────────
        printer_frame = QFrame()
        printer_frame.setStyleSheet(
            f"QFrame {{ background: {config.COLOR_INPUT_BG}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 10px; padding: 14px; }}"
        )
        pr_lay = QVBoxLayout(printer_frame)
        pr_lay.setSpacing(6)

        pr_title = QLabel(f"🖨️  Printer  ·  {config.PRINTER_NAME}")
        pr_title.setFont(QFont("DM Sans", 13, QFont.Bold))
        pr_title.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        pr_lay.addWidget(pr_title)

        # 1 regel: <icoon> Status · X/Y prints over · Z%
        self._evinfo_printer_lbl = QLabel("")
        self._evinfo_printer_lbl.setFont(QFont("DM Sans", 12, QFont.Bold))
        self._evinfo_printer_lbl.setWordWrap(True)
        pr_lay.addWidget(self._evinfo_printer_lbl)

        lay.addWidget(printer_frame)

        # ── Live refresh: elke 1.5s de labels bijwerken ───────────
        self._evinfo_dialog = dlg
        self._evinfo_refresh_timer = QTimer(dlg)
        self._evinfo_refresh_timer.setInterval(1500)
        self._evinfo_refresh_timer.timeout.connect(self._refresh_event_info_labels)
        self._evinfo_refresh_timer.start()
        # Stop timer bij sluiten van dialog (anders draait 'm tot app sluit)
        dlg.finished.connect(self._on_evinfo_dialog_closed)
        # Eerste render direct
        self._refresh_event_info_labels()

        # ── Actie-knoppen ──────────────────────────────────────────
        def _btn(label, color_bg, color_hover, font_size=14):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(QFont("DM Sans", font_size, QFont.Bold))
            b.setMinimumHeight(48)
            b.setStyleSheet(
                f"QPushButton {{ background: {color_bg}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
                f"border: none; border-radius: 10px; padding: 10px 18px; }}"
                f"QPushButton:hover {{ background: {color_hover}; }}"
                f"QPushButton:disabled {{ background: rgba(0,0,0,0.15); color: rgba(255,255,255,0.5); }}"
            )
            return b

        # Ververs (direct, geen PIN)
        refresh_btn = _btn("🔄  Ververs event", config.COLOR_PRIMARY, config.COLOR_PRIMARY_HOVER)
        refresh_btn.setEnabled(bool(booking_id))
        refresh_btn.clicked.connect(lambda: self._lock_action_refresh(dlg))
        lay.addWidget(refresh_btn)

        # Loskoppelen (PIN vereist)
        unlink_btn = _btn("🔗❌  Loskoppelen", config.COLOR_DANGER, "#8E2D24")
        unlink_btn.setEnabled(bool(booking_id))
        unlink_btn.clicked.connect(lambda: self._lock_action_unlink(dlg))
        lay.addWidget(unlink_btn)

        lay.addSpacing(8)

        # Geavanceerde instellingen (PIN vereist)
        settings_btn = _btn("⚙️  Geavanceerde instellingen",
                            config.COLOR_SECONDARY, config.COLOR_SECONDARY_HOVER)
        settings_btn.clicked.connect(lambda: self._lock_action_advanced(dlg))
        lay.addWidget(settings_btn)

        # Sluiten
        close_btn = QPushButton("Sluiten")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFont(QFont("DM Sans", 12))
        close_btn.setMinimumHeight(40)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; "
            "padding: 8px 16px; }"
            "QPushButton:hover { color: #333; }"
        )
        close_btn.clicked.connect(dlg.reject)
        lay.addWidget(close_btn, alignment=Qt.AlignCenter)

        dlg.exec_()

    def _lock_action_refresh(self, dlg):
        """Ververs-knop in event-info dialog: re-fetch booking + sluiten."""
        dlg.accept()
        # Hergebruik bestaande refresh-flow
        self._on_refresh_event_clicked()

    def _lock_action_unlink(self, dlg):
        """Loskoppelen vraagt PIN; bij correct PIN: clear linked_* en sluit."""
        pin = self.active_event.pin_code if self.active_event else ""
        if pin:
            try:
                entered, ok = PinDialog.get_pin(self, t("enter_pin"))
                if not ok or entered != pin:
                    return
            except Exception as e:
                print(f"[LOCK] PIN-prompt fout: {e}")
                return
        # PIN ok → volledig unlink
        ev = self.active_event
        old_booking_id = getattr(ev, 'linked_booking_id', '') if ev else ''
        if ev:
            ev.linked_booking_id = ""
            ev.linked_token = ""
            ev.linked_booking_label = ""
            ev.linked_design_path = ""
            ev.linked_photo_count = 0
            # Reset template_name omdat de cloud-templates straks weg zijn
            ev.template_name = ""
            ev.background_path = ""
            ev.save(config.EVENTS_DIR)
            print(f"[LOCK] Event losgekoppeld (was: {old_booking_id})")
            # Stop pending upload worker voor deze booking
            if old_booking_id:
                try:
                    from cloud_uploader import stop_worker
                    stop_worker(old_booking_id)
                except Exception as e:
                    print(f"[LOCK] Stop uploader fout: {e}")
            # Verwijder lokale cloud-template files voor deze booking
            if old_booking_id:
                try:
                    if os.path.isdir(config.TEMPLATES_DIR):
                        prefix = f"linked_{old_booking_id}_"
                        for fname in os.listdir(config.TEMPLATES_DIR):
                            if fname.startswith(prefix) and fname.endswith(".json"):
                                try:
                                    os.remove(os.path.join(config.TEMPLATES_DIR, fname))
                                    print(f"[LOCK] Lokale template verwijderd: {fname}")
                                except OSError:
                                    pass
                except Exception as e:
                    print(f"[LOCK] Templates cleanup fout: {e}")
        # Refresh UI als settings open is (linked-card moet 'Geen event' tonen)
        try:
            self._update_linked_card_visibility()
        except Exception:
            pass
        dlg.accept()
        # Terug naar welcome-page (idle routeert automatisch)
        self._go_idle()

    def _lock_action_advanced(self, dlg):
        """Geavanceerde instellingen vraagt PIN; bij ok: settings openen."""
        pin = self.active_event.pin_code if self.active_event else ""
        if pin:
            try:
                entered, ok = PinDialog.get_pin(self, t("enter_pin"))
                if not ok or entered != pin:
                    return
            except Exception as e:
                print(f"[LOCK] PIN-prompt fout: {e}")
                return
        dlg.accept()
        # PIN al geverifieerd — roep direct de "post-PIN" settings-flow aan
        # door _go_settings handmatig in te springen. We omzeilen dat
        # _go_settings opnieuw om PIN vraagt door even pin_code te verbergen.
        self._go_settings_after_pin()

    def _go_settings_after_pin(self):
        """Open settings panel ZONDER opnieuw PIN te vragen (al gevalideerd)."""
        # Nieuwe settings-sessie → Geavanceerd weer vergrendelen, zodat de
        # aparte code (config.ADVANCED_TAB_CODE) opnieuw gevraagd wordt.
        self._advanced_unlocked = False
        # Pauzeer DNP-poll tijdens settings — anders kan UI Automation
        # focus stelen tijdens typen.
        self._pause_dnp_poll(True)
        # Verberg printer-fout overlay als die openstaat — operator gaat
        # nu naar instellingen, niet meer naar idle-flow. Overlay komt
        # vanzelf terug bij terugkeer naar idle als de fout er nog is.
        try:
            self._hide_dnp_error_overlay()
        except Exception:
            pass
        self.state = State.SETTINGS
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowTitle("Bootharoo — Instellingen")
        self.show()
        self.showMaximized()
        try:
            self._populate_event_dropdown()
        except Exception as e:
            print(f"[SETTINGS] Event dropdown crash: {e}")
        try:
            self._load_settings_for_event()
        except Exception as e:
            print(f"[SETTINGS] Load settings crash: {e}")
        try:
            self._load_settings_templates()
        except Exception as e:
            print(f"[SETTINGS] Templates crash: {e}")
        self.stack.setCurrentIndex(self.pages["settings"])
        print(f"[SETTINGS] Geopend via lock-info dialog")

    def _go_settings(self):
        """Open operator panel, check PIN first if set."""
        import sys
        print("[SETTINGS] _go_settings aangeroepen!", flush=True)
        pin = self.active_event.pin_code if self.active_event else ""
        if pin:
            try:
                entered, ok = PinDialog.get_pin(self, t("enter_pin"))
                print(f"[PIN] entered='{entered}', ok={ok}, pin='{pin}'", flush=True)
                if not ok or entered != pin:
                    return
            except Exception as e:
                print(f"[PIN] Error: {e}", flush=True)
                import traceback; traceback.print_exc()
                return
        # Nieuwe settings-sessie → Geavanceerd weer vergrendelen.
        self._advanced_unlocked = False
        # Zelfde voorbereiding als _go_settings_after_pin: poller pauzeren
        # (focus-steal tijdens typen) + printer-fout overlay verbergen —
        # anders blokkeert het rode scherm de instellingen.
        self._pause_dnp_poll(True)
        try:
            self._hide_dnp_error_overlay()
        except Exception:
            pass
        # Exit fullscreen for settings — show as normal maximized window with title bar
        self.state = State.SETTINGS
        # Remove frameless/topmost flags so Windows title bar appears
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowTitle("Bootharoo — Instellingen")
        self.show()
        self.showMaximized()
        try:
            self._populate_event_dropdown()
            print("[SETTINGS] Event dropdown OK", flush=True)
        except Exception as e:
            print(f"[SETTINGS] Event dropdown crash: {e}", flush=True)
            import traceback; traceback.print_exc()
        try:
            self._load_settings_for_event()
            print("[SETTINGS] Settings loaded OK", flush=True)
        except Exception as e:
            print(f"[SETTINGS] Load settings crash: {e}", flush=True)
            import traceback; traceback.print_exc()
        try:
            self._load_settings_templates()
            print("[SETTINGS] Templates loaded OK", flush=True)
        except Exception as e:
            print(f"[SETTINGS] Templates crash: {e}", flush=True)
            import traceback; traceback.print_exc()
        self.stack.setCurrentIndex(self.pages["settings"])
        print(f"[SETTINGS] Geopend! Stack index: {self.pages['settings']}", flush=True)

    def _populate_event_dropdown(self):
        """Fill event dropdown with all events, select active one."""
        self._event_combo.blockSignals(True)
        self._event_combo.clear()
        self._event_combo_events = list_events(config.EVENTS_DIR)
        active_idx = 0
        for i, ev in enumerate(self._event_combo_events):
            self._event_combo.addItem(ev.name, ev.id)
            if self.active_event and ev.id == self.active_event.id:
                active_idx = i
        if self._event_combo_events and self.active_event:
            self._event_combo.setCurrentIndex(active_idx)
        elif not self._event_combo_events:
            self._event_combo.addItem("---")
        self._event_combo.blockSignals(False)

    def _on_event_dropdown_changed(self, index):
        """Handle event selection from dropdown."""
        if index < 0 or index >= len(getattr(self, '_event_combo_events', [])):
            return
        event = self._event_combo_events[index]
        # Reload from disk to get latest data
        path = os.path.join(config.EVENTS_DIR, f"{event.id}.json")
        if os.path.isfile(path):
            self.active_event = Event.load(path)
            self._save_active_event_id()
            self._load_settings_for_event()
            self._load_settings_templates()
            self._rebuild_idle_page()
            print(f"[SETTINGS] Event geselecteerd: {self.active_event.name}")

    def _on_event_create_new(self):
        """Create a new event via input dialog."""
        name, ok = TextInputDialog.get_text(
            self, t("new_event"), t("event_name") + ":"
        )
        if ok and name.strip():
            event = Event.create_new(name.strip())
            event.save(config.EVENTS_DIR)
            self.active_event = event
            self._save_active_event_id()
            self._populate_event_dropdown()
            self._load_settings_for_event()
            self._load_settings_templates()
            self._rebuild_idle_page()
            print(f"[EVENT] Nieuw event: {event.name}")

    def _on_save_photos_toggled(self, checked):
        """Toggle local photo saving."""
        if self.active_event:
            self.active_event.save_photos_locally = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Foto's lokaal opslaan: {'aan' if checked else 'uit'}")

    def _open_photos_folder(self):
        """Open de root photo-map (photos/).

        Vanaf v2.29 staan alle foto's onder photos/<event-naam>/{raw,strips,gif}/
        Bij openen tonen we de root zodat de klant zelf kan navigeren naar het
        gewenste event. Voorheen werd photos/<event-id>/ geopend (UUID-naam) —
        dat was verwarrend want de UUID zegt niemand wat.
        """
        photo_root = config.PHOTO_DIR
        if not os.path.isdir(photo_root):
            os.makedirs(photo_root, exist_ok=True)
        import subprocess
        subprocess.Popen(f'explorer "{photo_root}"')

    def _get_event_photo_dir(self):
        """Get the photo directory for the current event (creates if needed).

        Structure: photos/<event_name>/{raw,strips,gif}/
        Deze functie returnt de ROOT van het event (zonder subfolder).
        Voor specifieke types gebruik _get_raw_dir(), _get_strips_dir(),
        _get_gif_dir() — die maken óók hun subfolder aan.
        """
        if self.active_event:
            # Use event name as folder (sanitize for filesystem)
            safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in self.active_event.name).strip()
            if not safe_name:
                safe_name = self.active_event.id
            d = os.path.join(config.PHOTO_DIR, safe_name)
        else:
            # Geen actief event — gebruik "Standaard event" submap zodat ALLES
            # altijd binnen een event-map valt, ook zonder geselecteerd event.
            d = os.path.join(config.PHOTO_DIR, "Standaard event")
        os.makedirs(d, exist_ok=True)
        return d

    def _get_raw_dir(self):
        """photos/<event>/raw/ — rauwe foto's per capture."""
        d = os.path.join(self._get_event_photo_dir(), "raw")
        os.makedirs(d, exist_ok=True)
        return d

    def _get_strips_dir(self):
        """photos/<event>/strips/ — composiet-strips (incl. single-strip variant)."""
        d = os.path.join(self._get_event_photo_dir(), "strips")
        os.makedirs(d, exist_ok=True)
        return d

    def _get_gif_dir(self):
        """photos/<event>/gif/ — boomerang GIFs."""
        d = os.path.join(self._get_event_photo_dir(), "gif")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _timestamp_filename(ext=".jpg", photo_num=None, suffix=""):
        """Genereer bestandsnaam volgens nieuw format.

        Voorbeeld: DD-MM-YYYY_HH.MM.SS.jpg
                   DD-MM-YYYY_HH.MM.SS_1.jpg   (met photo_num=1)
                   DD-MM-YYYY_HH.MM.SS_enkel.jpg (met suffix='enkel')

        Args:
            ext: bestandsextensie (default .jpg)
            photo_num: optioneel positienummer binnen sessie (1-based)
            suffix: optioneel string-suffix (bv. 'enkel' voor single-strip)
        """
        ts = datetime.now().strftime("%d-%m-%Y_%H.%M.%S")
        if photo_num is not None:
            ts += f"_{photo_num}"
        if suffix:
            ts += f"_{suffix}"
        if not ext.startswith("."):
            ext = "." + ext
        return f"{ts}{ext}"

    def _on_event_delete(self):
        """Delete the currently selected event after confirmation."""
        if not self.active_event:
            return

        # Check if this is the only event — don't allow deletion
        all_events = list_events(config.EVENTS_DIR)
        if len(all_events) <= 1:
            from PyQt5.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setWindowTitle(t("cannot_delete"))
            msg.setText(t("cannot_delete_msg"))
            msg.setIcon(QMessageBox.Information)
            msg.setStandardButtons(QMessageBox.Ok)
            msg.setStyleSheet(
                f"QMessageBox {{ background: {config.COLOR_BG}; }}"
                f"QLabel {{ color: {config.COLOR_TEXT}; font-size: 14px; font-family: 'DM Sans'; }}"
                f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
                f"border: none; border-radius: 8px; padding: 10px 30px; font-size: 14px; "
                f"font-family: 'DM Sans'; font-weight: bold; min-height: 40px; min-width: 100px; }}"
                f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
            )
            msg.exec_()
            return

        # Confirm deletion with touchscreen-friendly dialog
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(t("delete_event"))
        msg.setText(t("confirm_delete_event", name=self.active_event.name))
        msg.setIcon(QMessageBox.Warning)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText(t("delete"))
        msg.button(QMessageBox.No).setText(t("cancel"))
        msg.setStyleSheet(
            f"QMessageBox {{ background: {config.COLOR_BG}; }}"
            f"QLabel {{ color: {config.COLOR_TEXT}; font-size: 14px; font-family: 'DM Sans'; }}"
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 10px 30px; font-size: 14px; "
            f"font-family: 'DM Sans'; font-weight: bold; min-height: 40px; min-width: 100px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
        )
        if msg.exec_() != QMessageBox.Yes:
            return

        name = self.active_event.name
        self.active_event.delete(config.EVENTS_DIR)
        print(f"[EVENT] Verwijderd: {name}")

        # Load another event (we already checked there are at least 2)
        remaining = list_events(config.EVENTS_DIR)
        if remaining:
            self.active_event = remaining[0]
        else:
            # Safety fallback — should never happen
            self.active_event = Event.create_new(t("default_text"))
            self.active_event.save(config.EVENTS_DIR)

        self._save_active_event_id()
        self._populate_event_dropdown()
        self._load_settings_for_event()
        self._load_settings_templates()

    def _load_settings_for_event(self):
        """Load current event's settings into the UI controls."""
        ev = self.active_event

        def _set(widget, value):
            # KRITIEK: in de verhuur-build zijn de Delen/Betaling-widgets
            # verwijderd; élke attribuut-toegang op zo'n dood sip-object
            # gooit RuntimeError. Zonder deze guard stierf deze methode
            # halverwege (bij _qr_toggle) en werden o.a. de backend-brand
            # radio's en camera-instellingen NOOIT gesynct — de switch
            # leek daardoor altijd terug te springen naar Hippe.
            try:
                if hasattr(widget, '_value') and hasattr(widget, '_val_label'):
                    # Touch spin widget
                    self._touch_spin_set(widget, value)
                elif isinstance(widget, QCheckBox):
                    widget.blockSignals(True)
                    widget.setChecked(value)
                    widget.blockSignals(False)
                elif isinstance(widget, QSpinBox):
                    widget.blockSignals(True)
                    widget.setValue(value)
                    widget.blockSignals(False)
                elif isinstance(widget, QLineEdit):
                    widget.blockSignals(True)
                    widget.setText(value)
                    widget.blockSignals(False)
                elif hasattr(widget, 'setChecked'):
                    # ToggleSwitch (QAbstractButton) — viel voorheen door alle
                    # branches heen waardoor toggles NOOIT gesynct werden met
                    # de opgeslagen waarde: na een herstart toonden ze altijd
                    # hun bouwtijd-default (aan), ongeacht de echte instelling.
                    widget.blockSignals(True)
                    widget.setChecked(bool(value))
                    widget.blockSignals(False)
            except RuntimeError:
                pass  # widget bestaat niet meer in deze build

        def _safe(fn):
            """Voer fn uit; sla over als widgets verwijderd zijn."""
            try:
                fn()
            except RuntimeError as ex:
                print(f"[SETTINGS] Sync-blok overgeslagen (widget weg): {ex}")

        # Serienummer-veld syncen (booth-wide; werkt ook zonder event).
        if hasattr(self, '_serial_input'):
            try:
                self._serial_input.blockSignals(True)
                self._serial_input.setText(self.serial_number)
                self._serial_input.blockSignals(False)
                if hasattr(self, '_adv_serial_footer'):
                    self._adv_serial_footer.setText(
                        f"Serienummer: {self.serial_number}" if self.serial_number else "")
            except RuntimeError:
                pass

        if ev:
            # Brand-radio's + PIN als EERSTE syncen — vóór alle fragiele
            # widget-aanrakingen, zodat dit nooit meer kan sneuvelen.
            self._sync_brand_radios()
            self._update_pin_button_text()
            _set(self._cut_checkbox, ev.cut_enabled)
            _set(self._print_enabled_toggle, ev.print_enabled)
            _set(self._auto_print_toggle, ev.auto_print)
            # Use the higher of auto_print_copies and legacy print_copies (backward compat)
            auto_copies = max(ev.auto_print_copies, ev.print_copies)
            _set(self._auto_copies_spin, auto_copies)
            _set(self._max_prints_spin, max(ev.max_prints, auto_copies if ev.auto_print else 1))
            _set(self._extra_prints_spin, ev.extra_prints_allowed)
            _set(self._qr_toggle, ev.gallery_enabled)
            # QR-branding state sync (verwijderde widgets: _safe slaat over)
            try:
                if hasattr(self, '_qr_branding_toggle'):
                    _set(self._qr_branding_toggle, getattr(ev, 'qr_branding_enabled', False))
                if hasattr(self, '_qr_branding_text'):
                    self._qr_branding_text.blockSignals(True)
                    self._qr_branding_text.setPlainText(getattr(ev, 'qr_branding_text', '') or '')
                    self._qr_branding_text.blockSignals(False)
                    self._qr_branding_text.setVisible(getattr(ev, 'qr_branding_enabled', False))
                if hasattr(self, '_qr_branding_container'):
                    self._qr_branding_container.setVisible(bool(ev.gallery_enabled))
            except RuntimeError:
                pass
            _set(self._email_toggle, ev.email_enabled)
            _set(self._email_collect_toggle, getattr(ev, 'email_collect', False))
            _set(self._email_subject_input, ev.email_subject)
            try:
                self._email_body_input.blockSignals(True)
                self._email_body_input.setPlainText(ev.email_body)
                self._email_body_input.blockSignals(False)
            except RuntimeError:
                pass
            _set(self._email_send_strip_cb, ev.email_send_strip)
            _set(self._share_single_strip_cb, ev.share_single_strip)
            _set(self._compress_sharing_cb, ev.compress_sharing)
            _set(self._email_send_originals_cb, ev.email_send_originals)
            _set(self._email_send_gif_cb, ev.email_send_gif)
            _set(self._countdown_spin, ev.countdown_seconds)
            _set(self._delay_spin, max(0, ev.photo_delay_ms // 1000))
            _set(self._sharing_timeout_spin, ev.sharing_timeout)
            _set(self._lock_size_spin, ev.lock_icon_size)
            # Camera settings
            try:
                if hasattr(self, '_cam_dslr_radio'):
                    self._cam_dslr_radio.blockSignals(True)
                    self._cam_webcam_radio.blockSignals(True)
                    if ev.camera_mode == "webcam":
                        self._cam_webcam_radio.setChecked(True)
                    else:
                        self._cam_dslr_radio.setChecked(True)
                    # Verhuur: picker-rij ALTIJD zichtbaar — ook in Canon-stand
                    # (huren+dslr) is dit de enige plek om van camera te wisselen.
                    self._webcam_select_row.setVisible(True)
                    self._cam_dslr_radio.blockSignals(False)
                    self._cam_webcam_radio.blockSignals(False)
                    self._update_webcam_status()
                    _set(self._cam_mirror_cb, ev.camera_mirror)
                    rot_map = {0: 0, 90: 1, 180: 2, 270: 3}
                    self._cam_rotation_combo.blockSignals(True)
                    self._cam_rotation_combo.setCurrentIndex(rot_map.get(ev.camera_rotation, 0))
                    self._cam_rotation_combo.blockSignals(False)
            except RuntimeError:
                pass
            # Live view positie radio + alignment toepassen
            try:
                if hasattr(self, '_live_view_pos_radios'):
                    pos = getattr(ev, 'live_view_position', 'center') or 'center'
                    if pos not in self._live_view_pos_radios:
                        pos = 'center'
                    for v, rb in self._live_view_pos_radios.items():
                        rb.blockSignals(True)
                        rb.setChecked(v == pos)
                        rb.blockSignals(False)
                    self._apply_live_view_alignment()
            except RuntimeError:
                pass
        else:
            # Geen actief event: brand-radio's + camerastatus tonen de
            # booth-wide waarden (anders bleven ze op de bouwtijd-default
            # 'hippe' / "geen webcam" staan).
            self._sync_brand_radios()
            self._update_webcam_status()
            _set(self._cut_checkbox, True)
            _set(self._print_enabled_toggle, True)
            _set(self._auto_print_toggle, True)
            _set(self._auto_copies_spin, 1)
            _set(self._max_prints_spin, 1)
            _set(self._extra_prints_spin, 0)
            _set(self._qr_toggle, False)
            _set(self._email_toggle, False)
            _set(self._email_collect_toggle, False)
            _set(self._email_subject_input, "Jouw Photobooth Foto's!")
            _safe(lambda: self._email_body_input.setPlainText(""))
            _set(self._email_send_strip_cb, True)
            _set(self._share_single_strip_cb, False)
            _set(self._compress_sharing_cb, False)
            _set(self._email_send_originals_cb, False)
            _set(self._email_send_gif_cb, True)
            _set(self._countdown_spin, 3)
            _set(self._delay_spin, 5)
            _set(self._sharing_timeout_spin, 30)
            _set(self._lock_size_spin, 60)
            if hasattr(self, '_cam_dslr_radio'):
                self._cam_dslr_radio.setChecked(True)
                _set(self._cam_mirror_cb, False)
                self._cam_rotation_combo.setCurrentIndex(0)
            # Geen event — radio terug naar default (center) zonder alignment-call
            if hasattr(self, '_live_view_pos_radios'):
                for v, rb in self._live_view_pos_radios.items():
                    rb.blockSignals(True)
                    rb.setChecked(v == "center")
                    rb.blockSignals(False)
            self._update_pin_button_text()

        # Update printer name label (elk blok _safe: verwijderde widgets
        # in de verhuur-build mogen de rest van de sync niet blokkeren)
        _safe(lambda: self._printer_name_label.setText(
            config.PRINTER_NAME or t("printer_not_selected")))

        # Update printer settings visibility
        _safe(self._update_printer_visibility)

        # Update Gmail status and email section visibility
        _safe(self._update_gmail_status)
        _safe(self._update_email_visibility)

        # Update idle background preview
        _safe(self._update_bg_preview)

        # Update layout background preview
        _safe(self._update_layout_bg_preview)

        # Update photo storage toggle
        if hasattr(self, '_save_photos_toggle') and ev:
            _set(self._save_photos_toggle, getattr(ev, 'save_photos_locally', True))

        # Update printer-modus 3-knop selector (4x3/4x6/3strips)
        if hasattr(self, '_printer_mode_btn_3strips') and ev:
            mode = getattr(ev, 'printer_mode', '3strips')
            # Legacy fallback (mocht ev nog niet door migratie zijn gegaan)
            if mode == 'canon':
                mode = '4x6'
            elif mode == 'dnp':
                mode = '3strips'
            mapping = {
                '4x3': self._printer_mode_btn_4x3,
                '4x6': self._printer_mode_btn_4x6,
                '3strips': self._printer_mode_btn_3strips,
            }
            for btn in mapping.values():
                btn.blockSignals(True)
            for m, btn in mapping.items():
                btn.setChecked(m == mode)
            for btn in mapping.values():
                btn.blockSignals(False)

        # DNP profielen-card is verplaatst naar dialog achter knop in
        # Geavanceerd — niets te tonen/verbergen op event-load.

        # Update booth-modus radio (Standalone/Linked) + linked event card
        try:
            if hasattr(self, '_booth_mode_linked_radio') and ev:
                bmode = getattr(ev, 'booth_mode', 'standalone')
                self._booth_mode_standalone_radio.blockSignals(True)
                self._booth_mode_linked_radio.blockSignals(True)
                if bmode == 'linked':
                    self._booth_mode_linked_radio.setChecked(True)
                else:
                    self._booth_mode_standalone_radio.setChecked(True)
                self._booth_mode_standalone_radio.blockSignals(False)
                self._booth_mode_linked_radio.blockSignals(False)
                self._update_linked_card_visibility()
        except RuntimeError:
            pass

        # Update payment settings (verhuur: betaling-widgets verwijderd)
        try:
            if hasattr(self, '_payment_toggle') and ev:
                self._payment_toggle.blockSignals(True)
                self._payment_toggle.setChecked(ev.payment_enabled)
                self._payment_toggle.blockSignals(False)
                self._update_payment_info()
        except RuntimeError:
            pass

        # Update SumUp/Clixibo terminal toggle
        try:
            if hasattr(self, '_sumup_toggle') and ev:
                self._sumup_toggle.blockSignals(True)
                self._sumup_toggle.setChecked(getattr(ev, 'sumup_enabled', False))
                self._sumup_toggle.blockSignals(False)
                self._update_sumup_status()
        except RuntimeError:
            pass

        # Update payment method radio + zichtbaarheid van payment-cards
        try:
            if hasattr(self, '_payment_method_radios') and ev:
                method = getattr(ev, 'payment_method', 'none')
                if method not in self._payment_method_radios:
                    method = 'none'
                for v, rb in self._payment_method_radios.items():
                    rb.blockSignals(True)
                    rb.setChecked(v == method)
                    rb.blockSignals(False)
                # Toon/verberg cards bij laden. Custom-mode toont Stripe + Voucher
                # + Custom-card tegelijk.
                if hasattr(self, '_payment_card'):
                    self._payment_card.setVisible(method in ("stripe", "custom"))
                if hasattr(self, '_sumup_card'):
                    self._sumup_card.setVisible(method == "sumup")
                if hasattr(self, '_voucher_card'):
                    self._voucher_card.setVisible(method in ("voucher", "custom"))
                if hasattr(self, '_custom_card'):
                    self._custom_card.setVisible(method == "custom")
                if method in ("voucher", "custom"):
                    self._refresh_voucher_ui()
                if method == "custom":
                    self._refresh_custom_ui()
        except RuntimeError:
            pass

        # Update event-limiet UI op basis van het actieve event
        if hasattr(self, '_evlimit_status_label'):
            try:
                self._refresh_event_limit_ui()
            except Exception as ex:
                print(f"[PRINT-QUOTA] UI refresh fout: {ex}")

        # Capture screen settings removed — freeze frame is used instead

        # Update intro screen preview + text fields
        try:
            if hasattr(self, '_intro_preview_label'):
                self._update_intro_preview()
                if self.active_event:
                    _set(self._intro_duration_spin, self.active_event.intro_duration)
                    if hasattr(self, '_intro_text_toggle'):
                        self._intro_text_toggle.setChecked(self.active_event.intro_text_enabled)
                        self._intro_text_input.setText(self.active_event.intro_text)
                        self._intro_text_input.setEnabled(self.active_event.intro_text_enabled)
        except RuntimeError:
            pass

        # Capture text settings removed — freeze frame used instead

        # Update account info
        _safe(self._update_account_info)

    def _update_account_info(self):
        """Update the Licentie card in settings with current license info."""
        user, token = auth.load_session()
        # Only show as active if BOTH the session exists AND _auth_plan is set
        # (online verification may have deactivated the license)
        is_active = bool(user and user.get("active") and self._auth_plan)
        if is_active:
            name = user.get("name", user.get("email", "Onbekend"))
            plan = user.get("plan", "starter")
            plan_display = "Professional" if plan == "professional" else "Starter"
            key = user.get("license_key", "")
            end_str = user.get("subscription_end", "")

            is_perm = user.get("is_permanent", False)
            perm_badge = " (Permanent)" if is_perm else ""
            self._account_status_label.setText("\u2705  " + t("license_active") + perm_badge)
            self._account_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS}; background: transparent;")
            self._account_email_label.setText(t("license_name_label", name=name))
            self._account_plan_label.setText(t("license_plan_label", plan=plan_display + perm_badge))

            if key:
                self._account_key_label.setText(t("license_code_label", code=key))
                self._account_key_label.show()
            else:
                self._account_key_label.hide()

            if end_str:
                try:
                    end_clean = end_str.replace("Z", "+00:00")
                    end_date = datetime.fromisoformat(end_clean)
                    formatted = end_date.strftime("%d-%m-%Y")
                    if is_perm:
                        self._account_expiry_label.setText(
                            f"{t('license_valid_forever')}\n"
                            f"{t('pro_expires_on', date=formatted)}"
                        )
                    else:
                        self._account_expiry_label.setText(t("license_valid_until", date=formatted))
                    self._account_expiry_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
                except Exception:
                    self._account_expiry_label.setText(t("license_valid_until", date=end_str))
            else:
                if is_perm:
                    self._account_expiry_label.setText(t("license_valid_forever"))
                else:
                    self._account_expiry_label.setText("")

            self._account_activate_btn.hide()
            self._account_logout_btn.show()
        else:
            self._account_status_label.setText("\u274c  " + t("license_not_activated"))
            self._account_status_label.setStyleSheet(f"color: {config.COLOR_DANGER}; background: transparent;")
            self._account_email_label.setText(t("activate_to_remove_watermark"))
            self._account_email_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
            self._account_plan_label.setText("")
            self._account_expiry_label.setText("")
            self._account_key_label.hide()
            self._account_activate_btn.show()
            self._account_logout_btn.hide()

    def _load_settings_templates(self):
        """Populate the layout categories in settings with preset + custom layouts."""
        # Clear existing category widgets
        while self._layout_categories_container.count():
            item = self._layout_categories_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Presets + custom JSON templates from templates dir
        self._preset_layouts = get_preset_layouts()
        custom = []
        if os.path.isdir(config.TEMPLATES_DIR):
            for fname in sorted(os.listdir(config.TEMPLATES_DIR)):
                if fname.lower().endswith(".json"):
                    try:
                        from template_model import Template as TModel
                        tmpl = TModel.load(os.path.join(config.TEMPLATES_DIR, fname))
                        custom.append(tmpl)
                    except Exception:
                        pass

        self._settings_template_widgets = {}
        self._custom_template_names = set()  # Track which are custom (deletable)
        selected = self.active_event.template_name if self.active_event else ""

        # Build lookup of custom templates by name (custom overrides preset frames)
        custom_by_name = {}
        for tmpl in custom:
            custom_by_name[tmpl.name] = tmpl

        # Categorize layouts by cut setting
        # Use custom version if available (has user-edited frame positions)
        cat_cut = []      # Snijden (dubbele strips)
        cat_nocut = []    # Niet snijden (enkele strips / volledig)

        # Filter op printer-modus (4x3 / 4x6 / 3strips). Legacy → nieuwe naam.
        pm = getattr(self.active_event, 'printer_mode', '3strips') if self.active_event else '3strips'
        if pm == 'canon':
            pm = '4x6'
        elif pm == 'dnp':
            pm = '3strips'

        # Verhuur = altijd Linked-modus. Alleen de linked template variants tonen.
        # Twee naam-conventies:
        #   - "Event <id> (N foto's)"   = legacy auto-gen (Fase 1 en eerder)
        #   - "Event <id> — <naam>"     = cloud-template (Fase 2+)
        # Beide worden getoond zodat operator tussen varianten kan kiezen.
        booth_mode = "linked"  # forced
        linked_booking_id = getattr(self.active_event, 'linked_booking_id', '') if self.active_event else ''
        linked_variants = []
        if linked_booking_id:
            prefix_legacy = f"Event {linked_booking_id[:8]} ("
            prefix_cloud = f"Event {linked_booking_id[:8]} — "
            for tmpl in custom:
                is_legacy = tmpl.name.startswith(prefix_legacy) and tmpl.name.endswith(" foto's)")
                is_cloud = tmpl.name.startswith(prefix_cloud)
                if is_legacy or is_cloud:
                    linked_variants.append(tmpl)
            linked_variants.sort(key=lambda t: t.num_photos)

        for tmpl in linked_variants:
            self._custom_template_names.add(tmpl.name)

        self._cat_grids = {}
        if linked_variants:
            # Geen categorie-header — direct grid van varianten
            self._add_layout_category("", linked_variants, selected, start_open=True)

        if selected:
            self.settings_active_label.setText(t("layout_label", name=self._translate_template_name(selected)))
            self.settings_active_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self._edit_layout_btn.setVisible(True)
        else:
            self.settings_active_label.setText(t("no_layout_selected"))
            self.settings_active_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            self._edit_layout_btn.setVisible(False)

    def _add_layout_category(self, cat_name, layouts, selected, start_open=False):
        """Add a collapsible category section with layout thumbnails."""
        # Category header button
        arrow = "\u25BC" if start_open else "\u25B6"
        header = QPushButton(f"  {arrow}  {cat_name}  ({len(layouts)})")
        header.setCursor(Qt.PointingHandCursor)
        header.setFont(QFont("DM Sans", 12, QFont.Bold))
        header.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_CARD_BG}; color: {config.COLOR_TEXT}; "
            f"border: none; border-radius: 6px; padding: 10px 14px; "
            f"text-align: left; }}"
            f"QPushButton:hover {{ background: {config.COLOR_ACCENT}; }}"
        )
        self._layout_categories_container.addWidget(header)

        # Grid container widget
        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setSpacing(10)
        grid_layout.setContentsMargins(10, 6, 10, 6)

        # Dynamic columns based on screen width
        screen_w = self.width() if self.width() > 0 else 1920
        if screen_w < 600:
            cols = 2
        elif screen_w < 900:
            cols = 3
        else:
            cols = 4
        col, row = 0, 0
        for layout in layouts:
            thumb = self._make_layout_thumb(layout, layout.name == selected)
            grid_layout.addWidget(thumb, row, col)
            self._settings_template_widgets[layout.name] = thumb
            col += 1
            if col >= cols:
                col = 0
                row += 1

        grid_widget.setVisible(start_open)
        self._layout_categories_container.addWidget(grid_widget)

        # Store reference for toggle
        self._cat_grids[cat_name] = (header, grid_widget)

        # Toggle on click
        def toggle(_, gw=grid_widget, hdr=header, name=cat_name, count=len(layouts)):
            vis = not gw.isVisible()
            gw.setVisible(vis)
            a = "\u25BC" if vis else "\u25B6"
            hdr.setText(f"  {a}  {name}  ({count})")
        header.mousePressEvent = toggle

    @staticmethod
    def _translate_template_name(name):
        """Translate preset template name to current language."""
        _tpl_map = {
            "Dubbele strip met 3 foto's": "tpl_double_3",
            "Dubbele strip met 4 foto's": "tpl_double_4",
            "2 foto's liggend": "tpl_2_landscape",
            "1 grote foto": "tpl_1_large",
            "2x2 liggend": "tpl_2x2_landscape",
            "3 foto's onder elkaar": "tpl_3_stacked",
            "2 enkele foto's": "tpl_2_single",
            "1 enkele foto": "tpl_1_single",
        }
        key = _tpl_map.get(name)
        return t(key) if key else name

    def _make_layout_thumb(self, layout, is_selected=False):
        """Create a visual layout preview thumbnail for settings."""
        is_custom = layout.name in getattr(self, '_custom_template_names', set())
        container = QWidget()
        container.setFixedSize(160, 200)
        container.setCursor(Qt.PointingHandCursor)
        border = f"3px solid {config.COLOR_PRIMARY}" if is_selected else f"2px solid {config.COLOR_BORDER}"
        container.setStyleSheet(
            f"QWidget {{ background: {config.COLOR_CARD_BG}; border: {border}; border-radius: 8px; }}"
            f"QWidget:hover {{ border-color: {config.COLOR_PRIMARY}; }}"
        )

        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # Draw layout preview — wrap in try/except zodat 1 stuk template
        # niet de hele grid laat crashen
        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setFixedHeight(155)
        try:
            preview = self._render_layout_preview(layout, 148, 155)
            if preview:
                thumb_label.setPixmap(preview)
        except Exception as e:
            thumb_label.setText(f"⚠️\n{e}")
            thumb_label.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 9px;")
            print(f"[LAYOUT-GRID] Preview render fout voor '{layout.name}': {e}")
        lay.addWidget(thumb_label)

        # Layout name (translated)
        name_label = QLabel(self._translate_template_name(layout.name))
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setFont(QFont("DM Sans", 8))
        name_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; border: none;")
        name_label.setWordWrap(True)
        name_label.setMaximumHeight(28)
        lay.addWidget(name_label)

        container.mousePressEvent = lambda e, t=layout: self._on_layout_selected(t)
        return container

    def _render_layout_preview(self, layout, w, h, tight=False):
        """Render a QPixmap showing the frame layout as colored rectangles.

        Args:
            tight: True = canvas vult de hele pixmap, geen letterbox-margin.
                   False = canvas centreert met 8% breathing-room.
        Achtergrond rondom canvas is ALTIJD transparant — laat de parent
        widget z'n eigen background tonen. Voor een 'gevallen vel'-effect
        kan de caller een drop-shadow op het label zetten.
        """
        from PyQt5.QtGui import QPainter, QPen, QBrush
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Canvas size hangt af van strip-type + frame-extents:
        #  triple_strip → 600x1200 portrait (5x10 cm DNP strip)
        #  4x3_strip    → 1200x900 landscape (4x3 paper)
        #  landscape    → 1800x1200 (cloud template '4 foto's op een vel')
        #  anders       → 1200x1800 (4x6 vel portrait)
        if getattr(layout, 'is_triple_strip', False):
            canvas_w = 600
            canvas_h = 1200
        elif getattr(layout, 'is_4x3_strip', False):
            canvas_w = 1200
            canvas_h = 900
        else:
            # Landscape-detectie via pure frame-positie. Als frames buiten de
            # 1200px portrait-breedte vallen, moet canvas wel landscape zijn.
            # is_double_strip flag wordt NIET gebruikt — bleek niet consistent
            # gezet in oudere DB-rijen.
            _fr = layout.frames or []
            _max_x = max((f.x + f.width for f in _fr), default=0)
            _max_y = max((f.y + f.height for f in _fr), default=0)
            if _max_x > 1200 and _max_x > _max_y:
                canvas_w = 1800
                canvas_h = 1200
            else:
                canvas_w = 1200
                canvas_h = 1800
        scale_x = w / canvas_w
        scale_y = h / canvas_h
        # tight=True: canvas vult volledig (geen margin); anders 8% breathing-room
        scale = min(scale_x, scale_y) * (1.0 if tight else 0.92)
        offset_x = (w - canvas_w * scale) / 2
        offset_y = (h - canvas_h * scale) / 2

        page_w = int(canvas_w * scale)
        page_h = int(canvas_h * scale)
        px, py = int(offset_x), int(offset_y)

        # Draw page background - prefer event bg, then template bg, then pink
        bg_path = ""
        if hasattr(self, 'active_event') and self.active_event and self.active_event.background_path:
            bg_path = self.active_event.background_path
        elif layout.background_path:
            bg_path = layout.background_path

        if bg_path and os.path.isfile(bg_path):
            bg = QPixmap(bg_path)
            if not bg.isNull():
                scaled_bg = bg.scaled(page_w, page_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                crop_x = (scaled_bg.width() - page_w) // 2
                crop_y = (scaled_bg.height() - page_h) // 2
                painter.drawPixmap(px, py, scaled_bg.copy(crop_x, crop_y, page_w, page_h))
                painter.setPen(QPen(QColor("#cccccc"), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(px, py, page_w, page_h)
            else:
                painter.setPen(QPen(QColor("#cccccc"), 1))
                painter.setBrush(QBrush(QColor("#ffe0e6")))
                painter.drawRect(px, py, page_w, page_h)
        else:
            painter.setPen(QPen(QColor("#cccccc"), 1))
            painter.setBrush(QBrush(QColor("#ffe0e6")))
            painter.drawRect(px, py, page_w, page_h)

        # Cut line: alleen voor klassieke single-strip (HiTi-cut tussen 2 helften).
        # Triple strip heeft 2 horizontale cuts; voor preview-eenvoud niet getekend.
        if not layout.is_double_strip and not getattr(layout, 'is_triple_strip', False):
            cut_x = int(offset_x + 600 * scale)
            painter.setPen(QPen(QColor("#ccaaaa"), 1, Qt.DashLine))
            painter.drawLine(cut_x, int(offset_y), cut_x, int(offset_y + canvas_h * scale))

        # Draw frames as photo-like placeholders with silhouette
        from PyQt5.QtGui import QLinearGradient, QPolygonF
        from PyQt5.QtCore import QPointF

        def _draw_photo_placeholder(painter, fx, fy, fw, fh, rotation=0):
            """Draw a gray gradient frame with a person silhouette.
            If rotation=90, the photo is landscape so the silhouette is rotated."""
            # Gray gradient background
            grad = QLinearGradient(fx, fy, fx, fy + fh)
            grad.setColorAt(0.0, QColor("#666666"))
            grad.setColorAt(1.0, QColor("#999999"))
            painter.setPen(QPen(QColor("#555555"), 1))
            painter.setBrush(QBrush(grad))
            painter.drawRect(fx, fy, fw, fh)

            # Only rotate silhouette when template explicitly sets rotation
            is_landscape = (rotation != 0)
            cx = fx + fw / 2
            cy = fy + fh / 2

            if is_landscape:
                # Rotated 90° silhouette for landscape frames
                head_r = min(fw, fh) * 0.12
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(255, 255, 255, 60)))
                # Head shifted left of center
                painter.drawEllipse(QPointF(cx - head_r * 0.6, cy), head_r, head_r)
                # Shoulders (trapezoid to the right of head)
                sh_left = cx + head_r * 0.6
                sh_right = sh_left + head_r * 1.6
                sh_h_top = head_r * 1.2
                sh_h_bot = head_r * 2.4
                shoulders = QPolygonF([
                    QPointF(sh_left, cy - sh_h_top),
                    QPointF(sh_left, cy + sh_h_top),
                    QPointF(sh_right, cy + sh_h_bot),
                    QPointF(sh_right, cy - sh_h_bot),
                ])
                painter.drawPolygon(shoulders)
            else:
                # Portrait silhouette (upright)
                head_r = min(fw, fh) * 0.12
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(255, 255, 255, 60)))
                painter.drawEllipse(QPointF(cx, cy - head_r * 0.6), head_r, head_r)
                # Shoulders (trapezoid below head)
                sh_top = cy + head_r * 0.6
                sh_bot = sh_top + head_r * 1.6
                sh_w_top = head_r * 1.2
                sh_w_bot = head_r * 2.4
                shoulders = QPolygonF([
                    QPointF(cx - sh_w_top, sh_top),
                    QPointF(cx + sh_w_top, sh_top),
                    QPointF(cx + sh_w_bot, sh_bot),
                    QPointF(cx - sh_w_bot, sh_bot),
                ])
                painter.drawPolygon(shoulders)

        is_triple = getattr(layout, 'is_triple_strip', False)
        for frame in layout.frames:
            fx = int(offset_x + frame.x * scale)
            fy = int(offset_y + frame.y * scale)
            fw = int(frame.width * scale)
            fh = int(frame.height * scale)
            frame_rot = getattr(frame, 'rotation', 0)
            _draw_photo_placeholder(painter, fx, fy, fw, fh, frame_rot)

            # Single-strip op HiTi wordt gespiegeld naar rechterhelft (cut tussen).
            # Triple-strip is een fysiek 5x10cm ontwerp — geen duplicatie in preview.
            if not layout.is_double_strip and not is_triple:
                fx2 = int(offset_x + (frame.x + 600) * scale)
                _draw_photo_placeholder(painter, fx2, fy, fw, fh, frame_rot)

        painter.end()

        # Display-rotatie: als de template alle frames 90/270 heeft, toon
        # de preview gedraaid zodat wat je in instellingen ziet overeenkomt
        # met de oriëntatie waarin de gast hem ziet. (Print blijft op de
        # oorspronkelijke oriëntatie, dit is alleen UI.)
        #
        # NB: in de preview wordt de silhouette getekend met "head left" voor
        # landscape frames, terwijl de échte foto in de strip "head right"
        # uitkomt na PIL rotate(-frame.rotation). Daardoor moet de preview-
        # pixmap de OMGEKEERDE rotatie t.o.v. de share-flow gebruiken om er
        # visueel rechtop uit te zien voor de bediener.
        target = _layout_display_rotation(layout)
        if target != 0:
            from PyQt5.QtGui import QTransform
            # +target voor settings (omgekeerd t.o.v. share/print-flow elders)
            transform = QTransform().rotate(target)
            pixmap = pixmap.transformed(transform, Qt.SmoothTransformation)
        return pixmap

    def _on_delete_custom_template(self, template_name):
        """Delete a custom template JSON file after confirmation."""
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(t("delete"))
        msg.setText(t("confirm_delete_event", name=template_name))
        msg.setIcon(QMessageBox.Warning)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText(t("delete"))
        msg.button(QMessageBox.No).setText(t("cancel"))
        msg.setStyleSheet(
            f"QMessageBox {{ background: {config.COLOR_BG}; }}"
            f"QLabel {{ color: {config.COLOR_TEXT}; font-size: 14px; font-family: 'DM Sans'; }}"
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 10px 30px; font-size: 14px; "
            f"font-family: 'DM Sans'; font-weight: bold; min-height: 40px; min-width: 100px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
        )
        if msg.exec_() != QMessageBox.Yes:
            return

        # Find and delete the JSON file
        import json as _json
        if os.path.isdir(config.TEMPLATES_DIR):
            for fname in os.listdir(config.TEMPLATES_DIR):
                if fname.lower().endswith(".json"):
                    try:
                        fpath = os.path.join(config.TEMPLATES_DIR, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = _json.load(f)
                        if data.get("name") == template_name:
                            os.remove(fpath)
                            print(f"[TEMPLATE] Verwijderd: {template_name} ({fname})")
                            break
                    except Exception:
                        pass

        # If active event was using this template, clear it
        if self.active_event and self.active_event.template_name == template_name:
            self.active_event.template_name = ""
            self.active_event.save(config.EVENTS_DIR)

        # Refresh the template gallery
        self._load_settings_templates()

    def _on_layout_selected(self, layout):
        """Handle layout click: select and auto-save layout + cut to event."""
        # Update highlight
        for name, w in self._settings_template_widgets.items():
            if name == layout.name:
                w.setStyleSheet(
                    f"QWidget {{ background: {config.COLOR_CARD_BG}; border: 3px solid {config.COLOR_PRIMARY}; border-radius: 8px; }}"
                )
            else:
                w.setStyleSheet(
                    f"QWidget {{ background: {config.COLOR_CARD_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; }}"
                    f"QWidget:hover {{ border-color: {config.COLOR_PRIMARY}; }}"
                )
        self.settings_active_label.setText(t("layout_label", name=self._translate_template_name(layout.name)))
        self.settings_active_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        self._edit_layout_btn.setVisible(True)
        # Update cut checkbox to layout default
        self._cut_checkbox.blockSignals(True)
        self._cut_checkbox.setChecked(layout.cut_default)
        self._cut_checkbox.blockSignals(False)
        # Auto-save layout + cut setting to event
        if self.active_event:
            self.active_event.template_name = layout.name
            self.active_event.cut_enabled = layout.cut_default
            self.active_event.save(config.EVENTS_DIR)
            cut_text = "ja" if layout.cut_default else "nee"
            print(f"[SETTINGS] Layout opgeslagen: {layout.name} (snijden: {cut_text})")
        # Update background preview
        self._update_layout_bg_preview()

    def _on_layout_bg_change(self):
        """Change the background image for the active event."""
        from PyQt5.QtWidgets import QFileDialog
        ev = self.active_event
        if not ev:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Layout achtergrond kiezen",
            os.path.expanduser("~"),
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return
        ev.background_path = path
        ev.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Event achtergrond ingesteld: {path}")
        self._load_settings_templates()  # Refresh previews
        self._update_layout_bg_preview()

    def _on_layout_bg_remove(self):
        """Remove the background from the active event."""
        ev = self.active_event
        if not ev:
            return
        ev.background_path = ""
        ev.save(config.EVENTS_DIR)
        print("[SETTINGS] Event achtergrond verwijderd")
        self._load_settings_templates()  # Refresh previews
        self._update_layout_bg_preview()

    def _update_layout_bg_preview(self):
        """Update the background preview label and buttons in the Layout card."""
        ev = self.active_event
        has_event = ev is not None
        self._layout_bg_btn.setVisible(has_event)

        if not has_event:
            self._layout_bg_label.setText(t("select_event_first"))
            self._layout_bg_remove_btn.setVisible(False)
            return

        bg_path = ev.background_path
        if bg_path and os.path.isfile(bg_path):
            fname = os.path.basename(bg_path)
            self._layout_bg_label.setText(t("bg_current", name=fname))
            self._layout_bg_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self._layout_bg_remove_btn.setVisible(True)
        else:
            self._layout_bg_label.setText(t("bg_white"))
            self._layout_bg_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            self._layout_bg_remove_btn.setVisible(False)

    # ── Printer-storingsmeldingen (statusuitlezing) aan/uit ──────────
    def _printer_status_enabled(self):
        """Of de printer-statusuitlezing (storingsmeldingen) aan staat.

        Per-booth opgeslagen in settings.json; standaard AAN. Uitzetten als
        er tijdelijk een niet-DNP printer (bv. Canon CP1500) gekoppeld wordt,
        zodat er geen valse foutmeldingen verschijnen.
        """
        return bool(self._load_app_setting("printer_status_enabled", True))

    def _start_printer_status_poller(self):
        """Start (of herstart) de DNP-statuspoller, mits van toepassing."""
        self._stop_printer_status_poller()
        try:
            from dnp_status import StatusPoller
            if getattr(self, 'backend_brand', '') == 'huren':
                print("[DNP-STATUS] Poller overgeslagen — Verhuurophalen-modus")
                self._dnp_poller = None
                return
            if not self._printer_status_enabled():
                print("[DNP-STATUS] Poller overgeslagen — storingsmeldingen staan uit")
                self._dnp_poller = None
                return
            self._dnp_poller = StatusPoller(
                interval_sec=4.0,
                printer_name=config.PRINTER_NAME,
            )
            self._dnp_poller.on_change(lambda st: self._dnp_status_signal.emit(st))
            self._dnp_poller.start()
            print(f"[DNP-STATUS] Poller gestart (4s interval, "
                  f"printer={config.PRINTER_NAME!r})")
        except Exception as e:
            print(f"[DNP-STATUS] Poller niet gestart: {e}")
            self._dnp_poller = None

    def _stop_printer_status_poller(self):
        """Stop de statuspoller. stop() joint tot ~10s, dus op een bg-thread
        zodat de UI niet bevriest bij het omzetten van de schakelaar."""
        p = getattr(self, '_dnp_poller', None)
        self._dnp_poller = None
        if p is not None:
            def _quiet_stop():
                try:
                    p.stop()
                except Exception:
                    pass
            threading.Thread(target=_quiet_stop, daemon=True).start()

    def _on_printer_status_toggled(self, checked):
        """Schakelaar 'Printer-storingsmeldingen' — onthoud + poller aan/uit."""
        enabled = bool(checked)
        self._save_app_setting("printer_status_enabled", enabled)
        print(f"[SETTINGS] Printer-storingsmeldingen: {'AAN' if enabled else 'UIT'}")
        if enabled:
            self._start_printer_status_poller()
        else:
            self._stop_printer_status_poller()
            # Sluit een eventueel openstaande storings-overlay direct.
            if getattr(self, '_dnp_error_overlay', None) is not None:
                self._hide_dnp_error_overlay()

    def _on_select_printer(self):
        """Open Windows printer selection dialog and persist choice."""
        selected = select_printer_dialog(self)
        if selected:
            config.PRINTER_NAME = selected
            self._printer_name_label.setText(selected)
            self._save_app_setting("printer_name", selected)
            self._update_devmode_status()
            print(f"[SETTINGS] Printer geselecteerd: {selected}")

    def _on_configure_printer(self):
        """Open the printer driver's own preferences dialog and save DEVMODE."""
        from printer import capture_printer_devmode
        if not config.PRINTER_NAME:
            self._devmode_status_label.setText(t("select_printer_first"))
            return

        # Get native window handle for dialog parenting
        hwnd = None
        try:
            hwnd = int(self.winId())
        except Exception:
            pass

        self._devmode_status_label.setText(t("dialog_opening"))
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        ok, msg = capture_printer_devmode(config.PRINTER_NAME, hwnd=hwnd)
        if ok:
            self._devmode_status_label.setText(t("saved"))
            print(f"[SETTINGS] {msg}")
        else:
            self._devmode_status_label.setText(msg)
            print(f"[SETTINGS] Printer instellen mislukt: {msg}")

        from PyQt5.QtCore import QTimer
        QTimer.singleShot(2000, self._update_devmode_status)

    def _on_capture_dnp_profile(self, profile_key):
        """Open de DNP driver-UI en sla DEVMODE op voor een specifiek profiel.

        De gebruiker stelt in de driver-UI handmatig de juiste paper-format +
        cut-optie in voor dit profiel, klikt OK, en de bytes worden bewaard.
        Daarna kiest de software bij élke print automatisch het juiste profiel
        op basis van het template.
        """
        from printer import capture_printer_devmode, DNP_PROFILE_LABELS
        from PyQt5.QtWidgets import QMessageBox, QApplication

        if not config.PRINTER_NAME:
            QMessageBox.warning(self, "Geen printer",
                "Selecteer eerst een printer in de Printer-tab.")
            return

        # Korte instructie tonen wat de gebruiker moet kiezen
        instructions = {
            "4x6_nocut": "Stel in:\n• Papierformaat: (4x6)\n• 2inch cut: Disable\n\nKlik OK om door te gaan.",
            "4x6_cut":   "Stel in:\n• Papierformaat: (4x6)\n• 2inch cut: Enable\n\nKlik OK om door te gaan.",
            "4x3":       "Stel in:\n• Papierformaat: (4x3)\n• 2inch cut: Disable (grayed-out)\n\nKlik OK om door te gaan.",
        }
        label = DNP_PROFILE_LABELS.get(profile_key, profile_key)
        ret = QMessageBox.information(self, f"Capture: {label}",
            instructions.get(profile_key, ""),
            QMessageBox.Ok | QMessageBox.Cancel)
        if ret != QMessageBox.Ok:
            return

        hwnd = None
        try:
            hwnd = int(self.winId())
        except Exception:
            pass

        QApplication.processEvents()
        ok, msg = capture_printer_devmode(config.PRINTER_NAME, hwnd=hwnd,
                                           profile_key=profile_key)
        if ok:
            QMessageBox.information(self, "Opgeslagen",
                f"Profiel '{label}' is opgeslagen.\n\n{msg}")
            print(f"[SETTINGS] DNP profiel {profile_key} OK: {msg}")
        else:
            QMessageBox.warning(self, "Niet opgeslagen",
                f"Capture mislukt voor '{label}':\n{msg}")
            print(f"[SETTINGS] DNP profiel {profile_key} fout: {msg}")
        self._update_dnp_profile_statuses()

    def _update_dnp_profile_statuses(self):
        """Update ✓/✗ indicators voor de 3 DNP profielen (binnen open dialog)."""
        if not hasattr(self, '_dnp_profile_status_labels') or not self._dnp_profile_status_labels:
            return
        from printer import get_profile_status
        if not config.PRINTER_NAME:
            for status in self._dnp_profile_status_labels.values():
                status.setText("✗")
                status.setStyleSheet(f"color: {config.COLOR_DANGER};")
            return
        statuses = get_profile_status(config.PRINTER_NAME)
        for key, status_lbl in self._dnp_profile_status_labels.items():
            saved = statuses.get(key, False)
            if saved:
                status_lbl.setText("✓")
                status_lbl.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            else:
                status_lbl.setText("✗")
                status_lbl.setStyleSheet(f"color: {config.COLOR_DANGER};")

    def _open_dnp_profiles_dialog(self):
        """Open dialog met de 3 DNP capture-knoppen — bewust verstopt zodat
        klanten er niet per ongeluk bij komen. Alleen operators die in
        Geavanceerd → DNP printer-instellingen klikken zien dit.
        """
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from printer import DNP_PROFILE_KEYS, DNP_PROFILE_LABELS

        dlg = QDialog(self)
        dlg.setWindowTitle("DNP printer-instellingen")
        dlg.setModal(True)
        dlg.setMinimumWidth(600)
        dlg.setStyleSheet(f"QDialog {{ background: {config.COLOR_CARD_BG}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        title = QLabel("DNP printer-profielen")
        title.setFont(QFont("DM Sans", 16, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
        lay.addWidget(title)

        intro = QLabel(
            "Eenmalig per PC instellen. Klik op een knop, kies in de driver-UI "
            "het juiste papierformaat + cut-optie, klik OK. Daarna schakelt de "
            "software automatisch tussen profielen tijdens het printen."
        )
        intro.setFont(QFont("DM Sans", 11))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; background: transparent;")
        lay.addWidget(intro)
        lay.addSpacing(6)

        # Bouw de 3 rijen met status + label + capture-knop. We registreren
        # de status-labels in self._dnp_profile_status_labels zodat
        # _update_dnp_profile_statuses ze kan refreshen na elke capture.
        self._dnp_profile_status_labels = {}
        self._dnp_profile_capture_btns = {}
        for key in DNP_PROFILE_KEYS:
            row = QHBoxLayout()
            row.setSpacing(12)
            status = QLabel("✗")
            status.setFont(QFont("DM Sans", 16, QFont.Bold))
            status.setFixedWidth(28)
            status.setAlignment(Qt.AlignCenter)
            status.setStyleSheet(f"color: {config.COLOR_DANGER}; background: transparent;")
            row.addWidget(status)
            self._dnp_profile_status_labels[key] = status

            lbl = QLabel(DNP_PROFILE_LABELS[key])
            lbl.setFont(QFont("DM Sans", 12))
            lbl.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            row.addWidget(lbl, 1)

            btn = QPushButton("Capture")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", 11, QFont.Bold))
            btn.setFixedHeight(34)
            btn.setStyleSheet(
                f"QPushButton {{ background: {config.COLOR_PRIMARY}; "
                f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
                f"border-radius: 6px; padding: 4px 22px; font-size: 12px; }}"
                f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
            )
            btn.clicked.connect(lambda _checked=False, k=key: self._on_capture_dnp_profile(k))
            row.addWidget(btn)
            self._dnp_profile_capture_btns[key] = btn

            lay.addLayout(row)

        # Sluit-knop onderaan
        lay.addSpacing(10)
        close_btn = QPushButton("Sluiten")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        close_btn.setFixedHeight(36)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; "
            f"color: {config.COLOR_TEXT_ON_PRIMARY}; border: none; "
            f"border-radius: 6px; padding: 6px 24px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        close_btn.clicked.connect(dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        # Eerste status-refresh + open
        self._update_dnp_profile_statuses()
        try:
            dlg.exec_()
        finally:
            # Refs naar widgets in een gesloten dialog opruimen
            self._dnp_profile_status_labels = {}
            self._dnp_profile_capture_btns = {}

    def _build_event_limit_section(self, parent_layout, label_style):
        """Bouw de Eventlimiet-sectie binnen Printerinstellingen-tab.

        Twee states:
          STATE A "viewer":  Toon huidige status (Onbeperkt / Maximum X · Used Y · Over Z)
                             + knop Instellen + Reset (alleen als limiet > 0)
          STATE B "editor":  +/− knop voor het aantal (stappen van 10) + Opslaan/Annuleer
        """
        header_lbl = QLabel(t("event_limit_label"))
        header_lbl.setFont(QFont("DM Sans", 13, QFont.Bold))
        header_lbl.setStyleSheet(label_style)
        parent_layout.addWidget(header_lbl)

        # ── STATE A: viewer ──
        self._evlimit_view = QWidget()
        view_lay = QHBoxLayout(self._evlimit_view)
        view_lay.setContentsMargins(0, 0, 0, 0)
        view_lay.setSpacing(12)

        self._evlimit_status_label = QLabel(t("event_limit_unlimited"))
        self._evlimit_status_label.setFont(QFont("DM Sans", 12))
        self._evlimit_status_label.setStyleSheet(f"color: {config.COLOR_TEXT};")
        view_lay.addWidget(self._evlimit_status_label, 1)

        small_btn = (
            f"QPushButton {{ background: {{bg}}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 18px; "
            f"font-size: 13px; font-weight: bold; min-height: 0; min-width: 0; }}"
            f"QPushButton:hover {{ background: {{hov}}; }}"
        )
        self._evlimit_configure_btn = QPushButton(t("event_limit_configure"))
        self._evlimit_configure_btn.setCursor(Qt.PointingHandCursor)
        self._evlimit_configure_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        self._evlimit_configure_btn.setFixedHeight(36)
        self._evlimit_configure_btn.setStyleSheet(
            small_btn.replace("{bg}", config.COLOR_PRIMARY).replace("{hov}", config.COLOR_PRIMARY_HOVER)
        )
        self._evlimit_configure_btn.clicked.connect(self._evlimit_enter_editor)
        view_lay.addWidget(self._evlimit_configure_btn)

        self._evlimit_reset_btn = QPushButton(t("event_limit_reset"))
        self._evlimit_reset_btn.setCursor(Qt.PointingHandCursor)
        self._evlimit_reset_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        self._evlimit_reset_btn.setFixedHeight(36)
        self._evlimit_reset_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_DANGER}; "
            f"border: 1px solid {config.COLOR_DANGER}; border-radius: 6px; "
            f"padding: 6px 18px; font-size: 13px; min-height: 0; min-width: 0; }}"
            f"QPushButton:hover {{ background: rgba(192,57,43,0.1); }}"
        )
        self._evlimit_reset_btn.clicked.connect(self._evlimit_reset)
        view_lay.addWidget(self._evlimit_reset_btn)

        parent_layout.addWidget(self._evlimit_view)

        # ── STATE B: editor ──
        self._evlimit_editor = QWidget()
        editor_lay = QHBoxLayout(self._evlimit_editor)
        editor_lay.setContentsMargins(0, 0, 0, 0)
        editor_lay.setSpacing(12)

        # Touch-spin in stappen van 10
        self._evlimit_spin = self._make_touch_spin(
            10, 9999, 100, suffix="", step=10
        )
        editor_lay.addWidget(self._evlimit_spin)

        evlimit_save_btn = QPushButton(t("event_limit_save"))
        evlimit_save_btn.setCursor(Qt.PointingHandCursor)
        evlimit_save_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        evlimit_save_btn.setFixedHeight(36)
        evlimit_save_btn.setStyleSheet(
            small_btn.replace("{bg}", config.COLOR_SUCCESS).replace("{hov}", "#3d8a5e")
        )
        evlimit_save_btn.clicked.connect(self._evlimit_save)
        editor_lay.addWidget(evlimit_save_btn)

        evlimit_cancel_btn = QPushButton(t("event_limit_cancel"))
        evlimit_cancel_btn.setCursor(Qt.PointingHandCursor)
        evlimit_cancel_btn.setFont(QFont("DM Sans", 11))
        evlimit_cancel_btn.setFixedHeight(36)
        evlimit_cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 6px; "
            f"padding: 6px 18px; font-size: 13px; min-height: 0; min-width: 0; }}"
        )
        evlimit_cancel_btn.clicked.connect(self._evlimit_cancel)
        editor_lay.addWidget(evlimit_cancel_btn)
        editor_lay.addStretch()

        parent_layout.addWidget(self._evlimit_editor)
        self._evlimit_editor.setVisible(False)  # default: viewer mode

    def _refresh_event_limit_ui(self):
        """Update viewer-labels en knopzichtbaarheid op basis van event.event_print_quota."""
        ev = self.active_event
        if not ev or not hasattr(self, '_evlimit_status_label'):
            return
        quota = int(getattr(ev, 'event_print_quota', 0) or 0)
        used = int(getattr(ev, 'event_prints_used', 0) or 0)
        if quota <= 0:
            self._evlimit_status_label.setText(t("event_limit_unlimited"))
            self._evlimit_status_label.setStyleSheet(f"color: {config.COLOR_TEXT};")
            self._evlimit_reset_btn.setVisible(False)
        else:
            remaining = max(0, quota - used)
            color = config.COLOR_DANGER if remaining == 0 else config.COLOR_TEXT
            self._evlimit_status_label.setText(
                f"{t('event_limit_max')}: {quota}  ·  "
                f"{t('event_limit_used')}: {used}  ·  "
                f"{t('event_limit_remaining')}: {remaining}"
            )
            self._evlimit_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
            self._evlimit_reset_btn.setVisible(True)

    def _evlimit_enter_editor(self):
        """Schakel naar editor-state, pre-fill huidige waarde (of 100 default)."""
        ev = self.active_event
        if not ev:
            return
        current = int(getattr(ev, 'event_print_quota', 0) or 0)
        if current <= 0:
            current = 100
        self._touch_spin_set(self._evlimit_spin, current)
        self._evlimit_view.setVisible(False)
        self._evlimit_editor.setVisible(True)

    def _evlimit_cancel(self):
        self._evlimit_editor.setVisible(False)
        self._evlimit_view.setVisible(True)

    def _evlimit_save(self):
        """Sla het ingestelde quotum op (event-specifiek, niet booth-wide)."""
        ev = self.active_event
        if not ev:
            return
        new_quota = int(getattr(self._evlimit_spin, "_value", 100) or 100)
        ev.event_print_quota = new_quota
        ev.save(config.EVENTS_DIR)
        print(f"[PRINT-QUOTA] Eventlimiet ingesteld: {new_quota}")
        self._evlimit_editor.setVisible(False)
        self._evlimit_view.setVisible(True)
        self._refresh_event_limit_ui()

    def _evlimit_reset(self):
        """Reset limiet (terug naar onbeperkt) + teller naar 0."""
        ev = self.active_event
        if not ev:
            return
        from PyQt5.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, t("event_limit_reset"), t("event_limit_reset_confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return
        ev.event_print_quota = 0
        ev.event_prints_used = 0
        ev.save(config.EVENTS_DIR)
        print("[PRINT-QUOTA] Eventlimiet gereset naar onbeperkt")
        self._refresh_event_limit_ui()

    def _on_test_print(self):
        """Generate a test image and send it to the configured printer."""
        if not config.PRINTER_NAME:
            print("[SETTINGS] Test print: geen printer geselecteerd")
            return

        from PIL import Image, ImageDraw, ImageFont
        from datetime import datetime

        test_path = os.path.join(config.DATA_DIR, "test_print.png")
        try:
            img = Image.new("RGB", (1200, 1800), "white")
            draw = ImageDraw.Draw(img)
            draw.rectangle([(20, 20), (1179, 1779)], outline="black", width=4)

            try:
                title_font = ImageFont.truetype("arial.ttf", 90)
                body_font = ImageFont.truetype("arial.ttf", 40)
            except Exception:
                title_font = ImageFont.load_default()
                body_font = ImageFont.load_default()

            draw.text((600, 180), "TEST PRINT", fill="black", anchor="mm", font=title_font)
            draw.text((600, 280), config.PRINTER_NAME, fill="gray", anchor="mm", font=body_font)
            draw.text((600, 340), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      fill="gray", anchor="mm", font=body_font)

            colors = [
                (255, 0, 0), (0, 180, 0), (0, 0, 255),
                (255, 220, 0), (255, 0, 255), (0, 200, 200), (0, 0, 0),
            ]
            bar_y, bar_h = 520, 220
            bar_w = 1000 // len(colors)
            for i, color in enumerate(colors):
                x0 = 100 + i * bar_w
                draw.rectangle([(x0, bar_y), (x0 + bar_w, bar_y + bar_h)], fill=color)

            grad_y, grad_h = 900, 120
            for i in range(1000):
                gray = int(255 * i / 999)
                draw.line([(100 + i, grad_y), (100 + i, grad_y + grad_h)],
                          fill=(gray, gray, gray))

            for i in range(1000):
                r = int(255 * i / 999)
                draw.line([(100 + i, grad_y + grad_h + 40),
                           (100 + i, grad_y + grad_h + 40 + grad_h)],
                          fill=(r, 0, 0))

            draw.text((600, 1700), "Bootharoo Photobooth", fill="black",
                      anchor="mm", font=body_font)

            img.save(test_path, "PNG")
        except Exception as e:
            print(f"[SETTINGS] Test print image fout: {e}")
            return

        # Test print: gebruik het profiel passend bij huidige printer-modus.
        # _resolve_dnp_profile_key(None) gaf altijd None (legacy pad) —
        # de test kon de vastgelegde 4x6_cut/4x3 profielen dus nooit
        # valideren. Map nu direct vanaf de booth printer_mode.
        from printer import PROFILE_4X6_CUT, PROFILE_4X6_NOCUT, PROFILE_4X3
        mode = getattr(self.active_event, 'printer_mode', '3strips') \
            if self.active_event else '3strips'
        test_profile = {
            '3strips': PROFILE_4X6_CUT,
            '4x6': PROFILE_4X6_NOCUT,
            '4x3': PROFILE_4X3,
        }.get(mode)
        self._test_print_thread = SubprocessPrintThread(
            test_path, config.PRINTER_NAME, 1, profile_key=test_profile,
            skip_status_check=not self._printer_status_enabled())
        self._test_print_thread.print_failed.connect(
            lambda msg: self._devmode_status_label.setText(f"⚠ Test mislukt: {msg[:80]}")
            if hasattr(self, '_devmode_status_label') else None
        )
        self._test_print_thread.print_complete.connect(
            lambda: self._devmode_status_label.setText("✓ Test print verstuurd")
            if hasattr(self, '_devmode_status_label') else None
        )
        self._test_print_thread.start()
        print(f"[SETTINGS] Test print naar: {config.PRINTER_NAME} (profiel: {test_profile or 'legacy'})")

    def _update_devmode_status(self):
        """Update the DEVMODE status label."""
        from printer import has_saved_devmode
        if not config.PRINTER_NAME:
            self._devmode_status_label.setText(t("no_printer"))
            return
        if has_saved_devmode(config.PRINTER_NAME):
            self._devmode_status_label.setText(t("driver_configured"))
            self._devmode_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        else:
            self._devmode_status_label.setText(t("driver_not_configured"))
            self._devmode_status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")

    def _on_order_paper(self):
        """Open the order paper URL in the default browser."""
        import webbrowser
        webbrowser.open("https://clixibo.nl/printpapier")

    def _on_cut_toggled(self, checked):
        """Toggle cut/split mode and auto-save."""
        if self.active_event:
            self.active_event.cut_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Snijden: {'aan' if checked else 'uit'}")

    def _on_serial_changed(self):
        """Serienummer opgeslagen (booth-wide). Alfanumeriek, getrimd."""
        if not hasattr(self, '_serial_input'):
            return
        val = self._serial_input.text().strip()
        # Booth-wide persist — werkt ook zonder actief event.
        try:
            from booth_settings import BoothSettings as _BS
            bs = _BS.load() if _BS.exists() else _BS()
            bs.serial_number = val
            bs.save()
        except Exception as ex:
            print(f"[SETTINGS] Serienummer booth-wide opslaan mislukt: {ex}")
        if self.active_event:
            self.active_event.serial_number = val
            self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Serienummer: {val!r}")
        self._update_log_context()
        # Ververs de zichtbare weergaves (Geavanceerd-footer + welcome-scherm;
        # het idle-scherm pakt 'm op bij de volgende _go_idle).
        if hasattr(self, '_adv_serial_footer'):
            self._adv_serial_footer.setText(f"Serienummer: {val}" if val else "")
        if hasattr(self, '_welcome_serial_label'):
            self._refresh_welcome_serial()

    @property
    def serial_number(self) -> str:
        """Serienummer van deze booth (event of booth-wide cache)."""
        ev = self.active_event
        if ev and getattr(ev, 'serial_number', ''):
            return ev.serial_number
        try:
            from booth_settings import BoothSettings as _BS
            if _BS.exists():
                return _BS.load().serial_number or ""
        except Exception:
            pass
        return ""

    def _update_log_context(self):
        """Sync de cloud-log context: serienummer + gekoppeld event + klant
        + brand. Aangeroepen bij startup en bij elke relevante wijziging."""
        try:
            import log_uploader
            ev = self.active_event
            log_uploader.update_context(
                serial=self.serial_number,
                event_id=getattr(ev, 'linked_booking_id', '') if ev else '',
                customer=getattr(ev, 'linked_booking_label', '') if ev else '',
                brand=self.backend_brand,
                token=getattr(ev, 'linked_token', '') if ev else '',
            )
        except Exception as e:
            print(f"[LOG-UPLOAD] Context-update mislukt: {e}")

    # ── Auto-updater ─────────────────────────────────────────────────
    def _on_check_updates(self):
        """Check GitHub op een nieuwere release (op een bg-thread)."""
        self._update_check_btn.setEnabled(False)
        self._update_install_btn.setVisible(False)
        self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        self._update_status_lbl.setText("Bezig met controleren…")

        def _bg():
            try:
                import updater
                res = updater.check_for_update()
            except Exception as e:
                res = {"error": str(e)}
            self._update_check_signal.emit(res)
        threading.Thread(target=_bg, daemon=True).start()

    def _on_update_check_result(self, res):
        """Main thread: toon het check-resultaat."""
        self._update_check_btn.setEnabled(True)
        self._pending_update = res
        if not isinstance(res, dict) or res.get("error"):
            err = res.get("error", "onbekend") if isinstance(res, dict) else "onbekend"
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_DANGER};")
            self._update_status_lbl.setText(f"Controle mislukt: {err}")
            return
        if res.get("newer") and res.get("url"):
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self._update_status_lbl.setText(
                f"Nieuwe versie beschikbaar: {res.get('latest','')}  "
                f"(je hebt {res.get('current','')})")
            self._update_install_btn.setText(f"Nu updaten naar {res.get('latest','')}")
            self._update_install_btn.setVisible(True)
        elif res.get("newer") and not res.get("url"):
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_DANGER};")
            self._update_status_lbl.setText(
                f"Versie {res.get('latest','')} gevonden, maar geen installer "
                f"in die release.")
        else:
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            self._update_status_lbl.setText("Je hebt al de nieuwste versie.")

    def _on_do_update(self):
        """Download de installer + start 'm. De app sluit zichzelf via de
        installer en herstart na de update."""
        res = self._pending_update or {}
        url = res.get("url", "")
        if not url:
            return
        from PyQt5.QtWidgets import QMessageBox
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Updaten")
        confirm.setText(
            f"De photobooth wordt nu bijgewerkt naar {res.get('latest','')}.\n\n"
            "De software sluit even af en start daarna automatisch opnieuw op. "
            "Doorgaan?")
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm.setDefaultButton(QMessageBox.Yes)
        if confirm.exec_() != QMessageBox.Yes:
            return

        self._update_install_btn.setVisible(False)
        self._update_check_btn.setEnabled(False)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(True)
        self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_TEXT};")
        self._update_status_lbl.setText("Bezig met downloaden…")

        def _bg():
            try:
                import updater
                path = updater.download_installer(
                    url, progress_cb=lambda p: self._update_progress_signal.emit(p))
                if not path:
                    self._update_done_signal.emit(False, "download mislukt")
                    return
                ok = updater.run_installer(path)
                self._update_done_signal.emit(ok, "" if ok else "installer kon niet starten")
            except Exception as e:
                self._update_done_signal.emit(False, str(e))
        threading.Thread(target=_bg, daemon=True).start()

    def _on_update_progress(self, pct):
        self._update_progress.setValue(int(pct))
        if pct >= 100:
            self._update_status_lbl.setText("Download klaar — installer wordt gestart…")

    def _on_update_done(self, ok, err):
        if ok:
            # Installer draait nu; hij sluit de app zo af + herstart.
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            self._update_status_lbl.setText(
                "Update gestart — de software wordt nu bijgewerkt en herstart.")
        else:
            self._update_progress.setVisible(False)
            self._update_check_btn.setEnabled(True)
            self._update_install_btn.setVisible(True)
            self._update_status_lbl.setStyleSheet(f"color: {config.COLOR_DANGER};")
            self._update_status_lbl.setText(f"Update mislukt: {err}")

    def _build_status_snapshot(self) -> dict:
        """Bouw een rijke statussnapshot van de booth: wat er op het scherm
        gebeurt, prints, en de verbindingen (camera, COB-relay, printer,
        internet, uploads). Draait op de main thread (Qt-state veilig)."""
        import time as _t
        ev = self.active_event
        snap = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": getattr(config, "VERSION", "?"),
            "serial": self.serial_number,
            "brand": self.backend_brand,
        }
        # ── Scherm / state (wat is er nu bezig) ──
        try:
            st = getattr(self, "state", None)
            snap["state"] = st.name if st is not None else "?"
        except Exception:
            snap["state"] = "?"
        try:
            idx = self.stack.currentIndex()
            snap["screen"] = next((k for k, v in self.pages.items() if v == idx),
                                  str(idx))
        except Exception:
            snap["screen"] = "?"
        # ── Camera ──
        try:
            snap["camera_connected"] = bool(self.camera.is_connected())
        except Exception:
            snap["camera_connected"] = False
        snap["camera_ready"] = bool(getattr(self, "_digicam_ready", False))
        try:
            snap["camera_type"] = type(self.camera).__name__
        except Exception:
            snap["camera_type"] = "?"
        snap["camera_mode"] = getattr(ev, "camera_mode", "?") if ev else "?"
        # ── COB / LED-relay ──
        try:
            snap["led_relay_connected"] = bool(self.led.available) if self.led else False
        except Exception:
            snap["led_relay_connected"] = False
        # ── Internet ──
        snap["online"] = bool(getattr(self, "_has_internet", False))
        # ── Printer ──
        snap["printer_name"] = getattr(config, "PRINTER_NAME", "")
        try:
            snap["print_enabled"] = bool(self.effective_print_enabled)
        except Exception:
            snap["print_enabled"] = None
        dnp = getattr(self, "_dnp_last_status", None)
        if dnp is not None:
            try:
                snap["printer_connected"] = bool(dnp.connected)
                snap["printer_level"] = getattr(dnp.level, "value", str(dnp.level))
                snap["printer_code"] = dnp.code
                snap["printer_label"] = dnp.label
                snap["printer_media"] = dnp.media
                snap["prints_remaining_roll"] = dnp.prints_remaining
                snap["prints_total_roll"] = dnp.prints_total
                snap["printer_serial"] = dnp.serial
                snap["printer_firmware"] = dnp.firmware
                snap["printer_error_method"] = dnp.error_method
            except Exception:
                pass
        else:
            # Huren-modus: HiTi zonder DNP-statuspoller
            snap["printer_connected"] = None
            snap["printer_label"] = ("HiTi (geen statuspoller)"
                                     if self.backend_brand == "huren" else "onbekend")
        # ── Gekoppeld event / klant ──
        if ev:
            snap["event_id"] = getattr(ev, "linked_booking_id", "") or ""
            snap["event_label"] = getattr(ev, "linked_booking_label", "") or ""
            snap["booth_mode"] = getattr(ev, "booth_mode", "") or ""
            snap["package"] = getattr(ev, "linked_package", "") or ""
        # ── Prints / quota / tellers ──
        snap["session_prints_used"] = int(getattr(self, "_session_prints_used", 0) or 0)
        if ev:
            snap["max_prints"] = getattr(ev, "max_prints", None)
            snap["auto_print_copies"] = getattr(ev, "auto_print_copies", None)
            snap["session_count"] = getattr(ev, "session_count", None)
            snap["photo_count"] = getattr(ev, "photo_count", None)
            quota = int(getattr(ev, "event_print_quota", 0) or 0)
            used = int(getattr(ev, "event_prints_used", 0) or 0)
            snap["event_print_quota"] = quota
            snap["event_prints_used"] = used
            if quota > 0:
                snap["event_prints_remaining"] = max(0, quota - used)
            # Print-venster t.o.v. de event-datum
            snap["event_date"] = getattr(ev, "linked_event_date", "") or ""
            phase = self._print_phase(ev)
            snap["print_phase"] = phase  # test / open / closed / none
            snap["before_event_date"] = (phase == 'test')
            if phase == 'test':
                tlimit = int(getattr(config, "TEST_PRINT_LIMIT", 10))
                tused = int(getattr(ev, "test_prints_used", 0) or 0)
                snap["test_prints_used"] = tused
                snap["test_prints_remaining"] = max(0, tlimit - tused)
        # ── Uploads (cloud-foto's) ──
        try:
            bid = getattr(ev, "linked_booking_id", "") if ev else ""
            if bid:
                from cloud_uploader import get_status as _us
                u = _us(bid)
                snap["uploads"] = {
                    "total": u.get("total", 0), "uploaded": u.get("uploaded", 0),
                    "pending": u.get("pending", 0), "failed": u.get("failed", 0),
                }
        except Exception:
            pass
        # ── Schijf ──
        try:
            import shutil
            free = shutil.disk_usage(config.PHOTO_DIR).free / (1024 ** 3)
            snap["disk_free_gb"] = round(free, 1)
        except Exception:
            pass
        # ── Uptime ──
        try:
            snap["uptime_sec"] = int(_t.time() - getattr(self, "_app_start_ts", _t.time()))
        except Exception:
            pass
        return snap

    def _push_status_snapshot(self):
        """Bouw de snapshot (main thread) en geef 'm aan de uploader. De
        eerstvolgende flush (~20s) stuurt 'm mee als heartbeat."""
        try:
            import log_uploader
            log_uploader.update_status(self._build_status_snapshot())
        except Exception as e:
            print(f"[STATUS] Snapshot mislukt: {e}")

    def _sync_brand_radios(self):
        """Backend-brand radio's + HiTi-rij syncen met de actuele waarde
        (event of booth-wide via self.backend_brand). blockSignals zodat
        het syncen zelf geen save-loop triggert."""
        if not hasattr(self, '_brand_hippe_radio'):
            print("[SETTINGS] Brand-radio sync overgeslagen (widgets nog niet gebouwd)")
            return
        brand = self.backend_brand
        print(f"[SETTINGS] Brand-radio sync: {brand} "
              f"(event={'ja' if self.active_event else 'nee'})")
        for rb in (self._brand_hippe_radio, self._brand_huren_radio):
            rb.blockSignals(True)
        self._brand_huren_radio.setChecked(brand == 'huren')
        self._brand_hippe_radio.setChecked(brand != 'huren')
        for rb in (self._brand_hippe_radio, self._brand_huren_radio):
            rb.blockSignals(False)
        if hasattr(self, '_brand_hiti_row'):
            self._brand_hiti_row.setVisible(brand == 'huren')

    def _on_backend_brand_changed(self, brand: str):
        """Backend-switch in Geavanceerd: 'hippe' of 'huren'.

        Huren-modus: DNP-poller pauzeren + fout-overlay sluiten (HiTi
        heeft geen statusbewaking) en HiTi-knoppen tonen. Terug naar
        hippe: poller hervat bij het verlaten van settings. De waarde
        propageert booth-wide via Event.save.
        """
        # Booth-wide persist — óók wanneer er (nog) geen actief event
        # gekoppeld is (welcome-page). Zonder dit ging de keuze verloren
        # bij herstart zolang er geen event was om via te propageren.
        self._booth_brand_cache = brand
        try:
            from booth_settings import BoothSettings as _BS
            bs = _BS.load() if _BS.exists() else _BS()
            bs.backend_brand = brand
            if brand != 'huren':
                # Canon (dslr) is huren-only — terug naar hippe betekent
                # terug naar webcam, zodat hippe-gedrag onaangetast blijft.
                bs.camera_mode = "webcam"
            bs.save()
            print(f"[SETTINGS] Backend-brand booth-wide opgeslagen: {brand}")
        except Exception as ex:
            print(f"[SETTINGS] Backend-brand booth-wide opslaan mislukt: {ex}")
        if self.active_event:
            self.active_event.backend_brand = brand
            if brand != 'huren' and self.active_event.camera_mode != "webcam":
                self.active_event.camera_mode = "webcam"
                print("[SETTINGS] Camera terug naar webcam (hippe-brand)")
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Backend-brand: {brand}")
            self._update_webcam_status()
        self._update_log_context()
        if hasattr(self, '_brand_hiti_row'):
            self._brand_hiti_row.setVisible(brand == 'huren')
        if brand == 'huren':
            self._pause_dnp_poll(True)
            try:
                self._hide_dnp_error_overlay()
            except Exception:
                pass

    def _on_print_enabled_toggled(self, checked):
        """Toggle printing on/off and show/hide printer settings."""
        if self.active_event:
            self.active_event.print_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Printen: {'aan' if checked else 'uit'}")
        self._update_printer_visibility()
        # Printen uit → poller direct pauzeren + eventuele fout-overlay
        # weg (er valt niks meer te bewaken). Weer aan → poller hervat
        # zodra we settings verlaten (_go_idle → _pause_dnp_poll(False)).
        if not checked:
            self._pause_dnp_poll(True)
            try:
                self._hide_dnp_error_overlay()
            except Exception:
                pass

    def _on_auto_print_toggled(self, checked):
        """Toggle auto-print and auto-save."""
        if self.active_event:
            self.active_event.auto_print = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Auto-print: {'aan' if checked else 'uit'}")
        self._update_printer_visibility()
        # Enforce max_prints >= auto_copies when auto-print turns on
        if checked:
            self._enforce_max_prints_minimum()

    def _on_auto_copies_changed(self, value):
        """Change auto-print copies and auto-save."""
        if self.active_event:
            self.active_event.auto_print_copies = value
            self.active_event.print_copies = value  # Keep legacy field in sync
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Auto-print kopieën: {value}")
        # Enforce max_prints >= auto_copies
        self._enforce_max_prints_minimum()

    def _on_max_prints_changed(self, value):
        """Change max prints per session and auto-save."""
        if self.active_event:
            self.active_event.max_prints = value
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Max prints: {value}")

    def _on_extra_prints_changed(self, value):
        """Change extra prints allowed (when auto-print ON) and auto-save."""
        if self.active_event:
            self.active_event.extra_prints_allowed = value
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Extra prints toegestaan: {value}")

    def _update_printer_visibility(self):
        """Show/hide printer sub-settings based on toggles."""
        print_on = self._print_enabled_toggle.isChecked()
        auto_on = self._auto_print_toggle.isChecked()
        self._printer_settings_container.setVisible(print_on)
        # Verhuur: de Printerinstellingen-kaart (auto-print, aantal prints,
        # extra prints, eventlimiet) blijft ALTIJD verborgen — die waarden
        # worden hardcoded geforceerd (1 print, 0 extra, onbeperkt), dus de
        # controls deden niets. Eerder zette deze regel 'm op print_on en
        # overschreef daarmee de bewuste verberging bij het opbouwen.
        if hasattr(self, '_print_settings_card'):
            self._print_settings_card.setVisible(False)
        self._auto_copies_container.setVisible(auto_on)
        # Show extra prints row when auto-print ON, max prints row when OFF
        self._extra_prints_row.setVisible(auto_on)
        self._max_prints_row.setVisible(not auto_on)

    def _enforce_max_prints_minimum(self):
        """Ensure max_prints >= auto_print_copies when auto-print is on."""
        if not self._auto_print_toggle.isChecked():
            return
        auto_copies = self._auto_copies_spin._value
        max_prints = self._max_prints_spin._value
        if max_prints < auto_copies:
            self._touch_spin_set(self._max_prints_spin, auto_copies)
            if self.active_event:
                self.active_event.max_prints = auto_copies
                self.active_event.save(config.EVENTS_DIR)

    def _on_qr_toggled(self, checked):
        """Toggle QR code / online gallery and auto-save.
        Branding sub-options zijn alleen zichtbaar als QR aan staat.
        """
        if self.active_event:
            self.active_event.gallery_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] QR-code: {'aan' if checked else 'uit'}")
        # Toon/verberg branding container
        if hasattr(self, '_qr_branding_container'):
            self._qr_branding_container.setVisible(bool(checked))

    def _on_qr_branding_toggled(self, checked):
        """Toggle bedrijfsgegevens tonen onderaan QR-gallery."""
        ev = self.active_event
        if ev:
            ev.qr_branding_enabled = bool(checked)
            ev.save(config.EVENTS_DIR)
            print(f"[SETTINGS] QR-branding: {'aan' if checked else 'uit'}")
        # Tekst-veld alleen tonen als toggle aan staat
        if hasattr(self, '_qr_branding_text'):
            self._qr_branding_text.setVisible(bool(checked))

    def _on_qr_branding_text_changed(self):
        """Save de bedrijfsgegevens tekst bij elke wijziging."""
        ev = self.active_event
        if not ev or not hasattr(self, '_qr_branding_text'):
            return
        ev.qr_branding_text = self._qr_branding_text.toPlainText()
        # Auto-save: schrijf naar event JSON + propageert naar booth_settings.
        # Bij snel typen kan dit veel saves geven — acceptabel voor settings.
        ev.save(config.EVENTS_DIR)

    def _on_email_toggled(self, checked):
        """Toggle email sharing and auto-save."""
        if self.active_event:
            self.active_event.email_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] E-mail: {'aan' if checked else 'uit'}")
        self._update_email_visibility()

    def _on_payment_toggled(self, checked):
        """Toggle payment mode."""
        if not checked:
            # Turning off — just save
            if self.active_event:
                self.active_event.payment_enabled = False
                self.active_event.save(config.EVENTS_DIR)
                print("[SETTINGS] Betaalmodus: uit")
            self._update_payment_info()
            return

        # Turning on — check if payment is configured
        # First try local session
        user, _ = auth.load_session()
        payment_url = user.get("payment_link_url", "") if user else ""
        booth_secret = user.get("booth_secret", "") if user else ""

        if payment_url and booth_secret:
            # Already configured locally — enable immediately
            if self.active_event:
                self.active_event.payment_enabled = True
                self.active_event.save(config.EVENTS_DIR)
                print("[SETTINGS] Betaalmodus: aan")
            self._update_payment_info()
            return

        # Not configured locally — show dialog and check online
        self._payment_toggle.blockSignals(True)
        self._payment_toggle.setChecked(False)
        self._payment_toggle.blockSignals(False)

        from PyQt5.QtWidgets import QDialog, QProgressBar
        dialog = QDialog(self)
        dialog.setWindowTitle(t("payment_not_setup_title"))
        dialog.setFixedSize(380, 160)
        dialog.setStyleSheet(f"QDialog {{ background: {config.COLOR_BG}; }}")
        d_lay = QVBoxLayout(dialog)
        d_lay.setSpacing(12)
        d_lay.setContentsMargins(24, 20, 24, 20)

        status_lbl = QLabel(t("payment_fetching"))
        status_lbl.setFont(QFont("DM Sans", 12))
        status_lbl.setAlignment(Qt.AlignCenter)
        d_lay.addWidget(status_lbl)

        progress = QProgressBar()
        progress.setRange(0, 0)  # Indeterminate spinner
        progress.setFixedHeight(6)
        progress.setStyleSheet(
            f"QProgressBar {{ background: {config.COLOR_BORDER}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {config.COLOR_PRIMARY}; border-radius: 3px; }}"
        )
        d_lay.addWidget(progress)

        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setFont(QFont("DM Sans", 11))
        cancel_btn.setFixedHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_BORDER}; color: {config.COLOR_TEXT}; "
            f"border: none; border-radius: 8px; padding: 6px 20px; }}"
            f"QPushButton:hover {{ background: #cccccc; }}"
        )
        cancel_btn.clicked.connect(dialog.reject)
        d_lay.addWidget(cancel_btn, alignment=Qt.AlignCenter)

        # Run check with a timeout timer instead of thread
        # This avoids QTimer.singleShot cross-thread issues
        self._payment_dialog_result = None
        self._payment_check_done = False

        import threading
        def _do_online_check():
            try:
                success, updated, _ = auth.verify_session_online()
                self._payment_dialog_result = (success, updated)
            except Exception:
                self._payment_dialog_result = (False, None)
            self._payment_check_done = True

        threading.Thread(target=_do_online_check, daemon=True).start()

        # Poll for result every 200ms (avoids cross-thread signal issues)
        poll_timer = QTimer(dialog)
        def _poll():
            if self._payment_check_done:
                poll_timer.stop()
                self._finish_payment_dialog(dialog, status_lbl, progress)
        poll_timer.timeout.connect(_poll)
        poll_timer.start(200)

        # Auto-timeout after 15 seconds
        QTimer.singleShot(15000, lambda: dialog.reject() if dialog.isVisible() and not self._payment_check_done else None)

        dialog.exec_()

    def _finish_payment_dialog(self, dialog, status_lbl, progress):
        """Handle result of online payment check."""
        if not dialog.isVisible():
            return  # User cancelled

        success, updated_user = self._payment_dialog_result or (False, None)
        user = updated_user if updated_user else None
        if not user:
            user, _ = auth.load_session()

        payment_url = user.get("payment_link_url", "") if user else ""
        booth_secret = user.get("booth_secret", "") if user else ""

        progress.hide()

        if payment_url and booth_secret:
            # Success!
            status_lbl.setText("✅ " + t("payment_configured"))
            status_lbl.setStyleSheet(f"color: {config.COLOR_SUCCESS}; font-size: 12px;")
            if self.active_event:
                self.active_event.payment_enabled = True
                self.active_event.save(config.EVENTS_DIR)
                print("[SETTINGS] Betaalmodus: aan")
            self._payment_toggle.blockSignals(True)
            self._payment_toggle.setChecked(True)
            self._payment_toggle.blockSignals(False)
            self._update_payment_info()
            QTimer.singleShot(1500, dialog.accept)
        else:
            # Not configured
            status_lbl.setText("❌ " + t("payment_not_configured"))
            status_lbl.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 11px;")
            # Change cancel button to "OK"
            for child in dialog.findChildren(QPushButton):
                child.setText("OK")
                child.clicked.disconnect()
                child.clicked.connect(dialog.accept)

    def _update_payment_info(self):
        """Update payment card UI with info from cloud."""
        if not hasattr(self, '_payment_status_label'):
            return
        user, _ = auth.load_session()
        booth_secret = user.get("booth_secret", "") if user else ""
        payment_url = user.get("payment_link_url", "") if user else ""
        if booth_secret and payment_url:
            self._payment_status_label.setText("\u2705 " + t("payment_configured"))
            self._payment_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS}; font-size: 11px;")
        else:
            self._payment_status_label.setText("\u274c " + t("payment_not_configured"))
            self._payment_status_label.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 11px;")
        # Load payment screen text
        ev = self.active_event
        if ev and hasattr(self, '_payment_screen_text'):
            text = getattr(ev, 'payment_screen_text', t("payment_scan_default"))
            self._payment_screen_text.blockSignals(True)
            self._payment_screen_text.setText(text)
            self._payment_screen_text.blockSignals(False)
        # Load payment bg
        if ev and hasattr(self, '_payment_bg_label'):
            bg = getattr(ev, 'payment_bg_path', '')
            if bg and os.path.isfile(bg):
                self._payment_bg_label.setText(os.path.basename(bg))
            else:
                self._payment_bg_label.setText(t("payment_bg_default"))

    def _on_payment_text_changed(self, text):
        """Save payment screen text to event."""
        if self.active_event:
            self.active_event.payment_screen_text = text
            self.active_event.save(config.EVENTS_DIR)

    def _on_payment_bg_change(self):
        """Choose custom background for payment idle screen."""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, t("payment_bg_choose_title"),
            os.path.expanduser("~"),
            "Afbeeldingen (*.png *.jpg *.jpeg)"
        )
        if path and self.active_event:
            self.active_event.payment_bg_path = path
            self.active_event.save(config.EVENTS_DIR)
            self._payment_bg_label.setText(os.path.basename(path))
            print(f"[PAYMENT] Achtergrond: {path}")

    def _start_payment_polling(self):
        """Start polling for pending payment sessions."""
        if hasattr(self, '_payment_poll_timer') and self._payment_poll_timer.isActive():
            return  # Already polling
        self._payment_poll_timer = QTimer(self)
        self._payment_poll_timer.timeout.connect(self._poll_payment_sessions)
        self._payment_poll_timer.start(3000)  # Poll every 3 seconds
        self._payment_queue = 0
        print("[PAYMENT] Polling gestart (elke 3 sec)")

    def _stop_payment_polling(self):
        """Stop payment polling."""
        if hasattr(self, '_payment_poll_timer'):
            self._payment_poll_timer.stop()
            print("[PAYMENT] Polling gestopt")

    def _start_sumup_always_on(self):
        """Start the always-on SumUp payment loop."""
        if getattr(self, '_sumup_always_on_running', False):
            return
        self._sumup_always_on_running = True
        self._sumup_active_checkout = None
        self._sumup_client_tx_id = None
        import threading
        threading.Thread(target=self._sumup_always_on_loop, daemon=True).start()
        print("[SUMUP] Always-on loop gestart")

    def _stop_sumup_always_on(self):
        """Stop the always-on SumUp payment loop."""
        self._sumup_always_on_running = False
        self._sumup_active_checkout = None

    def _sumup_always_on_loop(self):
        """Background loop: always keep a checkout on the Solo.

        Uses two detection methods:
        1. Poll specific checkout ID status (fast for that checkout)
        2. Poll transaction history for ANY successful payment (catches all)
        """
        import time
        from datetime import datetime, timezone
        from sumup_payment import (load_sumup_config, create_checkout,
                                   check_checkout, check_recent_successful_payments,
                                   _get_reader_id)

        cfg = load_sumup_config()
        if not cfg:
            print("[SUMUP] Niet geconfigureerd")
            self._sumup_always_on_running = False
            return

        api_key = cfg["api_key"]
        merchant = cfg["merchant_code"]
        amount = cfg["amount"]
        currency = cfg.get("currency", "EUR")
        reader_id = cfg.get("reader_id", "") or _get_reader_id(api_key, merchant) or ""

        if not reader_id:
            print("[SUMUP] Geen reader gevonden")
            self._sumup_always_on_running = False
            return

        # Track which transactions we've already processed
        seen_tx_ids = set()
        # Seed with recent history so we don't trigger on old payments
        existing = check_recent_successful_payments(api_key)
        seen_tx_ids.update(existing)
        # Track timestamp for filtering
        loop_start = datetime.now(timezone.utc).isoformat()

        while self._sumup_always_on_running:
            # Only create checkouts when IDLE
            if self.state != State.IDLE:
                time.sleep(1)
                continue

            # Create checkout and send to Solo
            checkout_id, client_tx_id = create_checkout(
                api_key, merchant, amount, currency, "Photobooth", reader_id
            )

            if not checkout_id:
                if client_tx_id == "READER_BUSY":
                    # Solo still processing previous — check for payments, retry fast
                    new_txs = check_recent_successful_payments(api_key, loop_start)
                    unseen = [tx for tx in new_txs if tx not in seen_tx_ids]
                    if unseen:
                        for tx_id in unseen:
                            seen_tx_ids.add(tx_id)
                            print(f"[SUMUP] Betaling gevonden tijdens BUSY: {tx_id[:12]}")
                            self._sumup_status_signal.emit(t("payment_success"))
                            self._sumup_payment_signal.emit()
                        while self._sumup_always_on_running and self.state != State.IDLE:
                            time.sleep(1)
                        time.sleep(1)
                        continue
                    time.sleep(2)
                    continue
                # Other error — wait and retry
                self._sumup_status_signal.emit("Fout — opnieuw proberen...")
                time.sleep(5)
                continue

            self._sumup_active_checkout = checkout_id
            self._sumup_client_tx_id = client_tx_id
            self._sumup_status_signal.emit(t("waiting_for_payment"))

            # Poll for payment
            poll_start = time.time()
            MAX_POLL = 540  # 9 minutes

            while self._sumup_always_on_running:
                if self.state != State.IDLE:
                    time.sleep(1)
                    continue

                if time.time() - poll_start > MAX_POLL:
                    print("[SUMUP] Checkout verlopen — vernieuwt")
                    break

                # Method 1: Check specific checkout
                status = check_checkout(api_key, checkout_id, client_tx_id)

                if status == "PAID":
                    seen_tx_ids.add(client_tx_id or checkout_id)
                    print("[SUMUP] Betaling ontvangen!")
                    self._sumup_active_checkout = None
                    self._sumup_status_signal.emit(t("payment_success"))
                    self._sumup_payment_signal.emit()
                    while self._sumup_always_on_running and self.state != State.IDLE:
                        time.sleep(1)
                    time.sleep(1)
                    break

                elif status in ("FAILED", "EXPIRED"):
                    self._sumup_active_checkout = None
                    break  # Silently create new checkout

                # Method 2: Check ALL recent successful payments
                new_txs = check_recent_successful_payments(api_key, loop_start)
                unseen = [tx for tx in new_txs if tx not in seen_tx_ids]
                if unseen:
                    for tx_id in unseen:
                        seen_tx_ids.add(tx_id)
                    print(f"[SUMUP] {len(unseen)} nieuwe betaling(en) via history!")
                    self._sumup_active_checkout = None
                    self._sumup_status_signal.emit(t("payment_success"))
                    self._sumup_payment_signal.emit()
                    # Queue extra sessions if multiple payments
                    if len(unseen) > 1:
                        self._payment_queue = getattr(self, '_payment_queue', 0) + len(unseen) - 1
                    while self._sumup_always_on_running and self.state != State.IDLE:
                        time.sleep(1)
                    time.sleep(1)
                    break

                time.sleep(1)

        print("[SUMUP] Always-on loop gestopt")
        self._sumup_always_on_running = False

    def _sumup_auto_start_session(self):
        """Start photo session after SumUp payment."""
        if self.state == State.IDLE:
            self._go_select_template()
        else:
            self._payment_queue = getattr(self, '_payment_queue', 0) + 1
            print(f"[SUMUP] In wachtrij ({self._payment_queue})")

    def _sumup_update_idle(self, text):
        """Update the SumUp status label on the idle screen."""
        if hasattr(self, '_sumup_idle_status'):
            self._sumup_idle_status.setText(text)

    def _sumup_start_payment(self):
        """Legacy — not used in always-on mode."""
        if self.state != State.IDLE:
            return
        if getattr(self, '_sumup_active_checkout', None):
            return
        self._sumup_cancelled = False
        from sumup_payment import load_sumup_config
        cfg = load_sumup_config()
        if not cfg:
            self._show_error(t("error_sumup_not_configured"))
            return

        self.state = State.PAYMENT  # Custom state for payment flow
        self._go_fullscreen()

        # Show spinner overlay on top of idle page
        self._sumup_overlay = QWidget(self.stack.widget(self.pages["idle"]))
        self._sumup_overlay.setStyleSheet("background: rgba(0,0,0,0.85); border-radius: 0;")
        screen = self.screen()
        sw = screen.geometry().width() if screen else self.width()
        sh = screen.geometry().height() if screen else self.height()
        self._sumup_overlay.setGeometry(0, 0, sw, sh)

        ov_lay = QVBoxLayout(self._sumup_overlay)
        ov_lay.setAlignment(Qt.AlignCenter)
        ov_lay.setSpacing(20)

        self._sumup_icon = QLabel("\u23F3")  # hourglass
        self._sumup_icon.setAlignment(Qt.AlignCenter)
        self._sumup_icon.setFont(QFont("DM Sans", 48))
        self._sumup_icon.setStyleSheet("color: white; background: transparent;")
        ov_lay.addWidget(self._sumup_icon)

        self._sumup_msg = QLabel(t("preparing_payment"))
        self._sumup_msg.setAlignment(Qt.AlignCenter)
        self._sumup_msg.setFont(QFont("DM Sans", 22, QFont.Bold))
        self._sumup_msg.setStyleSheet("color: white; background: transparent;")
        self._sumup_msg.setWordWrap(True)
        ov_lay.addWidget(self._sumup_msg)

        self._sumup_sub = QLabel("")
        self._sumup_sub.setAlignment(Qt.AlignCenter)
        self._sumup_sub.setFont(QFont("DM Sans", 14))
        self._sumup_sub.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent;")
        self._sumup_sub.setWordWrap(True)
        ov_lay.addWidget(self._sumup_sub)

        self._sumup_back_btn = QPushButton(t("back"))
        self._sumup_back_btn.setCursor(Qt.PointingHandCursor)
        self._sumup_back_btn.setFont(QFont("DM Sans", 14, QFont.Bold))
        self._sumup_back_btn.setFixedSize(200, 50)
        self._sumup_back_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: white; "
            f"border: none; border-radius: 12px; font-size: 14px; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        self._sumup_back_btn.clicked.connect(self._sumup_cancel)
        self._sumup_back_btn.setVisible(False)
        ov_lay.addWidget(self._sumup_back_btn, alignment=Qt.AlignCenter)

        self._sumup_overlay.show()
        self._sumup_overlay.raise_()

        # Start checkout in background thread
        import threading
        threading.Thread(target=self._sumup_do_checkout, daemon=True).start()

    def _sumup_do_checkout(self):
        """Background: create checkout and send to Solo, then poll."""
        from sumup_payment import load_sumup_config, create_checkout, check_checkout, _get_reader_id
        import time

        cfg = load_sumup_config()
        if not cfg:
            QTimer.singleShot(0, lambda: self._sumup_show_error(t("error_sumup_not_configured")))
            return

        api_key = cfg["api_key"]
        merchant = cfg["merchant_code"]
        amount = cfg["amount"]
        currency = cfg.get("currency", "EUR")
        reader_id = cfg.get("reader_id", "")

        if not reader_id:
            reader_id = _get_reader_id(api_key, merchant) or ""

        # Create checkout and send to Solo
        checkout_id, client_tx_id = create_checkout(api_key, merchant, amount, currency, "Photobooth", reader_id)

        if not checkout_id:
            if client_tx_id == "READER_BUSY":
                # Solo is still processing previous checkout — wait and retry
                QTimer.singleShot(0, lambda: self._sumup_msg.setText(t("sumup_terminal_busy")))
                time.sleep(5)
                if not getattr(self, '_sumup_cancelled', False):
                    # Retry
                    self._sumup_do_checkout()
                return
            error_msg = client_tx_id or "Onbekende fout"
            self._sumup_active_checkout = None
            QTimer.singleShot(0, lambda m=error_msg: self._sumup_show_error(f"Kon betaling niet starten:\n{m}"))
            return

        self._sumup_active_checkout = checkout_id
        self._sumup_client_tx_id = client_tx_id

        # Update UI: show "betaal via terminal"
        QTimer.singleShot(0, self._sumup_show_waiting)

        # Poll for payment
        poll_start = time.time()
        MAX_POLL = 300  # 5 minutes max

        while not getattr(self, '_sumup_cancelled', False):
            if time.time() - poll_start > MAX_POLL:
                self._sumup_active_checkout = None
                QTimer.singleShot(0, lambda: self._sumup_show_error(t("error_payment_expired")))
                return

            status = check_checkout(api_key, checkout_id, client_tx_id)

            if status == "PAID":
                QTimer.singleShot(0, self._sumup_payment_success)
                return
            elif status == "FAILED":
                self._sumup_active_checkout = None
                QTimer.singleShot(0, lambda: self._sumup_show_error(t("error_payment_failed")))
                return
            elif status == "EXPIRED":
                self._sumup_active_checkout = None
                QTimer.singleShot(0, lambda: self._sumup_show_error(t("error_payment_expired")))
                return

            # Short sleep intervals so cancel is responsive
            for _ in range(4):  # 4 x 0.5s = 2s total
                if getattr(self, '_sumup_cancelled', False):
                    break
                time.sleep(0.5)

        print("[SUMUP] Polling gestopt (cancelled)")
        self._sumup_active_checkout = None

    def _sumup_show_waiting(self):
        """Update overlay to show 'pay at terminal'."""
        if not hasattr(self, '_sumup_icon'):
            return
        self._sumup_icon.setText("\u27A1")  # arrow right
        self._sumup_icon.setFont(QFont("DM Sans", 60))
        self._sumup_msg.setText(t("pay_via_pin_terminal"))
        ev = self.active_event
        amount = 5.0
        try:
            from sumup_payment import load_sumup_config
            cfg = load_sumup_config()
            if cfg:
                amount = cfg["amount"]
        except Exception:
            pass
        self._sumup_sub.setText(f"\u20ac {amount:.2f}")
        self._sumup_back_btn.setVisible(True)

    def _sumup_show_error(self, msg):
        """Show error on payment overlay."""
        if not hasattr(self, '_sumup_icon'):
            return
        self._sumup_icon.setText("\u26a0")  # warning
        self._sumup_icon.setFont(QFont("DM Sans", 48))
        self._sumup_msg.setText(msg)
        self._sumup_sub.setText("")
        self._sumup_back_btn.setVisible(True)

    def _sumup_payment_success(self):
        """Payment received — close overlay and start photo session."""
        self._sumup_active_checkout = None
        if hasattr(self, '_sumup_overlay') and self._sumup_overlay:
            self._sumup_overlay.deleteLater()
            self._sumup_overlay = None
        self.state = State.IDLE  # Reset to idle so _go_select_template works
        self._go_select_template()

    def _sumup_cancel(self):
        """Cancel payment flow — go back to idle.

        Note: SumUp has no abort API, so the Solo may still show the amount
        for up to 60 seconds. We track the cancelled checkout so if someone
        pays on it, we still honor that payment.
        """
        print("[SUMUP] Cancel aangeroepen")
        # Remember the cancelled checkout so we can honor late payments
        cancelled_id = getattr(self, '_sumup_active_checkout', None)
        cancelled_tx = getattr(self, '_sumup_client_tx_id', None)
        if cancelled_id:
            if not hasattr(self, '_sumup_cancelled_checkouts'):
                self._sumup_cancelled_checkouts = []
            self._sumup_cancelled_checkouts.append((cancelled_id, cancelled_tx))
            print(f"[SUMUP] Checkout {cancelled_id[:12]}... onthouden voor late betaling")
        # Signal the polling thread to stop
        self._sumup_cancelled = True
        self._sumup_active_checkout = None
        self._sumup_client_tx_id = None
        self.state = State.IDLE
        # Remove overlay
        if hasattr(self, '_sumup_overlay') and self._sumup_overlay:
            self._sumup_overlay.deleteLater()
            self._sumup_overlay = None
        # Start background check for late payment on cancelled checkout
        if cancelled_id:
            self._sumup_watch_cancelled(cancelled_id, cancelled_tx)

    def _sumup_watch_cancelled(self, checkout_id, client_tx_id):
        """Watch a cancelled checkout for 90 seconds — if someone pays, honor it."""
        import threading
        def _watch():
            import time
            from sumup_payment import check_checkout, load_sumup_config
            cfg = load_sumup_config()
            if not cfg:
                return
            api_key = cfg["api_key"]
            for _ in range(45):  # 45 x 2s = 90 seconds
                if getattr(self, '_sumup_active_checkout', None):
                    # New session started, stop watching old one
                    return
                status = check_checkout(api_key, checkout_id, client_tx_id)
                if status == "PAID":
                    print(f"[SUMUP] Late betaling ontvangen op geannuleerde checkout!")
                    QTimer.singleShot(0, self._sumup_late_payment)
                    return
                elif status in ("FAILED", "EXPIRED"):
                    return
                time.sleep(2)
            print(f"[SUMUP] Cancelled checkout {checkout_id[:12]}... verlopen")
        threading.Thread(target=_watch, daemon=True).start()

    def _sumup_late_payment(self):
        """A cancelled checkout was paid — start photo session."""
        if self.state == State.IDLE:
            print("[SUMUP] Late betaling — fotosessie starten!")
            self._go_select_template()
        else:
            self._payment_queue = getattr(self, '_payment_queue', 0) + 1
            print(f"[SUMUP] Late betaling — in wachtrij ({self._payment_queue})")

    def _start_sumup_loop(self):
        """Start SumUp payment loop (creates checkouts, polls for payment)."""
        if hasattr(self, '_sumup_loop') and self._sumup_loop and self._sumup_loop.isRunning():
            self._sumup_loop.resume()
            return
        from sumup_payment import SumUpPaymentLoop, load_sumup_config
        cfg = load_sumup_config()
        if not cfg:
            print("[SUMUP] Niet geconfigureerd — loop niet gestart")
            return
        self._sumup_loop = SumUpPaymentLoop(self)
        self._sumup_loop.payment_received.connect(self._on_sumup_payment)
        self._sumup_loop.status_changed.connect(
            lambda msg: self.status_label.setText(msg) if self.state == State.IDLE else None
        )
        self._sumup_loop.error_occurred.connect(
            lambda msg: print(f"[SUMUP] Error: {msg}")
        )
        self._sumup_loop.start_loop()
        print("[SUMUP] Payment loop gestart")

    def _stop_sumup_loop(self):
        """Stop SumUp payment loop."""
        if hasattr(self, '_sumup_loop') and self._sumup_loop:
            self._sumup_loop.stop_loop()
            print("[SUMUP] Payment loop gestopt")

    def _on_sumup_payment(self):
        """Called when SumUp payment is received — start photo session."""
        if self.state == State.IDLE:
            print("[SUMUP] Betaling ontvangen — fotosessie starten!")
            self._go_select_template()
        else:
            # Queue the session for later
            self._payment_queue = getattr(self, '_payment_queue', 0) + 1
            print(f"[SUMUP] Betaling ontvangen — in wachtrij ({self._payment_queue})")

    def _poll_payment_sessions(self):
        """Poll server for pending payment sessions (non-blocking)."""
        if self.state != State.IDLE or getattr(self, '_payment_polling_active', False):
            return
        user = self._cached_user
        booth_id = user.get("booth_secret", "") if user else ""
        if not booth_id:
            return
        self._payment_polling_active = True

        def _do_poll():
            import urllib.request
            import json as _json
            try:
                url = f"{config.SUPABASE_URL}/functions/v1/check-sessions?booth_id={booth_id}"
                req = urllib.request.Request(url, headers={
                    "apikey": config.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
                }, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                if data.get("start_session"):
                    self._payment_poll_result = data.get("pending", 0)
                else:
                    self._payment_poll_result = 0
            except Exception:
                self._payment_poll_result = 0
            self._payment_polling_active = False

        self._payment_poll_result = 0
        threading.Thread(target=_do_poll, daemon=True).start()
        # Check result after 6 seconds max
        QTimer.singleShot(200, self._check_payment_poll_result)

    def _check_payment_poll_result(self):
        """Check if payment poll thread has returned a result."""
        if getattr(self, '_payment_polling_active', False):
            # Still running, check again in 200ms (max ~30 checks = 6 sec)
            if not hasattr(self, '_poll_check_count'):
                self._poll_check_count = 0
            self._poll_check_count += 1
            if self._poll_check_count < 30:
                QTimer.singleShot(200, self._check_payment_poll_result)
            else:
                self._payment_polling_active = False
                self._poll_check_count = 0
            return
        self._poll_check_count = 0
        pending = getattr(self, '_payment_poll_result', 0)
        if pending > 0 and self.state == State.IDLE:
            print(f"[PAYMENT] Sessie ontvangen! ({pending} in wachtrij)")
            self._payment_queue = max(0, pending - 1)
            QTimer.singleShot(500, self._go_select_template)

    def _on_email_collect_toggled(self, checked):
        """Toggle data collection."""
        if self.active_event:
            self.active_event.email_collect = checked
            self.active_event.data_collect_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Gegevens verzamelen: {'aan' if checked else 'uit'}")

    def _open_collected_data_folder(self):
        """Open the folder where collected data CSV files are stored."""
        csv_dir = config.DATA_DIR
        if not os.path.isdir(csv_dir):
            os.makedirs(csv_dir, exist_ok=True)
        import subprocess
        subprocess.Popen(f'explorer "{csv_dir}"')

    def _open_data_collect_dialog(self):
        """Open dialog to configure data collection fields and timing."""
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QRadioButton, QButtonGroup
        ev = self.active_event
        if not ev:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(t("data_collect_toggle"))
        dlg.setFixedWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 16, 20, 16)

        # Title
        title = QLabel(t("dc_dialog_title"))
        title.setFont(QFont("DM Sans", 14, QFont.Bold))
        lay.addWidget(title)

        # Timing
        timing_lbl = QLabel(t("dc_when"))
        timing_lbl.setFont(QFont("DM Sans", 11, QFont.Bold))
        lay.addWidget(timing_lbl)

        timing_group = QButtonGroup(dlg)
        current_timing = getattr(ev, 'data_collect_timing', 'after')

        rb_before = QRadioButton(t("dc_before"))
        rb_before.setFont(QFont("DM Sans", 10))
        rb_before.setChecked(current_timing == "before")
        timing_group.addButton(rb_before)
        lay.addWidget(rb_before)

        rb_after_auto = QRadioButton(t("dc_after_auto"))
        rb_after_auto.setFont(QFont("DM Sans", 10))
        rb_after_auto.setChecked(current_timing == "after_auto")
        timing_group.addButton(rb_after_auto)
        lay.addWidget(rb_after_auto)

        rb_after_optional = QRadioButton(t("dc_after_optional"))
        rb_after_optional.setFont(QFont("DM Sans", 10))
        rb_after_optional.setChecked(current_timing in ("after", "after_optional"))
        timing_group.addButton(rb_after_optional)
        lay.addWidget(rb_after_optional)

        # Separator
        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {config.COLOR_BORDER};")
        lay.addWidget(sep)

        # Fields
        fields_lbl = QLabel(t("dc_which_fields"))
        fields_lbl.setFont(QFont("DM Sans", 11, QFont.Bold))
        lay.addWidget(fields_lbl)

        current_fields = getattr(ev, 'data_collect_fields', 'email').split(',')
        field_options = [
            ("email", t("dc_email"), True),   # Always required
            ("naam", t("dc_name"), False),
            ("telefoon", t("dc_phone"), False),
            ("adres", t("dc_address"), False),
            ("geboortedatum", t("dc_birthdate"), False),
        ]
        field_cbs = {}
        for key, label, required in field_options:
            cb = QCheckBox(label + (" (verplicht)" if required else ""))
            cb.setFont(QFont("DM Sans", 10))
            cb.setChecked(key in current_fields)
            if required:
                cb.setEnabled(False)
                cb.setChecked(True)
            field_cbs[key] = cb
            lay.addWidget(cb)

        # Separator
        sep2 = QLabel("")
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {config.COLOR_BORDER};")
        lay.addWidget(sep2)

        # Auto email
        auto_email_cb = QCheckBox(t("dc_auto_email"))
        auto_email_cb.setFont(QFont("DM Sans", 10))
        auto_email_cb.setChecked(getattr(ev, 'data_collect_auto_email', True))
        lay.addWidget(auto_email_cb)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        save_btn = QPushButton(t("save"))
        save_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        save_btn.setMinimumHeight(36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 20px; }}"
            f"QPushButton:hover {{ background: #27ae60; }}"
        )
        save_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setFont(QFont("DM Sans", 11))
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_BORDER}; color: {config.COLOR_TEXT}; "
            f"border: none; border-radius: 6px; padding: 8px 20px; }}"
            f"QPushButton:hover {{ background: #bdc3c7; }}"
        )
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        if dlg.exec_() == QDialog.Accepted:
            # Save settings
            if rb_before.isChecked():
                ev.data_collect_timing = "before"
            elif rb_after_auto.isChecked():
                ev.data_collect_timing = "after_auto"
            else:
                ev.data_collect_timing = "after_optional"
            ev.data_collect_fields = ",".join(k for k, cb in field_cbs.items() if cb.isChecked())
            ev.data_collect_auto_email = auto_email_cb.isChecked()
            ev.data_collect_enabled = True
            ev.email_collect = True
            self._email_collect_toggle.blockSignals(True)
            self._email_collect_toggle.setChecked(True)
            self._email_collect_toggle.blockSignals(False)
            ev.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Gegevens: timing={ev.data_collect_timing}, "
                  f"fields={ev.data_collect_fields}, auto_email={ev.data_collect_auto_email}")
        dlg.deleteLater()

    def _save_contact_to_csv(self, contact_data):
        """Append contact data to the event's CSV file.

        Args:
            contact_data: dict with keys like 'email', 'naam', 'telefoon', etc.
        """
        import csv
        ev = self.active_event
        if not ev or not getattr(ev, 'data_collect_enabled', False):
            return
        csv_dir = os.path.join(config.DATA_DIR, "email_lists")
        os.makedirs(csv_dir, exist_ok=True)
        # Use event name as filename (sanitized)
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in ev.name).strip() or ev.id
        csv_path = os.path.join(csv_dir, f"{safe_name}_contacts.csv")
        file_exists = os.path.isfile(csv_path)

        # Determine columns from event fields
        fields = getattr(ev, 'data_collect_fields', 'email').split(',')
        columns = ["timestamp", "event"] + fields

        try:
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(columns)
                row = [datetime.now().isoformat(), ev.name]
                for field in fields:
                    row.append(contact_data.get(field, ""))
                writer.writerow(row)
            print(f"[DATA] Contact opgeslagen in CSV: {contact_data.get('email', '?')}")
        except Exception as e:
            print(f"[DATA] CSV fout: {e}")

    def _save_email_to_csv(self, email_address):
        """Legacy wrapper — saves email via new contact system."""
        self._save_contact_to_csv({"email": email_address})

    def _update_email_visibility(self):
        """Show/hide email settings cards based on email toggle and plan."""
        is_pro = self._is_pro_feature("email")
        email_on = self._email_toggle.isChecked()

        # Show/hide pro banner
        self._share_pro_banner.setVisible(not is_pro)

        # Disable sharing toggles for Starter plan
        self._qr_toggle.setEnabled(is_pro)
        self._email_toggle.setEnabled(is_pro)

        # Show email sub-cards only when enabled AND plan allows
        show_email = email_on and is_pro
        self._gmail_card.setVisible(show_email)
        self._email_content_card.setVisible(show_email)
        self._email_attach_card.setVisible(show_email)

    def _update_gmail_status(self):
        """Update Gmail status label and fill inputs from saved config."""
        from email_sender import load_gmail_config, _load_credentials
        email, password, _, _ = _load_credentials()
        if email and password:
            self._smtp_email_input.setText(email)
            self._smtp_password_input.setText(password)
            self._gmail_status_label.setText(t("gmail_configured", email=email))
            self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS}; font-size: 12px; background: transparent;")
            self._gmail_unlink_btn.setVisible(True)
        else:
            self._smtp_email_input.setText("")
            self._smtp_password_input.setText("")
            self._gmail_status_label.setText("")
            self._gmail_unlink_btn.setVisible(False)

    def _show_gmail_help(self):
        """Show step-by-step guide for creating a Gmail app password."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl

        dlg = QDialog(self)
        dlg.setWindowTitle(t("gmail_setup_title"))
        dlg.setFixedWidth(420)
        dlg.setMinimumHeight(500)
        dlg.setStyleSheet(f"QDialog {{ background: {config.COLOR_BG}; }}")

        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.setContentsMargins(20, 16, 20, 16)

        title = QLabel(t("gmail_setup_title"))
        title.setFont(QFont("DM Sans", 13, QFont.Bold))
        title.setStyleSheet(f"color: {config.COLOR_TEXT};")
        lay.addWidget(title)

        all_steps = [
            "Log in op je Google-account in een webbrowser",
            "Klik op onderstaande link om naar App-wachtwoorden te gaan:",
        ]
        for i, text in enumerate(all_steps, 1):
            step = QLabel(f"<b>{i}.</b> {text}")
            step.setFont(QFont("DM Sans", 10))
            step.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            step.setWordWrap(True)
            lay.addWidget(step)

        # Clickable link button — compact
        link_btn = QPushButton(t("open_app_passwords"))
        link_btn.setCursor(Qt.PointingHandCursor)
        link_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        link_btn.setFixedHeight(32)
        link_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 14px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
        )
        link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://myaccount.google.com/apppasswords"))
        )
        lay.addWidget(link_btn)

        steps2 = [
            "Typ een naam in, bijv. \"Photobooth\" en klik op <b>Maken</b>",
            "Google toont een wachtwoord van 16 tekens (bijv. abcd efgh ijkl mnop)",
            "Kopieer dit wachtwoord en plak het in het veld \"App-wachtwoord\"",
            "Vul je Gmail-adres in en klik op <b>OPSLAAN && TESTEN</b>",
        ]
        for i, text in enumerate(steps2, 3):
            step = QLabel(f"<b>{i}.</b> {text}")
            step.setFont(QFont("DM Sans", 10))
            step.setStyleSheet(f"color: {config.COLOR_TEXT}; background: transparent;")
            step.setWordWrap(True)
            lay.addWidget(step)

        note = QLabel(
            "\u26a0\ufe0f <b>Let op:</b> 2-stapsverificatie moet aan staan op je "
            "Google-account. Dit stel je in via <i>Google Account \u2192 Beveiliging "
            "\u2192 2-stapsverificatie</i>."
        )
        note.setFont(QFont("DM Sans", 9))
        note.setStyleSheet(
            f"color: {config.COLOR_TEXT_DIM}; background: rgba(0,0,0,0.05); "
            f"border-radius: 6px; padding: 8px;"
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addSpacing(8)
        close_btn = QPushButton(t("close"))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        close_btn.setFixedHeight(34)
        close_btn.setMaximumWidth(160)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 6px 20px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
        )
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn, alignment=Qt.AlignCenter)

        dlg.exec_()

    def _on_gmail_save_test(self):
        """Save SMTP credentials and test the connection."""
        email = self._smtp_email_input.text().strip()
        password = self._smtp_password_input.text().strip()

        if not email or not password:
            self._gmail_status_label.setText(t("fill_email_and_password"))
            self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 12px; background: transparent;")
            return

        from email_sender import save_smtp_config, test_smtp_connection
        save_smtp_config(email, password)

        self._gmail_link_btn.setEnabled(False)
        self._gmail_status_label.setText(t("testing_connection"))
        self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; font-size: 12px; background: transparent;")
        QApplication.processEvents()

        # Run test synchronously but with processEvents to keep UI alive
        try:
            ok, err = test_smtp_connection()
        except Exception as e:
            ok, err = False, str(e)
        self._on_smtp_test_result(ok, err)

    def _on_smtp_test_result(self, success, error):
        """Handle SMTP test result."""
        self._gmail_link_btn.setEnabled(True)
        if success:
            self._gmail_status_label.setText(t("connection_success"))
            self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS}; font-size: 12px; background: transparent;")
            self._gmail_unlink_btn.setVisible(True)
        else:
            self._gmail_status_label.setText(t("connection_failed", error=error))
            self._gmail_status_label.setStyleSheet(f"color: {config.COLOR_DANGER}; font-size: 12px; background: transparent;")

    def _on_gmail_unlink(self):
        """Remove Gmail SMTP credentials."""
        from email_sender import remove_gmail_config
        remove_gmail_config()
        self._update_gmail_status()
        print("[EMAIL] Gmail config verwijderd")

    def _on_email_subject_changed(self):
        """Save email subject to event."""
        if self.active_event:
            self.active_event.email_subject = self._email_subject_input.text().strip()
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] E-mail onderwerp: {self.active_event.email_subject}")

    def _on_email_body_changed(self):
        """Save email body text to event."""
        if self.active_event:
            self.active_event.email_body = self._email_body_input.toPlainText()
            self.active_event.save(config.EVENTS_DIR)
            print("[SETTINGS] E-mail tekst opgeslagen")

    def _on_email_attach_changed(self, _checked=None):
        """Save email attachment preferences to event."""
        if self.active_event:
            self.active_event.email_send_strip = self._email_send_strip_cb.isChecked()
            self.active_event.email_send_originals = self._email_send_originals_cb.isChecked()
            self.active_event.email_send_gif = self._email_send_gif_cb.isChecked()
            self.active_event.share_single_strip = self._share_single_strip_cb.isChecked()
            self.active_event.compress_sharing = self._compress_sharing_cb.isChecked()
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Bijlagen: strip={self.active_event.email_send_strip}, "
                  f"single={self.active_event.share_single_strip}, "
                  f"compress={self.active_event.compress_sharing}, "
                  f"originals={self.active_event.email_send_originals}, gif={self.active_event.email_send_gif}")

    def _on_countdown_changed(self, value):
        """Change countdown duration and auto-save."""
        if self.active_event:
            self.active_event.countdown_seconds = value
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Aftellen: {value} sec")

    def _on_delay_changed(self, value):
        """Change delay between photos and auto-save."""
        if self.active_event:
            self.active_event.photo_delay_ms = value * 1000
            self.active_event.save(config.EVENTS_DIR)

    def _on_sharing_timeout_changed(self, value):
        """Change sharing screen timeout and auto-save."""
        if self.active_event:
            self.active_event.sharing_timeout = value
            self.active_event.save(config.EVENTS_DIR)

    def _on_lock_size_changed(self, value):
        """Change lock icon size and auto-save."""
        if self.active_event:
            self.active_event.lock_icon_size = value
            self.active_event.save(config.EVENTS_DIR)

    def _on_camera_mode_changed(self, checked=None):
        """Toggle between DSLR and webcam mode — requires restart."""
        is_webcam = self._cam_webcam_radio.isChecked()
        # Verhuur: picker-rij blijft altijd zichtbaar (zie _load_settings)
        self._webcam_select_row.setVisible(True)
        if self.active_event:
            old_mode = self.active_event.camera_mode
            new_mode = "webcam" if is_webcam else "dslr"
            self.active_event.camera_mode = new_mode
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Camera modus: {new_mode}")
            # If mode actually changed, close app
            if old_mode != new_mode:
                from PyQt5.QtWidgets import QMessageBox
                msg = QMessageBox(self)
                msg.setWindowTitle(t("dialog_restart_required"))
                msg.setText(t("camera_mode_changed_restart"))
                msg.setIcon(QMessageBox.Information)
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec_()
                QApplication.quit()
                return
        if is_webcam:
            self._update_webcam_status()

    def _on_camera_settings_changed(self, _=None):
        """Save mirror and rotation settings."""
        if self.active_event:
            self.active_event.camera_mirror = self._cam_mirror_cb.isChecked()
            rot_idx = self._cam_rotation_combo.currentIndex()
            self.active_event.camera_rotation = [0, 90, 180, 270][rot_idx]
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Mirror: {self.active_event.camera_mirror}, Rotation: {self.active_event.camera_rotation}")

    def _on_live_view_position_changed(self, position):
        """Wissel live-view positie (center/top). Slaat op + past direct toe."""
        if not self.active_event:
            return
        if position not in ("center", "top"):
            position = "center"
        self.active_event.live_view_position = position
        self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Live view positie: {position}")
        # Direct toepassen op de bestaande live_view_label
        self._apply_live_view_alignment()

    def _apply_live_view_alignment(self):
        """Pas alignment van live_view_label aan op basis van event-setting.

        BELANGRIJK: default-pad ("center") = Qt.AlignCenter — IDENTIEK aan de
        oorspronkelijke hardcoded waarde op regel 2037. Alleen wanneer
        live_view_position == "top" wijkt het af.
        """
        if not hasattr(self, 'live_view_label'):
            return
        pos = getattr(self.active_event, 'live_view_position', 'center') if self.active_event else 'center'
        if pos == "top":
            self.live_view_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        else:
            # Default — exact zelfde gedrag als voorheen
            self.live_view_label.setAlignment(Qt.AlignCenter)

    def _ask_camera_restart(self):
        """Camera-modus gewijzigd (webcam ↔ Canon DSLR) — meld herstart
        en sluit af. Identieke flow als _on_camera_mode_changed: EDSDK/
        digiCamControl-initialisatie gebeurt bij opstarten."""
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(t("dialog_restart_required"))
        msg.setText(t("camera_mode_changed_restart"))
        msg.setIcon(QMessageBox.Information)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
        QApplication.quit()

    def _open_webcam_dialog(self):
        """Open dialog to select webcam and resolution."""
        dialog = QDialog(self)
        dialog.setWindowTitle(t("dialog_choose_webcam"))
        dialog.setMinimumWidth(400)
        lay = QVBoxLayout(dialog)
        lay.setSpacing(12)

        # Status label
        status = QLabel(t("searching_webcams"))
        status.setFont(QFont("DM Sans", 12))
        status.setAlignment(Qt.AlignCenter)
        lay.addWidget(status)

        # Camera list
        cam_combo = QComboBox()
        cam_combo.setFont(QFont("DM Sans", 12))
        cam_combo.setMinimumHeight(36)
        cam_combo.setVisible(False)
        lay.addWidget(cam_combo)

        # Resolution list
        res_label = QLabel(t("webcam_resolution"))
        res_label.setFont(QFont("DM Sans", 11))
        res_label.setVisible(False)
        lay.addWidget(res_label)
        res_combo = QComboBox()
        res_combo.setFont(QFont("DM Sans", 12))
        res_combo.setMinimumHeight(36)
        res_combo.setVisible(False)
        lay.addWidget(res_combo)

        # Buttons
        btn_row = QHBoxLayout()
        ok_btn = QPushButton(t("save"))
        ok_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 8px; padding: 8px 20px; }}"
        )
        ok_btn.setVisible(False)
        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setFont(QFont("DM Sans", 11))
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 8px; padding: 8px 20px; }}"
        )
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        # Verhuurophalen: extra optie "Canon camera" (DSLR via
        # digiCamControl). Sentinel-index onderscheidt hem van echte
        # webcam-indexen (>= 0). Hippe-brand: lijst ongewijzigd.
        CANON_IDX = -100
        is_huren = self.backend_brand == 'huren'

        def _populate(cameras, resolutions):
            if not cameras and not is_huren:
                status.setText(t("no_webcam_found"))
                return
            status.setText(t("select_webcam_prompt"))
            cam_combo.setVisible(True)
            for idx, name in cameras:
                cam_combo.addItem(name, idx)
            if is_huren:
                cam_combo.addItem("Canon camera", CANON_IDX)
            # Select saved — event-waarde, anders booth-wide (welcome-page)
            saved_mode, saved_idx = "webcam", 0
            if self.active_event:
                saved_mode = self.active_event.camera_mode
                saved_idx = self.active_event.webcam_index
            else:
                try:
                    from booth_settings import BoothSettings as _BS
                    if _BS.exists():
                        _b = _BS.load()
                        saved_mode, saved_idx = _b.camera_mode, _b.webcam_index
                except Exception:
                    pass
            if is_huren and saved_mode == "dslr":
                cam_combo.setCurrentIndex(cam_combo.count() - 1)
            else:
                for i in range(cam_combo.count()):
                    if cam_combo.itemData(i) == saved_idx:
                        cam_combo.setCurrentIndex(i)
                        break

            def _on_cam_change(ci):
                # Verhuur: resolutie-keuze verborgen, altijd "Standaard" (=hoogste).
                res_combo.clear()
                res_combo.addItem("Standaard")
                res_label.setVisible(False)
                res_combo.setVisible(False)

            cam_combo.currentIndexChanged.connect(_on_cam_change)
            _on_cam_change(cam_combo.currentIndex())
            ok_btn.setVisible(True)

        def _error(msg):
            status.setText(f"{t('error_title')}: {msg}")

        # Scan after dialog opens (delay so dialog is visible first)
        def _do_scan():
            try:
                from webcam import WebcamCamera
                cameras = WebcamCamera.list_cameras()
                standard_res = WebcamCamera.list_resolutions()
                resolutions = {idx: standard_res for idx, _ in cameras}
                _populate(cameras, resolutions)
            except Exception as e:
                _error(str(e))
        QTimer.singleShot(50, _do_scan)

        def _save():
            ci = cam_combo.currentIndex()
            cam_idx = cam_combo.itemData(ci)
            ev = self.active_event

            def _booth_persist(**kw):
                # Booth-wide opslaan — werkt óók zonder actief event
                # (welcome-page); nieuwe events erven dit via create_new.
                try:
                    from booth_settings import BoothSettings as _BS
                    bs = _BS.load() if _BS.exists() else _BS()
                    for k, v in kw.items():
                        setattr(bs, k, v)
                    bs.save()
                except Exception as ex:
                    print(f"[SETTINGS] Camera booth-wide opslaan mislukt: {ex}")

            old_mode = "webcam"
            if ev:
                old_mode = ev.camera_mode
            else:
                try:
                    from booth_settings import BoothSettings as _BS
                    if _BS.exists():
                        old_mode = _BS.load().camera_mode
                except Exception:
                    pass

            if is_huren and cam_idx == CANON_IDX:
                # Canon camera gekozen — schakel naar DSLR-modus
                # (digiCamControl). Zelfde herstart-flow als de
                # oorspronkelijke modus-radio's.
                if ev:
                    ev.camera_mode = "dslr"
                    ev.save(config.EVENTS_DIR)
                _booth_persist(camera_mode="dslr")
                self._webcam_status_label.setText("Canon camera (digiCamControl)")
                self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
                print("[SETTINGS] Camera: Canon DSLR (huren-modus)")
                dialog.accept()
                if old_mode != "dslr":
                    self._ask_camera_restart()
                return

            if cam_idx is None or cam_idx < 0:
                cam_idx = 0
            res = res_combo.currentText()
            cam_name = cam_combo.currentText()
            res_val = res if res != "Standaard" else ""
            if ev:
                if is_huren:
                    # Echte webcam gekozen — eventueel terug uit Canon-modus
                    ev.camera_mode = "webcam"
                ev.webcam_index = int(cam_idx)
                ev.webcam_name = cam_name
                ev.webcam_resolution = res_val
                ev.save(config.EVENTS_DIR)
            _booth_persist(camera_mode="webcam", webcam_index=int(cam_idx),
                           webcam_name=cam_name, webcam_resolution=res_val)
            self._webcam_status_label.setText(f"{cam_name} ({res})")
            self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            print(f"[SETTINGS] Webcam opgeslagen: index={cam_idx}, naam={cam_name}, resolutie={res}")
            if old_mode != "webcam":
                dialog.accept()
                self._ask_camera_restart()
                return
            dialog.accept()

        ok_btn.clicked.connect(_save)
        dialog.exec_()

    def _test_webcam_diagnostic(self):
        """Run webcam diagnostics and show results in a dialog."""
        from PyQt5.QtWidgets import QDialog, QTextEdit
        dialog = QDialog(self)
        dialog.setWindowTitle("Webcam Diagnostics")
        dialog.setMinimumSize(500, 400)
        lay = QVBoxLayout(dialog)
        log = QTextEdit()
        log.setReadOnly(True)
        log.setFont(QFont("Consolas", 10))
        lay.addWidget(log)

        def add(text):
            log.append(text)
            QApplication.processEvents()

        close_btn = QPushButton("OK")
        close_btn.clicked.connect(dialog.accept)
        lay.addWidget(close_btn)

        # Run diagnostics
        ev = self.active_event
        add(f"=== WEBCAM DIAGNOSTICS v2.5 ===\n")

        # 1. Event settings
        add(f"Event: {ev.name if ev else 'NONE'}")
        add(f"camera_mode: {ev.camera_mode if ev else '?'}")
        add(f"webcam_index: {ev.webcam_index if ev else '?'}")
        add(f"webcam_name: {ev.webcam_name if ev else '?'}")
        add(f"webcam_resolution: {ev.webcam_resolution if ev else '?'}")
        add("")

        # 2. Check cv2
        add("--- OpenCV Check ---")
        try:
            import cv2
            add(f"cv2 version: {cv2.__version__}")
        except ImportError as e:
            add(f"cv2 IMPORT FAILED: {e}")
            dialog.exec_()
            return

        # 3. Check WebcamCamera
        add("\n--- WebcamCamera Import ---")
        try:
            from webcam import WebcamCamera
            add("WebcamCamera: OK")
        except ImportError as e:
            add(f"WebcamCamera IMPORT FAILED: {e}")
            dialog.exec_()
            return

        # 4. List cameras
        add("\n--- Available Cameras ---")
        try:
            cameras = WebcamCamera.list_cameras()
            if cameras:
                for idx, name in cameras:
                    add(f"  [{idx}] {name}")
            else:
                add("  GEEN CAMERAS GEVONDEN")
        except Exception as e:
            add(f"  ERROR: {e}")

        # 5. Try to open saved index
        wc_idx = ev.webcam_index if ev else 0
        wc_name = ev.webcam_name if ev else ""
        wc_res = ev.webcam_resolution if ev else ""
        add(f"\n--- Connect Test (index={wc_idx}, name={wc_name}) ---")

        try:
            test_cam = WebcamCamera()
            result = test_cam.connect(wc_idx, wc_res, wc_name)
            add(f"connect() result: {result}")
            add(f"is_connected: {test_cam.is_connected()}")
            if test_cam.is_connected():
                add(f"camera_name: {test_cam._camera_name}")
                # Try to grab a frame
                ret, frame = test_cam.cap.read()
                add(f"frame read: ret={ret}, shape={frame.shape if ret else 'N/A'}")
                add("\n*** WEBCAM WORKS! ***")
            else:
                add("\n*** WEBCAM FAILED TO CONNECT ***")
            test_cam.disconnect()
        except Exception as e:
            add(f"EXCEPTION: {e}")
            import traceback
            add(traceback.format_exc())

        # 6. Current camera state
        add(f"\n--- Current App Camera ---")
        add(f"type: {type(self.camera).__name__}")
        add(f"has .cap: {hasattr(self.camera, 'cap')}")
        add(f"is_connected: {self.camera.is_connected()}")

        dialog.exec_()

    def _update_webcam_status(self):
        """Update webcam status label from saved settings."""
        if not hasattr(self, '_webcam_status_label'):
            return
        # Waarden uit het event, of booth-wide als er (nog) geen event is
        ev = self.active_event
        cam_mode, wc_name, wc_idx, wc_res = None, "", 0, ""
        if ev:
            cam_mode = ev.camera_mode
            wc_name, wc_idx, wc_res = ev.webcam_name, ev.webcam_index, ev.webcam_resolution
        else:
            try:
                from booth_settings import BoothSettings as _BS
                if _BS.exists():
                    bs = _BS.load()
                    cam_mode = bs.camera_mode
                    wc_name, wc_idx, wc_res = bs.webcam_name, bs.webcam_index, bs.webcam_resolution
            except Exception:
                pass
        if cam_mode == "dslr" and self.backend_brand == 'huren':
            self._webcam_status_label.setText("Canon camera (digiCamControl)")
            self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        elif cam_mode == "webcam":
            name = wc_name or f"Camera {wc_idx}"
            res = wc_res or "Standaard"
            self._webcam_status_label.setText(f"{name} ({res})")
            self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        else:
            self._webcam_status_label.setText(t("no_webcam_selected"))
            self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")

    def _on_language_changed(self, index):
        """Change UI language and ask user to restart."""
        lang_codes = ["nl", "en", "de", "fr", "es", "it"]
        if 0 <= index < len(lang_codes):
            code = lang_codes[index]
            save_language(code)
            set_language(code)
            print(f"[SETTINGS] Taal gewijzigd naar: {code}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Language / Taal",
                "Language changed. Please restart the application.\n"
                "Taal gewijzigd. Herstart de applicatie.")

    def _on_booth_mode_changed(self, checked):
        """Wissel tussen Standalone en Linked-modus.

        Bij switch naar Standalone wordt een eventueel gekoppeld event
        ontkoppeld (linked_* velden gewist) zodat state en radio
        100% consistent zijn — gebruiker-eis.
        """
        if not checked:
            return
        mode = "linked" if self._booth_mode_linked_radio.isChecked() else "standalone"
        if not self.active_event:
            return

        # Switch naar Standalone terwijl er een event gekoppeld is → loskoppelen
        if mode == "standalone" and self.active_event.linked_booking_id:
            from PyQt5.QtWidgets import QMessageBox
            ans = QMessageBox.question(self, "Modus wisselen",
                "Naar Standalone schakelen ontkoppelt het huidige event.\n"
                "Wachtende uploads blijven in de queue. Doorgaan?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                # Zet radio terug naar linked zonder signal-loop
                self._booth_mode_linked_radio.blockSignals(True)
                self._booth_mode_linked_radio.setChecked(True)
                self._booth_mode_linked_radio.blockSignals(False)
                return
            # Stop uploader, wis linked_* velden (queue blijft)
            old_id = self.active_event.linked_booking_id
            self.active_event.linked_booking_id = ""
            self.active_event.linked_token = ""
            self.active_event.linked_booking_label = ""
            self.active_event.linked_design_path = ""
            if old_id:
                try:
                    from cloud_uploader import stop_worker
                    stop_worker(old_id)
                except Exception:
                    pass

        self.active_event.booth_mode = mode
        self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Booth-modus: {mode}")
        self._update_linked_card_visibility()

    def _update_linked_card_visibility(self):
        """Toon de Gekoppeld-event-kaart altijd (verhuur = altijd Linked).

        Verbergt alle Canon/Standalone-flow UI op Event- en Layout-tabs
        zodat alleen de Linked-relevante onderdelen zichtbaar zijn.
        """
        if not hasattr(self, '_card_linked'):
            return
        ev = self.active_event
        # Verhuur is altijd linked — geen toggle meer
        is_linked = True

        # Standalone-Event-tab widgets altijd verbergen — verhuur toont alleen
        # de Gekoppeld-kaart op het Event-tab.
        if hasattr(self, '_event_picker_row'):
            self._event_picker_row.setVisible(False)
        if hasattr(self, '_card_event'):
            self._card_event.setVisible(False)
        if hasattr(self, '_card_idle_bg'):
            self._card_idle_bg.setVisible(False)
        # Canon bg-management UI op Layout-tab verbergen
        if hasattr(self, '_canva_btn'):
            self._canva_btn.setVisible(False)
        if hasattr(self, '_layout_bg_label'):
            self._layout_bg_label.setVisible(False)
        if hasattr(self, '_layout_bg_btn'):
            self._layout_bg_btn.setVisible(False)
        if hasattr(self, '_layout_bg_remove_btn'):
            self._layout_bg_remove_btn.setVisible(False)

        # Gekoppeld-kaart naar Event-tab verplaatsen (top)
        if hasattr(self, '_tab0_lay'):
            current = self._card_linked.parentWidget()
            tab0_widget = self._tab0_lay.parentWidget()
            if current is not tab0_widget:
                self._card_linked.setParent(None)
                self._tab0_lay.insertWidget(0, self._card_linked)

        self._card_linked.setVisible(True)

        # Wifi-paneel definitief uit — content altijd zichtbaar
        if hasattr(self, '_no_wifi_widget'):
            self._no_wifi_widget.setVisible(False)
        if hasattr(self, '_linked_content_widget'):
            self._linked_content_widget.setVisible(True)

        booking_id = getattr(ev, 'linked_booking_id', '') if ev else ''
        label = getattr(ev, 'linked_booking_label', '') if ev else ''

        if booking_id:
            self._linked_status_label.setText(f"🟢 {label or booking_id}\nID: {booking_id}")
            self._btn_couple_event.setVisible(False)
            self._btn_refresh_event.setVisible(True)
            self._btn_unlink_event.setVisible(True)
            # Foto-aantal selector blijft verborgen (layout bepaalt aantal)
            self._touch_spin_set(self._linked_count_spin, getattr(ev, 'linked_photo_count', 2))
            self._update_linked_progress()
        else:
            self._linked_status_label.setText("Geen event gekoppeld")
            self._btn_couple_event.setVisible(True)
            self._btn_refresh_event.setVisible(False)
            self._btn_unlink_event.setVisible(False)
            self._linked_progress_label.setText("")
            self._linked_progress_label.setVisible(False)

    def _update_linked_progress(self):
        """Werk de upload-voortgang regel bij — alleen tonen als er iets in queue zit."""
        ev = self.active_event
        if not ev or not getattr(ev, 'linked_booking_id', ''):
            self._linked_progress_label.setText("")
            self._linked_progress_label.setVisible(False)
            return
        try:
            from cloud_uploader import get_status
            s = get_status(ev.linked_booking_id)
        except Exception:
            self._linked_progress_label.setVisible(False)
            return
        if s["total"] == 0:
            self._linked_progress_label.setText("")
            self._linked_progress_label.setVisible(False)
            return
        pct = int(100 * s["uploaded"] / max(1, s["total"]))
        msg = f"Upload: {s['uploaded']}/{s['total']} foto's ({pct}%)"
        if s["pending"] > 0:
            msg += f" — {s['pending']} wacht op upload"
        if s["failed"] > 0:
            msg += f" — {s['failed']} mislukt"
        self._linked_progress_label.setText(msg)
        self._linked_progress_label.setVisible(True)

    def _on_couple_event_clicked(self):
        """Open de event-koppel modal — implementatie in Fase 3."""
        # Capture-sessie check
        if hasattr(self, 'state') and self.state not in (State.IDLE, State.SETTINGS):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Event koppelen",
                "Stop eerst de huidige sessie voor je een event koppelt.")
            return
        self._show_couple_event_dialog()

    def _on_refresh_event_clicked(self):
        """Re-fetch booking metadata + design uit cloud — async via worker."""
        ev = self.active_event
        token = getattr(ev, 'linked_token', '') if ev else ''
        if not token:
            return

        from couple_event_dialog import CouplingWorker, CouplingLoadingDialog
        loading = CouplingLoadingDialog(self)
        worker = CouplingWorker(token, self, brand=self.backend_brand)
        self._coupling_worker = worker

        def _on_progress(msg):
            loading.set_status(msg)

        def _on_done(booking_data, design_local_path, err_msg):
            loading.accept()
            # Hergebruik dezelfde finished-flow als bij coupling
            self._on_coupling_finished(token, booking_data, design_local_path, err_msg)
            try:
                worker.deleteLater()
            except Exception:
                pass
            self._coupling_worker = None

        worker.progress.connect(_on_progress)
        worker.done.connect(_on_done)
        worker.start()
        loading.exec_()

    def _on_unlink_event_clicked(self):
        """Loskoppelen — clear linked_* velden + stop uploader."""
        from PyQt5.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Loskoppelen",
            "Loskoppelen van het event? Foto's blijven in de queue tot ze geüpload zijn."
            ) != QMessageBox.Yes:
            return
        ev = self.active_event
        old_booking_id = ev.linked_booking_id if ev else ""
        if ev:
            ev.linked_booking_id = ""
            ev.linked_token = ""
            ev.linked_booking_label = ""
            ev.linked_design_path = ""
            # NIET booth_mode wisselen — verhuur blijft altijd 'linked'.
            # Card toont nu lege "Geen event gekoppeld" state met Koppel-knop.
            ev.save(config.EVENTS_DIR)
        # Stop uploader (queue blijft op disk staan; bij re-koppeling pakt-ie weer op)
        if old_booking_id:
            try:
                from cloud_uploader import stop_worker
                stop_worker(old_booking_id)
            except Exception as e:
                print(f"[LINKED] Uploader stop fout: {e}")
        self._update_linked_card_visibility()
        print("[LINKED] Event losgekoppeld")

    def _on_linked_count_changed(self, value):
        """Aantal foto's per strip aangepast — regenereer template-frames."""
        ev = self.active_event
        if not ev:
            return
        ev.linked_photo_count = int(value)
        ev.save(config.EVENTS_DIR)
        print(f"[LINKED] Foto-aantal: {value}")
        # Als gekoppeld: regenereer template met nieuwe count + default frames
        if ev.linked_booking_id and ev.linked_design_path:
            ok, err = self._fetch_and_apply_linked_design()
            if not ok:
                print(f"[LINKED] Template-regen waarschuwing: {err}")

    def _apply_linked_booking(self, booking_data: dict):
        """Schrijf booking-metadata naar active_event (na coupling of refresh).

        BELANGRIJK: als de cloud-response geen geldige booking.id bevat,
        wordt de bestaande koppeling NIET overschreven met lege waarden.
        Eerder werd `linked_booking_id` blind op `b.get("id", "")` gezet —
        bij een lege/onvolledige response betekende dat: koppeling weg, na
        reboot welcome-scherm. Nu: alleen schrijven als bid niet-leeg.
        """
        if not self.active_event:
            return
        b = booking_data.get("booking", {}) or {}
        q = booking_data.get("quote", {}) or {}
        bid = b.get("id", "") or ""
        # Geen geldige booking.id in response → bestaande koppeling intact laten.
        # Voorkomt dat een lege/onvolledige cloud-response de event-koppeling op
        # disk wist na een geslaagde fetch_booking-call.
        if not bid:
            print(f"[LINKED] _apply_linked_booking: lege booking.id in response, "
                  f"bestaande koppeling intact gelaten "
                  f"(id={self.active_event.linked_booking_id!r})")
            return
        # Display label: customer + event_date als beschikbaar
        name = (b.get("customer_name") or q.get("customer_name")
                or b.get("event_name") or q.get("event_name") or "Gekoppeld event")
        date = (b.get("event_date") or b.get("event_start_date")
                or q.get("event_date") or q.get("event_start_date") or "")
        label = f"{name}" + (f" · {date}" if date else "")
        # Event-datum bewaren voor de test-print-limiet (max N prints vóór
        # de event-datum, daarna onbeperkt). Alleen overschrijven als we een
        # datum hebben — een lege response mag een bekende datum niet wissen.
        if date:
            self.active_event.linked_event_date = str(date)[:10]
        # Re-couple cleanup: oude uploader stoppen als we naar een ANDERE booking
        # switchen, anders blijft die op de oude queue draaien.
        old_id = self.active_event.linked_booking_id
        if old_id and old_id != str(bid):
            try:
                from cloud_uploader import stop_worker
                stop_worker(old_id)
                print(f"[LINKED] Oude uploader gestopt voor {old_id} (re-couple naar {bid})")
            except Exception as e:
                print(f"[LINKED] Stop oude uploader fout (niet kritiek): {e}")
        self.active_event.linked_booking_id = str(bid)
        # Token NIET overschrijven met lege string — bewaar bestaande tokens
        # als de response 'm niet bevat (token zit normaal niet in booking_data).
        new_token = q.get("token", "") or ""
        if new_token:
            self.active_event.linked_token = new_token
            # Token ook bij de upload-queue zelf bewaren zodat deze booking
            # blijft uploaden nadat de booth aan een ander event gekoppeld is.
            try:
                from cloud_uploader import save_queue_token
                save_queue_token(str(bid), new_token, label,
                                 brand=self.backend_brand)
            except Exception as e:
                print(f"[LINKED] save_queue_token fout (niet kritiek): {e}")
        self.active_event.linked_booking_label = label
        # Design path NIET wissen als de response 'm niet teruggeeft —
        # behoud bestaande lokale referentie voor offline-flow.
        new_design = b.get("photostrip_design_url", "") or ""
        if new_design:
            self.active_event.linked_design_path = new_design
        # Gebruiker-keuze: altijd 3 foto's voor DNP linked-modus (kan later
        # variabel via UI). Cloud-DB-default is 2 — die negeren we.
        self.active_event.linked_photo_count = 3
        # Coupling impliceert booth_mode='linked' — UI moet matchen met state
        self.active_event.booth_mode = "linked"
        # Printer-mode: cloud is source-of-truth — het design is opgeslagen
        # met een specifieke aspect-ratio (1.5:1 Canon vs 2:1 DNP). De lokale
        # printer_mode moet daarop matchen anders faalt validate_design_format
        # en kan de print-compose niet de juiste output bouwen.
        # Cloud kent (nog) geen 4x3 — dat is voorlopig lokaal-only.
        cloud_pm = booking_data.get("printer_mode", "")
        if cloud_pm == "premium":
            self.active_event.printer_mode = "3strips"
        elif cloud_pm == "standard":
            self.active_event.printer_mode = "4x6"
        # Pakket-type bewaren voor pakket-afhankelijke print-delay.
        # Eerst proberen booking.package (officieel veld), dan fallback
        # op printer_mode mapping (oudere bookings).
        pkg = (b.get("package") or "").lower().strip()
        if not pkg and cloud_pm in ("premium", "standard"):
            pkg = cloud_pm
        if pkg in ("premium", "standard"):
            self.active_event.linked_package = pkg
        else:
            # Onbekende pakketnaam (bv. "deluxe"): wis het oude pakket
            # zodat een eerdere "premium"-waarde niet blijft hangen en
            # de delay-logica terugvalt op de veilige default (standard).
            self.active_event.linked_package = ""
            if pkg:
                print(f"[BOOKING] Onbekend pakket {pkg!r} — delay-default "
                      f"(standard/20s) wordt gebruikt")
        # Pakket bepaalt printen: 'foto' (Light — alleen digitaal, geen
        # printer in het pakket) → printen automatisch UIT; alle andere
        # pakketten → AAN. Alleen bij een ANDERE booking dan voorheen,
        # zodat een handmatige toggle niet elke periodieke refresh (60s)
        # van dezelfde booking wordt teruggedraaid.
        if old_id != str(bid):
            new_print_enabled = (pkg != "foto")
            if self.active_event.print_enabled != new_print_enabled:
                print(f"[BOOKING] Pakket {pkg or 'onbekend'} → printen "
                      f"{'AAN' if new_print_enabled else 'UIT'}")
            self.active_event.print_enabled = new_print_enabled
            # Settings-toggle meteen syncen indien al gebouwd
            if hasattr(self, '_print_enabled_toggle'):
                try:
                    self._print_enabled_toggle.blockSignals(True)
                    self._print_enabled_toggle.setChecked(new_print_enabled)
                    self._print_enabled_toggle.blockSignals(False)
                except Exception:
                    pass
        self.active_event.save(config.EVENTS_DIR)
        # Koppeling gewijzigd → cloud-log context bijwerken (event + klant)
        self._update_log_context()

    def _show_couple_event_dialog(self):
        """Open de event-koppel modal: webcam QR-scan + handmatige fallback.

        QR-scan en API-calls draaien op aparte threads zodat de UI nooit
        bevriest. Tijdens cloud-calls wordt een loading-dialoog getoond.
        """
        from couple_event_dialog import CoupleEventDialog
        wc_idx = 0
        wc_res = ""
        wc_name = ""
        if self.active_event:
            wc_idx = getattr(self.active_event, 'webcam_index', 0) or 0
            wc_res = getattr(self.active_event, 'webcam_resolution', '') or ""
            wc_name = getattr(self.active_event, 'webcam_name', '') or ""

        # Webcam tijdelijk vrijgeven voor de QR-scanner
        cam_was_connected = False
        try:
            if hasattr(self, 'camera') and self.camera and self.camera.is_connected():
                cam_was_connected = True
                self.camera.stop_live_view()
                self.camera.disconnect()
        except Exception as e:
            print(f"[LINKED] Camera vrijgeven fout (continue): {e}")

        try:
            dlg = CoupleEventDialog(self, webcam_index=wc_idx)
            result = dlg.exec_()
        finally:
            if cam_was_connected:
                try:
                    self.camera.connect(wc_idx, wc_res, wc_name)
                    print(f"[LINKED] Camera heraangesloten ({wc_idx})")
                except Exception as e:
                    print(f"[LINKED] Camera reconnect fout: {e}")

        if result != dlg.Accepted:
            return
        token = dlg.selected_token
        if not token:
            return

        # Cloud calls op background-thread — UI blijft responsive met loading-dialoog
        from couple_event_dialog import CouplingWorker, CouplingLoadingDialog
        loading = CouplingLoadingDialog(self)
        worker = CouplingWorker(token, self, brand=self.backend_brand)
        # Bewaren als attr zodat de QThread niet ge-garbage-collect wordt
        self._coupling_worker = worker

        def _on_progress(msg):
            loading.set_status(msg)

        def _on_done(booking_data, design_local_path, err_msg):
            # Sluit loading-dialog op de main thread (we zitten al op main via Qt signal)
            loading.accept()
            self._on_coupling_finished(token, booking_data, design_local_path, err_msg)
            try:
                worker.deleteLater()
            except Exception:
                pass
            self._coupling_worker = None

        worker.progress.connect(_on_progress)
        worker.done.connect(_on_done)
        worker.start()

        # Modal — blokkeert tot worker .accept() doet via done-signal
        loading.exec_()

    def _on_coupling_finished(self, token, booking_data, design_local_path, err_msg):
        """Op main thread aangeroepen na cloud-calls. Past data toe op event."""
        from PyQt5.QtWidgets import QMessageBox

        if booking_data is None:
            QMessageBox.warning(self, "Koppelen mislukt", err_msg or "Onbekende fout")
            return

        # Metadata toepassen
        self._apply_linked_booking(booking_data)
        if self.active_event:
            self.active_event.linked_token = token
            self.active_event.save(config.EVENTS_DIR)
            # Token bij de upload-queue bewaren — QR-coupling pad heeft het
            # token direct (komt uit de gescande code, niet uit de response).
            try:
                from cloud_uploader import save_queue_token
                save_queue_token(
                    self.active_event.linked_booking_id, token,
                    getattr(self.active_event, 'linked_booking_label', ''),
                    brand=self.backend_brand,
                )
            except Exception as e:
                print(f"[LINKED] save_queue_token fout (niet kritiek): {e}")

        # Design verwerken indien aanwezig
        if design_local_path:
            # User-initiated action (eerste coupling of Ververs) → regen forceren
            ok, fetch_err = self._apply_design_to_template(design_local_path, force_regen=True)
            if not ok:
                QMessageBox.warning(self, "Design probleem",
                    f"{fetch_err}\n\nKlik OK en scan opnieuw met een nieuw design.")
                self.active_event.linked_design_path = ""
                self.active_event.save(config.EVENTS_DIR)
        elif err_msg == "geen design":
            # Booking nog zonder design → witte achtergrond gebruiken zodat
            # er meteen gewerkt kan worden. Klant kan later design uploaden +
            # operator op 'Ververs' klikken om alsnog branding toe te voegen.
            self._apply_design_to_template("", force_regen=True)
            print("[LINKED] Booking zonder design — witte achtergrond gebruikt")
        elif err_msg:
            # Booking OK maar design fetch faalde door netwerk/serverfout
            QMessageBox.warning(self, "Design probleem",
                f"Booking is gekoppeld, maar design kon niet opgehaald worden:\n{err_msg}\n\n"
                "Klik 'Ververs' zodra er internet is.")

        self._update_linked_card_visibility()
        self._start_linked_uploader()
        if self.active_event:
            print(f"[LINKED] Event gekoppeld: {self.active_event.linked_booking_label}")

        # Als de gebruiker vanaf de Welcome-page kwam: terug naar idle, die
        # routeert nu automatisch naar de normale tap-to-start idle omdat
        # er een booking gekoppeld is.
        if self.stack.currentIndex() == self.pages.get("welcome", -1):
            print("[UI] Coupling klaar — vanaf welcome → idle (normale flow)")
            self._go_idle()

    def _get_cloud_templates_for_booking(self, booking_id: str) -> list:
        """Lees cloud templates[] uit de booking-cache voor dit booking_id.

        De cache wordt geschreven door cloud_booking.fetch_booking. Returns []
        als geen cache of geen templates[]-veld.
        """
        if not booking_id:
            return []
        ev = self.active_event
        token = getattr(ev, 'linked_token', '') if ev else ''
        if not token:
            return []
        try:
            from cloud_booking import _read_booking_cache, extract_templates
            cached = _read_booking_cache(token)
            if not cached:
                return []
            return extract_templates(cached)
        except Exception as e:
            print(f"[LINKED] Kon cloud templates niet lezen: {e}")
            return []

    def _apply_design_to_template(self, local_design_path, force_regen=False):
        """Synchroon (lokaal) — valideer formaat + genereer linked templates.

        Twee paden:
          A. Cloud-template flow (Fase 2+): cloud heeft templates[] in de
             booking-cache → loop ze, download per template de achtergrond,
             schrijf elk weg als linked_<booking>_<template_id>.json.
          B. Legacy auto-gen (geen cloud templates): genereer lokaal 2-foto +
             3-foto varianten op basis van ev.printer_mode + meegegeven design.

        Bij lege/missende local_design_path wordt een wit-achtergrond template
        gegenereerd (booking nog zonder design — niet-blokkerend).

        Args:
            force_regen: True = altijd overschrijven (Ververs-knop, eerste
                         coupling). False = bestaande templates met user-
                         edits behouden (auto-couple bij startup).
        """
        ev = self.active_event
        if not ev:
            return False, "Geen actief event"
        booking_id = ev.linked_booking_id
        if not booking_id:
            return False, "Geen booking_id"

        # ── Pad A: cloud templates beschikbaar? ──────────────────────────
        cloud_templates = self._get_cloud_templates_for_booking(booking_id)
        if cloud_templates:
            print(f"[LINKED] {len(cloud_templates)} cloud template(s) gevonden in booking-cache "
                  f"— gebruik Pad A (cloud-template flow)")
            return self._apply_cloud_templates(cloud_templates, force_regen)
        else:
            print(f"[LINKED] Geen cloud templates in booking-cache — val terug op "
                  f"Pad B (legacy auto-gen). Mogelijke oorzaken:\n"
                  f"  1. Edge function get-photobooth-booking is nog niet v2 (geen 'templates' veld)\n"
                  f"  2. Booking-cache is verouderd — klik 'Ververs' om opnieuw te fetchen\n"
                  f"  3. Klant heeft nog geen templates in portaal gemaakt")

        # ── Pad B: legacy auto-gen ───────────────────────────────────────
        pm = ev.printer_mode
        # Legacy → nieuwe mode-naam (voor de zekerheid, mocht migratie nog
        # niet hebben gedraaid)
        if pm == "canon":
            pm = "4x6"
        elif pm == "dnp":
            pm = "3strips"
        # Lege design-path = booking zonder design → witte achtergrond,
        # GEEN format-validatie nodig.
        design_path_for_template = ""
        if local_design_path and os.path.isfile(local_design_path):
            from cloud_booking import validate_design_format
            # 4x3 wordt nog niet door cloud ondersteund — val terug op standard
            # (1.5:1 aspect, zelfde als 4x6 Canon-design).
            if pm == "3strips":
                cloud_check_mode = "premium"
            else:  # 4x6 of 4x3
                cloud_check_mode = "standard"
            ok, vmsg = validate_design_format(local_design_path, cloud_check_mode)
            if not ok:
                return False, vmsg
            design_path_for_template = local_design_path

        from template_model import make_linked_template
        os.makedirs(config.TEMPLATES_DIR, exist_ok=True)

        # Verwijder oude variant-loze linked_<id>.json (uit oudere versies)
        old_single = os.path.join(config.TEMPLATES_DIR, f"linked_{booking_id}.json")
        if os.path.isfile(old_single):
            try:
                os.remove(old_single)
                print(f"[LINKED] Oude template verwijderd: {old_single}")
            except OSError:
                pass

        # NOOIT een 2-foto variant — niet bij Verhuurophalen én niet bij Hippe.
        # De legacy auto-gen maakt alleen nog de 3-foto strip (fallback voor
        # designs zonder cloud-template). De 4-foto 2x2 opmaak loopt via het
        # portaal (cloud-template, Pad A). Een eventuele oude 2-foto variant
        # wordt opgeruimd zodat hij niet meer in de keuzelijst blijft hangen.
        counts = (3,)
        stale_2foto = os.path.join(config.TEMPLATES_DIR,
                                   f"linked_{booking_id}_2foto.json")
        if os.path.isfile(stale_2foto):
            try:
                os.remove(stale_2foto)
                print("[LINKED] Oude 2-foto variant verwijderd")
            except OSError:
                pass
        variants_created = []
        for count in counts:
            tmpl_path = os.path.join(config.TEMPLATES_DIR, f"linked_{booking_id}_{count}foto.json")
            if os.path.isfile(tmpl_path) and not force_regen:
                print(f"[LINKED] Template bestaat al — behoud user-edits: linked_{booking_id}_{count}foto.json")
                continue
            tmpl = make_linked_template(pm, count, design_path_for_template, booking_id)
            tmpl.name = f"Event {booking_id[:8]} ({count} foto's)"
            try:
                tmpl.save(tmpl_path)
                variants_created.append(tmpl)
                print(f"[LINKED] Template gegenereerd: linked_{booking_id}_{count}foto.json")
            except Exception as e:
                return False, f"Template opslaan mislukt: {e}"

        # Template-keuze: alleen overschrijven als er nog niets gekozen is
        # OF als de huidige keuze geen linked-variant van DEZE booking is.
        # Anders respecteert het de user-keuze tussen 2/3 foto.
        # Er bestaat alleen nog een 3-foto variant (Pad B), dus de keuze is
        # eenduidig — geen 2/3-keuzepagina meer.
        ev.template_name = f"Event {booking_id[:8]} (3 foto's)"
        # Belangrijk: clear event.background_path zodat template-bg uit cloud wint
        ev.background_path = ""
        ev.save(config.EVENTS_DIR)
        if hasattr(self, '_layout_categories_container'):
            try:
                self._load_settings_templates()
            except Exception as e:
                print(f"[LINKED] Layout refresh waarschuwing: {e}")
        return True, ""

    def _apply_cloud_templates(self, cloud_templates: list, force_regen: bool):
        """Pad A: schrijf cloud templates naar lokale JSON-bestanden.

        Per cloud template:
          1. Download achtergrond via fetch_template_bg (None → wit)
          2. Bouw Template via from_cloud_template
          3. Schrijf naar linked_<booking_id>_tmpl_<template_id>.json

        force_regen=False respecteert user-edits (overschrijft niet).
        """
        from template_model import Template
        from cloud_booking import fetch_template_bg

        ev = self.active_event
        if not ev:
            return False, "Geen actief event"
        booking_id = ev.linked_booking_id
        token = getattr(ev, 'linked_token', '')
        os.makedirs(config.TEMPLATES_DIR, exist_ok=True)

        # Verwijder legacy auto-gen varianten (2foto/3foto) zodat ze niet
        # naast de cloud-templates in de grid blijven hangen. Cloud is leidend.
        for count in (2, 3):
            legacy_path = os.path.join(config.TEMPLATES_DIR,
                                       f"linked_{booking_id}_{count}foto.json")
            if os.path.isfile(legacy_path):
                try:
                    os.remove(legacy_path)
                    print(f"[LINKED] Legacy auto-gen verwijderd: {legacy_path}")
                except OSError:
                    pass

        # Slimme cleanup: delete lokale files die NIET meer in de cloud-lijst
        # voorkomen, MAAR ALLEEN als de cloud-lijst non-empty is. Een lege
        # lijst betekent waarschijnlijk netwerkfout/bug — dan niets wissen.
        # Een non-empty lijst betekent klant heeft expliciet templates
        # verwijderd in portaal → die mogen ook lokaal weg.
        valid_template_ids = {ct.get("id", "") for ct in cloud_templates if ct.get("id")}
        if not valid_template_ids:
            print(f"[LINKED-CLOUD] Cloud-lijst leeg — geen cleanup (bug-safe)")
        else:
            try:
                existing_cloud_files = [
                    f for f in os.listdir(config.TEMPLATES_DIR)
                    if f.startswith(f"linked_{booking_id}_tmpl_") and f.endswith(".json")
                ]
                # Match safe_id-vorm (UUID sanitization) tegen valid ids
                valid_safe_ids = {
                    "".join(c if c.isalnum() or c in "-_" else "_" for c in vid)
                    for vid in valid_template_ids
                }
                for fname in existing_cloud_files:
                    middle = fname[len(f"linked_{booking_id}_tmpl_"):-len(".json")]
                    if middle in valid_safe_ids:
                        continue  # nog steeds geldig
                    # Niet meer in cloud → user heeft 'm in portaal verwijderd
                    fpath = os.path.join(config.TEMPLATES_DIR, fname)
                    try:
                        os.remove(fpath)
                        print(f"[LINKED-CLOUD] Verwijderd (uit portaal weg): {fname}")
                    except OSError as e:
                        print(f"[LINKED-CLOUD] Kon niet verwijderen {fname}: {e}")
                    try:
                        if os.path.isfile(fpath + ".sig"):
                            os.remove(fpath + ".sig")
                    except OSError:
                        pass
            except OSError:
                pass

        # Sorteer templates: is_default eerst, dan sort_order, dan naam
        sorted_templates = sorted(
            [ct for ct in cloud_templates if ct.get("id")],
            key=lambda c: (
                not c.get("is_default", False),   # default eerst (False < True)
                c.get("sort_order", 0),
                c.get("name", ""),
            )
        )

        first_template_name = None
        default_template_name = None
        applied_count = 0
        skipped_count = 0
        print(f"[LINKED-CLOUD] Verwerken van {len(sorted_templates)} cloud templates "
              f"(force_regen={force_regen})")
        for ct in sorted_templates:
            tmpl_id = ct.get("id", "")
            if not tmpl_id:
                continue
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in tmpl_id)
            local_path = os.path.join(
                config.TEMPLATES_DIR, f"linked_{booking_id}_tmpl_{safe_id}.json"
            )
            # Verhuurophalen: het PORTAAL is leidend — de klant past daar de
            # foto-posities aan en de booth moet volgen. Lokale edits bestaan
            # in deze modus niet. Signature (frames+bg+canvas) detecteert
            # portaal-wijzigingen zodat we alleen dán opnieuw genereren (en
            # niet elke 60s-refresh de achtergrond opnieuw downloaden).
            cloud_sig = None
            sig_path = local_path + ".sig"
            if self.backend_brand == 'huren':
                import json as _json
                cloud_sig = _json.dumps({
                    "frames": ct.get("frames"),
                    "bg": ct.get("background_url") or "",
                    "cw": ct.get("canvas_w"), "ch": ct.get("canvas_h"),
                }, sort_keys=True)

            skip_existing = False
            if os.path.isfile(local_path) and not force_regen:
                if cloud_sig is not None:
                    try:
                        with open(sig_path, "r", encoding="utf-8") as sf:
                            skip_existing = (sf.read() == cloud_sig)
                    except OSError:
                        skip_existing = False
                    if not skip_existing:
                        print(f"[LINKED-CLOUD] Portaal-wijziging gedetecteerd "
                              f"— template wordt ververst: {safe_id[:8]}")
                else:
                    skip_existing = True

            if skip_existing:
                print(f"[LINKED-CLOUD] Behoud lokale versie: "
                      f"linked_{booking_id}_tmpl_{safe_id}.json")
                skipped_count += 1
                # Toch de naam onthouden voor default-selectie
                try:
                    existing = Template.load(local_path)
                    name = existing.name
                except Exception:
                    name = ct.get("name") or f"Template {safe_id[:8]}"
                if first_template_name is None:
                    first_template_name = name
                if ct.get("is_default") and default_template_name is None:
                    default_template_name = name
                continue

            # Download achtergrond (kan leeg zijn → wit)
            bg_local = ""
            if ct.get("background_url"):
                bg_path, bg_err = fetch_template_bg(token, tmpl_id, booking_id,
                                                    brand=self.backend_brand)
                if bg_path:
                    bg_local = bg_path
                elif bg_err:
                    print(f"[LINKED-CLOUD] Bg fetch waarschuwing voor "
                          f"template {tmpl_id}: {bg_err}")

            try:
                tmpl = Template.from_cloud_template(ct, bg_local)
            except Exception as e:
                print(f"[LINKED-CLOUD] Kan template {tmpl_id} niet parsen: {e}")
                continue

            # Display-naam: cloud-naam + booking-prefix zodat operator weet
            # bij welk event hij hoort
            display_name = ct.get("name") or f"Template {safe_id[:8]}"
            tmpl.name = f"Event {booking_id[:8]} — {display_name}"
            try:
                tmpl.save(local_path)
                applied_count += 1
                print(f"[LINKED-CLOUD] Template opgeslagen: {os.path.basename(local_path)} "
                      f"({len(tmpl.frames)} frames, is_default={ct.get('is_default')}, "
                      f"sort_order={ct.get('sort_order', 0)})")
                if cloud_sig is not None:
                    try:
                        with open(sig_path, "w", encoding="utf-8") as sf:
                            sf.write(cloud_sig)
                    except OSError:
                        pass  # geen sig = volgende refresh genereert opnieuw
            except Exception as e:
                print(f"[LINKED-CLOUD] Save fout voor {tmpl_id}: {e}")
                continue

            if first_template_name is None:
                first_template_name = tmpl.name
            if ct.get("is_default") and default_template_name is None:
                default_template_name = tmpl.name

        print(f"[LINKED-CLOUD] Klaar: {applied_count} opgeslagen, {skipped_count} overgeslagen "
              f"(default='{default_template_name}', first='{first_template_name}')")

        # Set active template_name:
        # - nog niet gezet op een cloud-template variant → kies default
        # - huidige template is een gedeletete cloud-template → kies default
        chosen = default_template_name or first_template_name
        current = ev.template_name or ""
        prefix = f"Event {booking_id[:8]} — "
        valid_cloud_names = set()
        for ct in cloud_templates:
            display_name = ct.get("name") or ""
            if display_name:
                valid_cloud_names.add(f"Event {booking_id[:8]} — {display_name}")
        needs_reset = (
            not current.startswith(prefix)
            or (valid_cloud_names and current not in valid_cloud_names)
        )
        if chosen and needs_reset:
            ev.template_name = chosen
            print(f"[LINKED-CLOUD] Active template_name → '{chosen}' "
                  f"(was '{current}')")
        ev.background_path = ""  # cloud-template wint
        ev.save(config.EVENTS_DIR)
        if hasattr(self, '_layout_categories_container'):
            try:
                self._load_settings_templates()
            except Exception as e:
                print(f"[LINKED-CLOUD] Layout refresh waarschuwing: {e}")
        return True, ""

    def _fetch_and_apply_linked_design(self) -> tuple[bool, str]:
        """Synchroon: download design + delegate naar _apply_design_to_template.

        Gebruikt voor auto-recouple bij startup (blocking is acceptabel daar).
        Voor interactieve flows: gebruik CouplingWorker (async).

        Bij geen design beschikbaar: template met witte achtergrond aanmaken
        zodat de booth meteen bruikbaar is.
        """
        ev = self.active_event
        if not ev:
            return False, "Geen actief event"
        token = ev.linked_token
        booking_id = ev.linked_booking_id
        design_path = ev.linked_design_path
        if not token or not booking_id:
            return False, "Geen geldige koppeling"
        if not design_path:
            # Geen design nog geüpload → wit-achtergrond template (niet-blokkerend)
            return self._apply_design_to_template("")

        from cloud_booking import fetch_design
        local, err = fetch_design(token, design_path, booking_id)
        if not local:
            # Download mislukt → fallback naar wit zodat booth bruikbaar blijft
            self._apply_design_to_template("")
            return False, err or "Design fetch mislukt"

        return self._apply_design_to_template(local)

    def _start_linked_uploader(self):
        """Start upload-worker voor het gekoppelde event."""
        ev = self.active_event
        if not ev or not ev.linked_booking_id or not ev.linked_token:
            return
        try:
            from cloud_uploader import start_worker
            w = start_worker(ev.linked_booking_id, ev.linked_token,
                             brand=self.backend_brand)
            w.progress_changed.connect(lambda _s: self._update_linked_progress())
        except Exception as e:
            print(f"[LINKED] Uploader start fout: {e}")

    def _on_printer_mode_changed_v2(self, mode: str):
        """Persisteer printer-modus (4x3 | 4x6 | 3strips) via active_event.

        Triggered door de 3-knop selector boven de Layout-grid. Doet:
        1. mode persisteren
        2. linked templates opnieuw genereren voor de nieuwe mode (canvas
           verschilt per mode — 1200x900 voor 4x3, 600x1800 voor 4x6,
           600x1200 voor 3strips). User-edits van vorige mode gaan verloren.
        3. layout-grid verversen
        """
        if mode not in ("4x3", "4x6", "3strips"):
            return
        if self.active_event:
            self.active_event.printer_mode = mode
            self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Printer-modus: {mode}")
        # Regenereer linked templates voor de nieuwe modus
        self._regenerate_linked_for_mode_change()
        # DNP profielen-card zit nu in een verborgen dialog — geen runtime
        # zichtbaarheid meer op deze plek.
        # Ververs layout-lijst
        if hasattr(self, '_layout_categories_container'):
            try:
                self._load_settings_templates()
            except Exception as ex:
                print(f"[SETTINGS] Layout-refresh overgeslagen: {ex}")

    def _regenerate_linked_for_mode_change(self):
        """Genereer linked templates opnieuw voor de huidige printer-modus.

        Aangeroepen bij mode-switch. Probeert het bestaande design uit de
        lokale cloud_cache te hergebruiken; valt terug op wit als geen
        cache-bestand bestaat. force_regen=True overschrijft user-edits
        van de vorige modus (canvas-grootte verschilt anders niet matchen).
        """
        ev = self.active_event
        if not ev or ev.booth_mode != 'linked' or not ev.linked_booking_id:
            return
        local_design = ""
        booking_id = ev.linked_booking_id
        try:
            from cloud_booking import _design_cache_path
            for ext in ('png', 'jpg', 'jpeg'):
                p = _design_cache_path(booking_id, ext)
                if os.path.isfile(p):
                    local_design = p
                    break
        except Exception as e:
            print(f"[LINKED] Cache-lookup fout: {e}")
        try:
            self._apply_design_to_template(local_design, force_regen=True)
        except Exception as ex:
            print(f"[LINKED] Regenerate fout: {ex}")

    def _on_pin_button_clicked(self):
        """Open touchscreen PIN keypad to set a new PIN code."""
        entered, ok = PinDialog.get_pin(self, t("pin_code").rstrip(":"))
        if ok and self.active_event:
            self.active_event.pin_code = entered.strip()
            self.active_event.save(config.EVENTS_DIR)
            self._update_pin_button_text()
            print(f"[SETTINGS] PIN: {'ingesteld' if self.active_event.pin_code else 'verwijderd'}")

    def _on_pin_clear(self):
        """Clear the PIN code."""
        if self.active_event:
            self.active_event.pin_code = ""
            self.active_event.save(config.EVENTS_DIR)
            self._update_pin_button_text()
            print("[SETTINGS] PIN: verwijderd")

    def _update_pin_button_text(self):
        """Update PIN button label to show current status."""
        pin = ""
        if self.active_event:
            pin = self.active_event.pin_code
        if pin:
            self._pin_button.setText("●" * len(pin) + "  " + t("pin_change"))
        else:
            self._pin_button.setText(t("no_pin"))

    # ── Voucher Settings ──────────────────────────

    def _on_payment_method_changed(self, method):
        """Wissel betaalmethode (none/stripe/sumup/voucher/custom).
        Updatet legacy velden + toont juiste config-card.

        Bij 'custom': vraag wachtwoord (tenzij dit event al ontgrendeld is).
        """
        ev = self.active_event
        if not ev:
            return
        # Custom flow: vereist wachtwoord-bevestiging tenzij al ontgrendeld
        if method == "custom" and not getattr(ev, 'custom_flow_unlocked', False):
            if not self._prompt_custom_unlock():
                # Verkeerde code of geannuleerd — springt radio terug naar
                # vorige keuze. We bepalen die uit het huidige payment_method.
                fallback = getattr(ev, 'payment_method', 'none') or 'none'
                if fallback == 'custom':
                    fallback = 'none'
                if hasattr(self, '_payment_method_radios') and fallback in self._payment_method_radios:
                    self._payment_method_radios[fallback].blockSignals(True)
                    self._payment_method_radios[fallback].setChecked(True)
                    self._payment_method_radios[fallback].blockSignals(False)
                return
            ev.custom_flow_unlocked = True
        ev.payment_method = method
        # Sync legacy velden zodat bestaande logic blijft werken
        ev.payment_enabled = (method == "stripe")
        ev.sumup_enabled = (method == "sumup")
        ev.save(config.EVENTS_DIR)
        # Sync de hidden toggles (voorkomt dubbel-events)
        if hasattr(self, '_payment_toggle'):
            self._payment_toggle.blockSignals(True)
            self._payment_toggle.setChecked(method == "stripe")
            self._payment_toggle.blockSignals(False)
        if hasattr(self, '_sumup_toggle'):
            self._sumup_toggle.blockSignals(True)
            self._sumup_toggle.setChecked(method == "sumup")
            self._sumup_toggle.blockSignals(False)
        # Toon/verberg cards. Custom-mode toont Stripe + Voucher + Custom-card
        # tegelijk omdat custom flow van alle drie de configuraties gebruik maakt.
        if hasattr(self, '_payment_card'):
            self._payment_card.setVisible(method in ("stripe", "custom"))
        if hasattr(self, '_sumup_card'):
            self._sumup_card.setVisible(method == "sumup")
        if hasattr(self, '_voucher_card'):
            self._voucher_card.setVisible(method in ("voucher", "custom"))
        if hasattr(self, '_custom_card'):
            self._custom_card.setVisible(method == "custom")
        # Laad UI velden indien relevant
        if method in ("voucher", "custom"):
            self._refresh_voucher_ui()
        if method == "custom":
            self._refresh_custom_ui()
        print(f"[SETTINGS] Betaalmethode gewijzigd naar: {method}")

    def _prompt_custom_unlock(self):
        """Toon wachtwoord-dialog. Returnt True bij juiste code, False anders."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout
        from custom_flow import is_valid_unlock_code

        dlg = QDialog(self)
        dlg.setWindowTitle(t("custom_unlock_title"))
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(f"background: {config.COLOR_BG}; color: {config.COLOR_TEXT};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        prompt = QLabel(t("custom_unlock_prompt"))
        prompt.setFont(QFont("DM Sans", 11))
        prompt.setWordWrap(True)
        lay.addWidget(prompt)

        inp = QLineEdit()
        inp.setEchoMode(QLineEdit.Password)
        inp.setFont(QFont("DM Sans", 14))
        inp.setMinimumHeight(40)
        inp.setStyleSheet(
            f"QLineEdit {{ background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; "
            f"border-radius: 6px; padding: 6px 10px; color: {config.COLOR_TEXT}; }}"
            f"QLineEdit:focus {{ border-color: {config.COLOR_PRIMARY}; }}"
        )
        lay.addWidget(inp)

        err = QLabel("")
        err.setFont(QFont("DM Sans", 10))
        err.setStyleSheet(f"color: {config.COLOR_DANGER};")
        lay.addWidget(err)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 6px; padding: 8px 16px; }}"
        )
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }}"
        )
        result = {"ok": False}
        def _check():
            if is_valid_unlock_code(inp.text()):
                result["ok"] = True
                dlg.accept()
            else:
                err.setText(t("custom_unlock_wrong"))
                inp.selectAll()
                inp.setFocus()
        ok_btn.clicked.connect(_check)
        inp.returnPressed.connect(_check)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        inp.setFocus()
        dlg.exec_()
        return result["ok"]

    def _on_custom_bg_choose(self, which):
        """File picker voor keuzescherm- of betaalscherm-achtergrond.

        which: "choice" of "payment"
        """
        try:
            ev = self.active_event
            if not ev:
                return
            from PyQt5.QtWidgets import QFileDialog
            current = (ev.custom_choice_bg_path if which == "choice"
                       else ev.custom_payment_bg_path)
            start_dir = os.path.dirname(current) if current and os.path.isfile(current) else ""
            path, _ = QFileDialog.getOpenFileName(
                self, t("custom_bg_choose"), start_dir,
                "Images (*.png *.jpg *.jpeg *.bmp)"
            )
            if not path:
                return
            if which == "choice":
                ev.custom_choice_bg_path = path
            else:
                ev.custom_payment_bg_path = path
            ev.save(config.EVENTS_DIR)
            print(f"[CUSTOM] BG {which} opgeslagen: {path}")
            self._refresh_custom_ui()
        except Exception as ex:
            import traceback
            print(f"[CUSTOM] _on_custom_bg_choose({which}) crash: {ex}")
            traceback.print_exc()

    def _on_custom_bg_clear(self, which):
        """Wis achtergrond-pad."""
        try:
            ev = self.active_event
            if not ev:
                return
            if which == "choice":
                ev.custom_choice_bg_path = ""
            else:
                ev.custom_payment_bg_path = ""
            ev.save(config.EVENTS_DIR)
            self._refresh_custom_ui()
        except Exception as ex:
            import traceback
            print(f"[CUSTOM] _on_custom_bg_clear({which}) crash: {ex}")
            traceback.print_exc()

    def _on_custom_choice_timeout_changed(self, new_val):
        """Save keuzescherm-timeout (touch-spin on_change callback)."""
        try:
            ev = self.active_event
            if not ev:
                return
            ev.custom_choice_timeout = int(new_val)
            ev.save(config.EVENTS_DIR)
        except Exception as ex:
            import traceback
            print(f"[CUSTOM] _on_custom_choice_timeout_changed crash: {ex}")
            traceback.print_exc()

    def _on_custom_payment_timeout_changed(self, new_val):
        """Save betaalscherm-timeout."""
        try:
            ev = self.active_event
            if not ev:
                return
            ev.custom_payment_timeout = int(new_val)
            ev.save(config.EVENTS_DIR)
        except Exception as ex:
            import traceback
            print(f"[CUSTOM] _on_custom_payment_timeout_changed crash: {ex}")
            traceback.print_exc()

    def _refresh_custom_ui(self):
        """Vul de custom-card velden vanuit het actieve event."""
        try:
            ev = self.active_event
            if not ev or not hasattr(self, '_custom_choice_bg_label'):
                return
            # Achtergrond labels — extra defensief: cast naar str + lege fallback
            ch_bg = ev.custom_choice_bg_path or ""
            pay_bg = ev.custom_payment_bg_path or ""
            self._custom_choice_bg_label.setText(
                os.path.basename(ch_bg) if ch_bg else t("custom_bg_default")
            )
            self._custom_payment_bg_label.setText(
                os.path.basename(pay_bg) if pay_bg else t("custom_bg_default")
            )
            # Timeouts via _touch_spin_set — graceful als spin-attributen missen
            if hasattr(self, '_custom_choice_timeout_spin'):
                self._touch_spin_set(self._custom_choice_timeout_spin,
                                     int(ev.custom_choice_timeout or 30))
            if hasattr(self, '_custom_payment_timeout_spin'):
                self._touch_spin_set(self._custom_payment_timeout_spin,
                                     int(ev.custom_payment_timeout or 120))
        except Exception as ex:
            import traceback
            print(f"[CUSTOM] _refresh_custom_ui crash: {ex}")
            traceback.print_exc()

    def _refresh_voucher_ui(self):
        """Toon de juiste state (config of result) op basis van of er codes zijn."""
        ev = self.active_event
        if not ev:
            return
        try:
            import voucher
            store = voucher.load_store(ev.id)
        except Exception as e:
            print(f"[VOUCHER] Kon store niet laden: {e}")
            return
        codes = store.get("codes", [])
        s = voucher.stats(codes)

        if s["total"] == 0:
            # STATE A: nog geen codes — toon configuratie
            self._voucher_state_config.setVisible(True)
            self._voucher_state_result.setVisible(False)
        else:
            # STATE B: codes bestaan — toon stats + acties
            self._voucher_state_config.setVisible(False)
            self._voucher_state_result.setVisible(True)
            self._voucher_total_label.setText(f"{t('voucher_total_label')}: {s['total']}")
            self._voucher_used_label.setText(f"{t('voucher_used_label')}: {s['used']}")
            avail_color = config.COLOR_DANGER if s["available"] == 0 else config.COLOR_SUCCESS
            self._voucher_avail_label.setText(f"{t('voucher_available_label')}: {s['available']}")
            self._voucher_avail_label.setStyleSheet(f"color: {avail_color}; font-weight: bold;")

    def _on_voucher_generate(self):
        """Genereer codes op basis van config-velden, schakel naar resultaat-state."""
        ev = self.active_event
        if not ev:
            return
        count = self._voucher_count_spin.value()
        mid_len = getattr(self._voucher_midlen_spin, "_value", 6)
        # Lees geselecteerde charset uit radio-group
        charset = "alphanum"
        for value, rb in self._voucher_charset_radios.items():
            if rb.isChecked():
                charset = value
                break
        try:
            import voucher
            new_codes = voucher.generate_codes(
                prefix="",
                suffix="",
                middle_length=int(mid_len),
                middle_chars=charset,
                count=int(count),
            )
            voucher.update_config(ev.id, {
                "prefix": "",
                "suffix": "",
                "middle_length": int(mid_len),
                "middle_chars": charset,
            })
            added = voucher.add_codes_to_store(ev.id, new_codes)
            print(f"[VOUCHER] {added} codes gegenereerd ({count} gevraagd, {mid_len} tekens, {charset})")
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("voucher_generate"), f"Fout: {e}")
            print(f"[VOUCHER] Generate fout: {e}")
            return
        self._refresh_voucher_ui()

    def _on_voucher_view(self):
        """Toon dialog met alle codes + status."""
        ev = self.active_event
        if not ev:
            return
        import voucher
        store = voucher.load_store(ev.id)
        codes = store.get("codes", [])
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                      QListWidgetItem, QPushButton)
        dlg = QDialog(self)
        dlg.setWindowTitle(t("voucher_view"))
        dlg.setMinimumSize(440, 500)
        dlg.setStyleSheet(f"background: {config.COLOR_BG}; color: {config.COLOR_TEXT};")
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        lst.setStyleSheet(
            f"QListWidget {{ background: {config.COLOR_INPUT_BG}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 6px; "
            f"font-family: 'Consolas', monospace; font-size: 13px; padding: 6px; }}"
        )
        for c in codes:
            mark = " (gebruikt)" if c.get("used") else ""
            item = QListWidgetItem(f"{c.get('code', '?')}{mark}")
            if c.get("used"):
                item.setForeground(QColor(config.COLOR_TEXT_DIM))
            lst.addItem(item)
        lay.addWidget(lst)
        close_btn = QPushButton("OK")
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
            f"border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }}"
        )
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec_()

    def _on_voucher_export_choose(self):
        """Vraag wat te exporteren (gebruikt/ongebruikt/alle), dan opslaan."""
        ev = self.active_event
        if not ev:
            return
        import voucher
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QPushButton, QLabel,
                                      QFileDialog, QMessageBox)
        store = voucher.load_store(ev.id)
        if not store["codes"]:
            QMessageBox.information(self, t("voucher_export_btn"), t("voucher_no_codes"))
            return

        # Sub-keuze dialog
        dlg = QDialog(self)
        dlg.setWindowTitle(t("voucher_export_btn"))
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(f"background: {config.COLOR_BG}; color: {config.COLOR_TEXT};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel(t("voucher_export_choose"))
        title.setFont(QFont("DM Sans", 12, QFont.Bold))
        lay.addWidget(title)

        choice = {"value": None}

        def _make_choice_btn(label, key):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(QFont("DM Sans", 11))
            b.setMinimumHeight(40)
            b.setStyleSheet(
                f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: {config.COLOR_TEXT_ON_PRIMARY}; "
                f"border: none; border-radius: 6px; padding: 8px; text-align: left; padding-left: 16px; }}"
                f"QPushButton:hover {{ background: {config.COLOR_SECONDARY_HOVER}; }}"
            )
            def _click():
                choice["value"] = key
                dlg.accept()
            b.clicked.connect(_click)
            return b

        lay.addWidget(_make_choice_btn(t("voucher_export_used"),   "used"))
        lay.addWidget(_make_choice_btn(t("voucher_export_unused"), "unused"))
        lay.addWidget(_make_choice_btn(t("voucher_export_all"),    "all"))

        cancel_btn = QPushButton(t("cancel"))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {config.COLOR_TEXT_DIM}; "
            f"border: 1px solid {config.COLOR_BORDER}; border-radius: 6px; padding: 8px; }}"
        )
        cancel_btn.clicked.connect(dlg.reject)
        lay.addWidget(cancel_btn)

        if dlg.exec_() != QDialog.Accepted or not choice["value"]:
            return

        kind = choice["value"]
        codes = store["codes"]
        if kind == "used":
            subset = [c for c in codes if c.get("used")]
            content = voucher.export_txt(subset)
            ext = "txt"
        elif kind == "unused":
            subset = [c for c in codes if not c.get("used")]
            content = voucher.export_txt(subset)
            ext = "txt"
        else:  # all
            content = voucher.export_csv(codes)
            ext = "csv"

        if kind in ("used", "unused") and not subset:
            QMessageBox.information(self, t("voucher_export_btn"), t("voucher_no_codes"))
            return

        default_name = f"vouchers_{ev.name or ev.id}_{kind}.{ext}"
        path, _ = QFileDialog.getSaveFileName(
            self, t("voucher_export_btn"), default_name,
            f"{ext.upper()} files (*.{ext})"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, t("voucher_export_btn"),
                t("voucher_export_done", path=path))
        except Exception as e:
            QMessageBox.warning(self, t("voucher_export_btn"), f"Fout: {e}")

    def _on_voucher_clear_all(self):
        """Wis alle codes voor dit event (na bevestiging)."""
        ev = self.active_event
        if not ev:
            return
        from PyQt5.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, t("voucher_clear_all"), t("voucher_clear_confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return
        import voucher
        store = voucher.load_store(ev.id)
        store["codes"] = []
        voucher.save_store(ev.id, store)
        self._refresh_voucher_ui()
        print("[VOUCHER] Alle codes gewist")

    # ── SumUp Settings ──────────────────────────

    def _on_sumup_toggled(self, checked):
        """Toggle SumUp payment mode — auto-disable Stripe if enabling SumUp."""
        if self.active_event:
            self.active_event.sumup_enabled = bool(checked)
            if checked:
                # Disable Stripe when SumUp is enabled
                self.active_event.payment_enabled = False
                if hasattr(self, '_payment_toggle'):
                    self._payment_toggle.blockSignals(True)
                    self._payment_toggle.setChecked(False)
                    self._payment_toggle.blockSignals(False)
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] SumUp: {'aan' if checked else 'uit'}")
        self._update_sumup_status()

    def _update_sumup_status(self):
        """Update SumUp status label."""
        from sumup_payment import load_sumup_config
        cfg = load_sumup_config()
        if cfg:
            self._sumup_status_label.setText(
                t("sumup_configured", amount=cfg.get('amount', '?'), merchant=cfg.get('merchant_code', '?')[:8])
            )
            self._sumup_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        else:
            self._sumup_status_label.setText(t("not_configured"))
            self._sumup_status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")

    def _open_sumup_config(self):
        """Open SumUp configuration dialog."""
        from sumup_payment import load_sumup_config, save_sumup_config, test_connection

        dialog = QDialog(self)
        dialog.setWindowTitle(t("dialog_configure_clixibo"))
        dialog.setFixedSize(520, 500)
        lay = QVBoxLayout(dialog)
        lay.setSpacing(10)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel(t("clixibo_payment_terminal"))
        title.setFont(QFont("DM Sans", 16, QFont.Bold))
        lay.addWidget(title)

        lbl_style = f"color: {config.COLOR_TEXT}; font-size: 12px; font-weight: bold;"
        input_style = f"padding: 6px; border: 1px solid {config.COLOR_BORDER}; border-radius: 4px; font-size: 12px;"

        # API Key
        lbl = QLabel(t("api_key_label"))
        lbl.setStyleSheet(lbl_style)
        lay.addWidget(lbl)
        api_input = QLineEdit()
        api_input.setPlaceholderText(t("placeholder_paste_api_key"))
        api_input.setEchoMode(QLineEdit.Password)
        api_input.setStyleSheet(input_style)
        lay.addWidget(api_input)

        # Merchant Code
        lbl2 = QLabel(t("merchant_code_label"))
        lbl2.setStyleSheet(lbl_style)
        lay.addWidget(lbl2)
        merchant_input = QLineEdit()
        merchant_input.setPlaceholderText(t("placeholder_merchant_code"))
        merchant_input.setStyleSheet(input_style)
        lay.addWidget(merchant_input)

        # Pairing Code
        pair_row = QHBoxLayout()
        pair_col = QVBoxLayout()
        lbl3 = QLabel(t("solo_pairing_code_label"))
        lbl3.setStyleSheet(lbl_style)
        pair_col.addWidget(lbl3)
        pair_input = QLineEdit()
        pair_input.setPlaceholderText(t("placeholder_solo_code"))
        pair_input.setStyleSheet(input_style)
        pair_input.setFixedWidth(200)
        pair_col.addWidget(pair_input)
        pair_row.addLayout(pair_col)
        pair_btn = QPushButton(t("btn_pair"))
        pair_btn.setFont(QFont("DM Sans", 10, QFont.Bold))
        pair_btn.setFixedHeight(32)
        pair_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: white; "
            f"border: none; border-radius: 6px; padding: 4px 12px; font-size: 10px; }}"
        )
        def _pair():
            key = api_input.text().strip()
            merchant = merchant_input.text().strip()
            code = pair_input.text().strip()
            if not key or not merchant or not code:
                status_label.setText(t("fill_all_sumup_fields"))
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
                return
            status_label.setText(t("pairing_solo"))
            status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            QApplication.processEvents()
            from sumup_payment import pair_reader, _get_reader_id
            ok, msg = pair_reader(key, merchant, code)
            if ok:
                # Auto-detect reader ID and save it
                rid = _get_reader_id(key, merchant)
                if rid:
                    msg += f" (ID: {rid[:12]}...)"
                    # Save reader_id in config if we have all fields
                    amt = amount_input.text().strip() or "2.50"
                    from sumup_payment import save_sumup_config
                    save_sumup_config(key, merchant, amt, reader_id=rid)
                status_label.setText(f"OK: {msg}")
                status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            else:
                status_label.setText(msg)
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
        pair_btn.clicked.connect(_pair)
        pair_row.addWidget(pair_btn, alignment=Qt.AlignBottom)
        pair_row.addStretch()
        lay.addLayout(pair_row)

        # Amount
        amount_row = QHBoxLayout()
        lbl4 = QLabel(t("amount_eur_label"))
        lbl4.setStyleSheet(lbl_style)
        amount_row.addWidget(lbl4)
        amount_input = QLineEdit()
        amount_input.setPlaceholderText("2.50")
        amount_input.setFixedWidth(100)
        amount_input.setStyleSheet(input_style)
        amount_row.addWidget(amount_input)
        amount_row.addStretch()
        lay.addLayout(amount_row)

        # Status
        status_label = QLabel("")
        status_label.setFont(QFont("DM Sans", 11))
        status_label.setWordWrap(True)
        lay.addWidget(status_label)

        # Load existing config
        cfg = load_sumup_config()
        if cfg:
            api_input.setText(cfg.get("api_key", ""))
            merchant_input.setText(cfg.get("merchant_code", ""))
            amount_input.setText(str(cfg.get("amount", "2.50")))

        # Buttons
        btn_row = QHBoxLayout()
        test_btn = QPushButton(t("btn_test"))
        test_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        test_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 16px; }}"
        )
        def _test():
            key = api_input.text().strip()
            if not key:
                status_label.setText(t("fill_api_key"))
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
                return
            status_label.setText(t("testing_connection"))
            status_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
            QApplication.processEvents()
            ok, msg = test_connection(key)
            if ok:
                status_label.setText(f"OK: {msg}")
                status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
            else:
                status_label.setText(msg)
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
        test_btn.clicked.connect(_test)
        btn_row.addWidget(test_btn)

        save_btn = QPushButton(t("save"))
        save_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        save_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 16px; }}"
        )
        def _save():
            key = api_input.text().strip()
            merchant = merchant_input.text().strip()
            amount = amount_input.text().strip()
            if not key or not merchant or not amount:
                status_label.setText(t("fill_all_fields"))
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
                return
            try:
                float(amount)
            except ValueError:
                status_label.setText(t("invalid_amount"))
                status_label.setStyleSheet(f"color: {config.COLOR_DANGER};")
                return
            # Auto-detect reader ID
            from sumup_payment import _get_reader_id
            rid = _get_reader_id(key, merchant) or ""
            save_sumup_config(key, merchant, amount, reader_id=rid)
            self._update_sumup_status()
            if rid:
                status_label.setText(t("saved_with_reader", reader=rid[:16]))
            else:
                status_label.setText(t("saved_no_reader"))
            status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
        save_btn.clicked.connect(_save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton(t("close"))
        cancel_btn.setFont(QFont("DM Sans", 11))
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SECONDARY}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 16px; }}"
        )
        cancel_btn.clicked.connect(dialog.close)
        btn_row.addWidget(cancel_btn)

        lay.addLayout(btn_row)
        dialog.exec_()

    # ── Camera Settings ──────────────────────────

    def _refresh_camera_settings(self):
        """Fetch current camera settings and populate dropdowns."""
        def _fetch():
            try:
                mode = self.camera.get_property('mode') or '--'
                shutters = self.camera.list_property_values('shutterspeed')
                apertures = self.camera.list_property_values('aperture')
                isos = self.camera.list_property_values('iso')
                cur_shutter = self.camera.get_property('shutterspeed') or ''
                cur_aperture = self.camera.get_property('aperture') or ''
                cur_iso = self.camera.get_property('iso') or ''
                return mode, shutters, apertures, isos, cur_shutter, cur_aperture, cur_iso
            except Exception as e:
                print(f"[CAMERA] Fout bij ophalen instellingen: {e}")
                return '--', [], [], [], '', '', ''

        def _apply(result):
            mode, shutters, apertures, isos, cur_s, cur_a, cur_i = result

            # Mode display
            mode_map = {'M': 'Handmatig (M)', 'A': 'Diafragma (AV)', 'S': 'Sluitertijd (TV)',
                        'P': 'Programma (P)', 'Av': 'Diafragma (AV)', 'Tv': 'Sluitertijd (TV)'}
            self._cam_mode_label.setText(mode_map.get(mode, mode))

            # Populate shutter speed dropdown
            self._shutter_combo.blockSignals(True)
            self._shutter_combo.clear()
            if shutters:
                self._shutter_combo.addItems(shutters)
                if cur_s in shutters:
                    self._shutter_combo.setCurrentText(cur_s)
            else:
                self._shutter_combo.addItem(cur_s or '--')
            # Enable only in TV/M modes
            can_shutter = mode in ('S', 'Tv', 'M')
            self._shutter_combo.setEnabled(can_shutter)
            self._shutter_combo.blockSignals(False)

            # Populate aperture dropdown
            self._aperture_combo.blockSignals(True)
            self._aperture_combo.clear()
            if apertures:
                self._aperture_combo.addItems(apertures)
                if cur_a in apertures:
                    self._aperture_combo.setCurrentText(cur_a)
            else:
                self._aperture_combo.addItem(cur_a or '--')
            # Enable only in AV/M modes
            can_aperture = mode in ('A', 'Av', 'M')
            self._aperture_combo.setEnabled(can_aperture)
            self._aperture_combo.blockSignals(False)

            # Populate ISO dropdown
            self._iso_combo.blockSignals(True)
            self._iso_combo.clear()
            if isos:
                self._iso_combo.addItems(isos)
                if cur_i in isos:
                    self._iso_combo.setCurrentText(cur_i)
            else:
                self._iso_combo.addItem(cur_i or 'Auto')
            self._iso_combo.blockSignals(False)

            print(f"[CAMERA] Instellingen: modus={mode}, sluitertijd={cur_s}, "
                  f"diafragma={cur_a}, ISO={cur_i}")

        # Run in background thread to avoid blocking UI
        def _run():
            result = _fetch()
            QTimer.singleShot(0, lambda: _apply(result))

        threading.Thread(target=_run, daemon=True).start()

    def _on_shutter_changed(self, value):
        """Set shutter speed on camera."""
        if value and value != '--':
            threading.Thread(
                target=lambda: self.camera.set_property('shutterspeed', value),
                daemon=True
            ).start()

    def _on_aperture_changed(self, value):
        """Set aperture on camera."""
        if value and value != '--':
            threading.Thread(
                target=lambda: self.camera.set_property('aperture', value),
                daemon=True
            ).start()

    def _on_iso_changed(self, value):
        """Set ISO on camera."""
        if value and value != '--' and value != 'Auto':
            threading.Thread(
                target=lambda: self.camera.set_property('iso', value),
                daemon=True
            ).start()

    # ── Layout Editor ──────────────────────────

    def _build_layout_editor_page(self):
        """Visual layout editor page with interactive canvas (dark theme)."""
        page = QWidget()
        page.setStyleSheet("background: #1a1a1a;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 15, 20, 15)
        lay.setSpacing(10)

        # Top bar with X close button
        top = QHBoxLayout()

        # X close button (top-left)
        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedSize(44, 44)
        close_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.15); color: #ffffff; "
            "border: none; border-radius: 22px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )
        close_btn.clicked.connect(self._editor_back)
        top.addWidget(close_btn)

        self._editor_title = QLabel(t("editor_title"))
        self._editor_title.setFont(QFont("DM Sans", 24, QFont.Bold))
        self._editor_title.setStyleSheet("color: #ffffff;")
        top.addWidget(self._editor_title)
        top.addStretch()
        # Frame info label
        self._editor_info = QLabel("")
        self._editor_info.setFont(QFont("DM Sans", 14))
        self._editor_info.setStyleSheet("color: #999999;")
        top.addWidget(self._editor_info)
        lay.addLayout(top)

        # Middle area: canvas + sidebar
        middle = QHBoxLayout()

        # Canvas
        self._editor_canvas = LayoutEditorCanvas()
        self._editor_canvas.frameChanged.connect(self._on_editor_frame_changed)
        middle.addWidget(self._editor_canvas, stretch=1)

        # Sidebar with tools
        sidebar = QVBoxLayout()
        sidebar.setSpacing(12)

        dark_btn_style = (
            "QPushButton { background: rgba(255,255,255,0.15); color: #ffffff; "
            "border: none; border-radius: 8px; padding: 10px 16px; font-size: 13px; min-height: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )

        # --- Frame count control ---
        count_label = QLabel(t("editor_photos"))
        count_label.setFont(QFont("DM Sans", 11, QFont.Bold))
        count_label.setStyleSheet("color: #999999; background: transparent;")
        count_label.setAlignment(Qt.AlignCenter)
        sidebar.addWidget(count_label)

        count_row = QHBoxLayout()
        count_row.setSpacing(4)
        minus_btn = QPushButton("−")
        minus_btn.setCursor(Qt.PointingHandCursor)
        minus_btn.setFixedSize(44, 44)
        minus_btn.setFont(QFont("DM Sans", 20, QFont.Bold))
        minus_btn.setStyleSheet(dark_btn_style)
        minus_btn.clicked.connect(self._editor_remove_frame)
        count_row.addWidget(minus_btn)

        self._editor_count_label = QLabel("0")
        self._editor_count_label.setFont(QFont("DM Sans", 20, QFont.Bold))
        self._editor_count_label.setStyleSheet("color: #ffffff; background: transparent;")
        self._editor_count_label.setAlignment(Qt.AlignCenter)
        self._editor_count_label.setFixedWidth(40)
        count_row.addWidget(self._editor_count_label)

        plus_btn = QPushButton("+")
        plus_btn.setCursor(Qt.PointingHandCursor)
        plus_btn.setFixedSize(44, 44)
        plus_btn.setFont(QFont("DM Sans", 20, QFont.Bold))
        plus_btn.setStyleSheet(dark_btn_style)
        plus_btn.clicked.connect(self._editor_add_frame)
        count_row.addWidget(plus_btn)
        sidebar.addLayout(count_row)

        # Separator
        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.1);")
        sidebar.addWidget(sep)

        # --- Delete selected frame ---
        del_btn = QPushButton(t("editor_delete_selection"))
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        del_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(192,57,43,0.6); color: #ffffff; "
            f"border: none; border-radius: 8px; padding: 10px 12px; font-size: 11px; min-height: 0; }}"
            f"QPushButton:hover {{ background: rgba(192,57,43,0.8); }}"
        )
        del_btn.clicked.connect(self._editor_delete_selected)
        sidebar.addWidget(del_btn)

        # Separator
        sep2 = QLabel("")
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(255,255,255,0.1);")
        sidebar.addWidget(sep2)

        # --- Background image button (verborgen in verhuur: design uit cloud) ---
        bg_btn = QPushButton(t("editor_change_bg"))
        bg_btn.setCursor(Qt.PointingHandCursor)
        bg_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        bg_btn.setStyleSheet(dark_btn_style)
        bg_btn.clicked.connect(self._editor_change_background)
        sidebar.addWidget(bg_btn)
        bg_btn.setVisible(False)  # verhuur: design altijd uit clixibo, niet aanpasbaar

        # Separator
        sep3 = QLabel("")
        sep3.setFixedHeight(1)
        sep3.setStyleSheet("background: rgba(255,255,255,0.1);")
        sidebar.addWidget(sep3)

        # Separator
        sep4 = QLabel("")
        sep4.setFixedHeight(1)
        sep4.setStyleSheet("background: rgba(255,255,255,0.1);")
        sidebar.addWidget(sep4)

        # --- Frame position/size inputs ---
        pos_label = QLabel(t("position_size_label"))
        pos_label.setFont(QFont("DM Sans", 10, QFont.Bold))
        pos_label.setStyleSheet("color: #999999; background: transparent;")
        pos_label.setAlignment(Qt.AlignCenter)
        sidebar.addWidget(pos_label)

        dark_input_style = (
            "QSpinBox { background: rgba(255,255,255,0.12); color: #ffffff; "
            "border: 1px solid rgba(255,255,255,0.2); border-radius: 4px; "
            "padding: 2px 6px; font-size: 11px; min-height: 28px; }"
            "QSpinBox:focus { border-color: rgba(255,255,255,0.5); }"
            "QSpinBox::up-button, QSpinBox::down-button { width: 16px; }"
        )
        dark_lbl_style = "color: #cccccc; background: transparent; font-size: 11px;"

        # X position
        xy_row1 = QHBoxLayout()
        xy_row1.setSpacing(6)
        lbl_x = QLabel("X:")
        lbl_x.setStyleSheet(dark_lbl_style)
        lbl_x.setFixedWidth(20)
        xy_row1.addWidget(lbl_x)
        self._editor_x_input = QSpinBox()
        self._editor_x_input.setRange(0, 1200)
        self._editor_x_input.setStyleSheet(dark_input_style)
        self._editor_x_input.valueChanged.connect(self._on_editor_xy_changed)
        xy_row1.addWidget(self._editor_x_input)
        lbl_y = QLabel("Y:")
        lbl_y.setStyleSheet(dark_lbl_style)
        lbl_y.setFixedWidth(20)
        xy_row1.addWidget(lbl_y)
        self._editor_y_input = QSpinBox()
        self._editor_y_input.setRange(0, 1800)
        self._editor_y_input.setStyleSheet(dark_input_style)
        self._editor_y_input.valueChanged.connect(self._on_editor_xy_changed)
        xy_row1.addWidget(self._editor_y_input)
        sidebar.addLayout(xy_row1)

        # Width/Height
        xy_row2 = QHBoxLayout()
        xy_row2.setSpacing(6)
        lbl_w = QLabel("B:")
        lbl_w.setStyleSheet(dark_lbl_style)
        lbl_w.setFixedWidth(20)
        xy_row2.addWidget(lbl_w)
        self._editor_w_input = QSpinBox()
        self._editor_w_input.setRange(10, 1200)
        self._editor_w_input.setStyleSheet(dark_input_style)
        self._editor_w_input.valueChanged.connect(self._on_editor_xy_changed)
        xy_row2.addWidget(self._editor_w_input)
        lbl_h = QLabel("H:")
        lbl_h.setStyleSheet(dark_lbl_style)
        lbl_h.setFixedWidth(20)
        xy_row2.addWidget(lbl_h)
        self._editor_h_input = QSpinBox()
        self._editor_h_input.setRange(10, 1800)
        self._editor_h_input.setStyleSheet(dark_input_style)
        self._editor_h_input.valueChanged.connect(self._on_editor_xy_changed)
        xy_row2.addWidget(self._editor_h_input)
        sidebar.addLayout(xy_row2)

        # Separator
        sep5 = QLabel("")
        sep5.setFixedHeight(1)
        sep5.setStyleSheet("background: rgba(255,255,255,0.1);")
        sidebar.addWidget(sep5)

        # Name input hidden (not needed — template keeps its preset name)
        self._editor_name_input = QLineEdit()
        self._editor_name_input.setVisible(False)

        sidebar.addStretch()

        middle.addLayout(sidebar)
        lay.addLayout(middle, stretch=1)

        # Bottom bar
        bottom = QHBoxLayout()
        bottom.setSpacing(15)
        bottom.addStretch()

        save_btn = QPushButton(t("editor_save"))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        save_btn.setMinimumHeight(56)
        save_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_SUCCESS}; color: #ffffff; "
            f"border: none; border-radius: 12px; padding: 14px 40px; font-size: 16px; min-height: 0; }}"
            f"QPushButton:hover {{ background: {config.COLOR_SUCCESS_HOVER}; }}"
        )
        save_btn.clicked.connect(self._editor_save)

        back_btn = QPushButton(t("editor_back"))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setFont(QFont("DM Sans", 16, QFont.Bold))
        back_btn.setMinimumHeight(56)
        back_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.15); color: #ffffff; "
            "border: none; border-radius: 12px; padding: 14px 40px; font-size: 16px; min-height: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )
        back_btn.clicked.connect(self._editor_back)

        bottom.addWidget(back_btn)
        bottom.addWidget(save_btn)
        lay.addLayout(bottom)

        self.stack.addWidget(page)

    def _open_layout_editor(self):
        """Open layout editor for the currently selected layout."""
        try:
            self._open_layout_editor_impl()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[EDITOR] Crash in _open_layout_editor:\n{tb}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Editor fout",
                f"Kon editor niet openen:\n{type(e).__name__}: {e}\n\n"
                f"Stack trace is gelogd naar app_crash.log.")

    def _open_layout_editor_impl(self):
        if not self.active_event or not self.active_event.template_name:
            print("[EDITOR] Geen active_event of template_name")
            return
        target = self.active_event.template_name
        print(f"[EDITOR] Open editor voor template: {target!r}")
        # Search presets first, then custom templates
        all_layouts = list(get_preset_layouts())
        if os.path.isdir(config.TEMPLATES_DIR):
            from template_model import Template as TModel
            for fname in os.listdir(config.TEMPLATES_DIR):
                if fname.lower().endswith(".json"):
                    try:
                        all_layouts.append(TModel.load(os.path.join(config.TEMPLATES_DIR, fname)))
                    except Exception as ex:
                        print(f"[EDITOR] Kon {fname} niet laden: {ex}")
        # Find last match (custom templates override presets with same name)
        match = None
        for layout in all_layouts:
            if layout.name == target:
                match = layout
        if not match:
            print(f"[EDITOR] Geen match voor template_name {target!r}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Template niet gevonden",
                f"Kon template '{target}' niet vinden. Selecteer eerst een layout.")
            return
        print(f"[EDITOR] Match gevonden: {match.name}, {match.num_photos} frames, "
              f"is_triple={getattr(match, 'is_triple_strip', False)}, "
              f"is_double={match.is_double_strip}")
        self._editor_canvas.set_template(match)
        self._editor_canvas.set_event_background(self.active_event.background_path if self.active_event else "")
        self._editor_title.setText(t("editor_title"))
        self._editor_name_input.setText(match.name)
        self._editor_info.setText("")
        self._editor_update_count_label()
        if hasattr(self, '_editor_x_input'):
            self._update_editor_xy_fields()
        self.stack.setCurrentIndex(self.pages["layout_editor"])

    def _on_editor_frame_changed(self):
        """Update info label and XY fields when a frame is resized/moved."""
        canvas = self._editor_canvas
        if canvas.selected_frame >= 0 and canvas.template:
            frame = canvas.template.frames[canvas.selected_frame]
            self._editor_info.setText(
                f"Frame {canvas.selected_frame + 1}: "
                f"{frame.width}x{frame.height} px @ ({frame.x}, {frame.y})"
            )
            self._update_editor_xy_fields()

    def _update_editor_xy_fields(self):
        """Sync XY/WH spinboxes with the selected frame."""
        canvas = self._editor_canvas
        if not canvas.template or canvas.selected_frame < 0:
            return
        frame = canvas.template.frames[canvas.selected_frame]
        # Block signals to prevent feedback loop
        for inp in (self._editor_x_input, self._editor_y_input,
                    self._editor_w_input, self._editor_h_input):
            inp.blockSignals(True)
        self._editor_x_input.setValue(int(frame.x))
        self._editor_y_input.setValue(int(frame.y))
        self._editor_w_input.setValue(int(frame.width))
        self._editor_h_input.setValue(int(frame.height))
        for inp in (self._editor_x_input, self._editor_y_input,
                    self._editor_w_input, self._editor_h_input):
            inp.blockSignals(False)

    def _on_editor_xy_changed(self):
        """Update the selected frame from XY/WH spinboxes."""
        canvas = self._editor_canvas
        if not canvas.template or canvas.selected_frame < 0:
            return
        frame = canvas.template.frames[canvas.selected_frame]
        frame.x = self._editor_x_input.value()
        frame.y = self._editor_y_input.value()
        frame.width = self._editor_w_input.value()
        frame.height = self._editor_h_input.value()
        canvas.update()  # Repaint canvas
        self._editor_info.setText(
            f"Frame {canvas.selected_frame + 1}: "
            f"{frame.width}x{frame.height} px @ ({frame.x}, {frame.y})"
        )

    def _editor_update_count_label(self):
        """Update the photo count label in the editor sidebar."""
        canvas = self._editor_canvas
        if canvas.template:
            self._editor_count_label.setText(str(len(canvas.template.frames)))

    def _editor_add_frame(self):
        """Add a new photo frame to the layout."""
        canvas = self._editor_canvas
        if not canvas.template:
            return
        from template_model import PhotoFrame
        t = canvas.template
        # Canvas-grootte op basis van type (triple_strip = 600x1200)
        canvas_w, canvas_h = canvas._canvas_size()
        is_triple = getattr(t, 'is_triple_strip', False)
        # Strip-breedte voor frame-berekening (single strip = 600, double = 1200,
        # triple = 600 want canvas IS al 600 breed)
        strip_w = canvas_w if (is_triple or t.is_double_strip) else 600
        margin = 30
        frame_w = strip_w - 2 * margin
        # Aspect: 16:9 voor triple (Surface widescreen), anders 3:2 (Canon)
        aspect = (16.0 / 9.0) if is_triple else (3.0 / 2.0)
        frame_h = int(frame_w / aspect)
        # Vind onderste frame om eronder te plaatsen
        max_y = margin
        for f in t.frames:
            bottom = f.y + f.height
            if bottom > max_y:
                max_y = bottom
        y = max_y + 30  # spacing
        # Clamp tegen canvas-hoogte (NIET hardcoded 1800)
        if y + frame_h > canvas_h:
            y = max(margin, canvas_h - frame_h - margin)
        t.frames.append(PhotoFrame(x=margin, y=y, width=frame_w, height=frame_h))
        canvas.selected_frame = len(t.frames) - 1
        canvas.update()
        self._editor_update_count_label()
        self.frameChanged_emitted = True

    def _editor_remove_frame(self):
        """Remove the last photo frame from the layout."""
        canvas = self._editor_canvas
        if not canvas.template or len(canvas.template.frames) <= 1:
            return
        canvas.template.frames.pop()
        if canvas.selected_frame >= len(canvas.template.frames):
            canvas.selected_frame = len(canvas.template.frames) - 1
        canvas.update()
        self._editor_update_count_label()

    def _editor_delete_selected(self):
        """Delete the currently selected frame."""
        canvas = self._editor_canvas
        if not canvas.template or canvas.selected_frame < 0:
            return
        if len(canvas.template.frames) <= 1:
            return  # Keep at least 1 frame
        del canvas.template.frames[canvas.selected_frame]
        canvas.selected_frame = min(canvas.selected_frame,
                                     len(canvas.template.frames) - 1)
        canvas.update()
        self._editor_update_count_label()

    def _editor_save(self):
        """Save edited layout — wrapped in try/except met log + dialoog."""
        try:
            self._editor_save_impl()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[EDITOR-SAVE] Crash:\n{tb}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Opslaan mislukt",
                f"Kon template niet opslaan:\n{type(e).__name__}: {e}\n\n"
                f"Stacktrace in app_crash.log.")

    def _editor_save_impl(self):
        """Save edited layout as custom template and go back to settings."""
        canvas = self._editor_canvas
        if not canvas.template:
            print("[EDITOR-SAVE] Geen template op canvas — kan niets opslaan")
            return
        if not self.active_event:
            print("[EDITOR-SAVE] Geen active_event — terug naar settings")
            self.stack.setCurrentIndex(self.pages["settings"])
            return

        import json as _json

        t = canvas.template
        is_triple = bool(getattr(t, 'is_triple_strip', False))
        is_4x3 = bool(getattr(t, 'is_4x3_strip', False))

        # Naam-veld lezen — vallen terug op template.name (voor linked-templates
        # zodat de overwrite-by-name match krijgt en de grid-filter werkt)
        try:
            entered = self._editor_name_input.text().strip() if hasattr(self, '_editor_name_input') else ""
        except Exception:
            entered = ""
        custom_name = entered or t.name
        if not custom_name:
            custom_name = "Template"

        cut_default = is_triple or (not t.is_double_strip)

        data = {
            "name": custom_name,
            "background_path": t.background_path or "",
            "is_double_strip": t.is_double_strip,
            "is_triple_strip": is_triple,
            "is_4x3_strip": is_4x3,
            "cut_default": cut_default,
            "frames": [{"x": f.x, "y": f.y, "width": f.width, "height": f.height,
                         "rotation": getattr(f, 'rotation', 0.0)}
                        for f in t.frames],
        }

        # Zoek bestaand bestand met dezelfde naam → overwrite
        fname = None
        if os.path.isdir(config.TEMPLATES_DIR):
            for existing_fname in os.listdir(config.TEMPLATES_DIR):
                if not existing_fname.lower().endswith(".json"):
                    continue
                try:
                    fpath = os.path.join(config.TEMPLATES_DIR, existing_fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        existing = _json.load(f)
                    if existing.get("name") == custom_name:
                        fname = existing_fname
                        break
                except Exception:
                    pass

        if not fname:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"custom_{self.active_event.id}_{timestamp}.json"

        path = os.path.join(config.TEMPLATES_DIR, fname)
        os.makedirs(config.TEMPLATES_DIR, exist_ok=True)
        # Atomic write: tmp + rename
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

        self.active_event.template_name = custom_name
        self.active_event.cut_enabled = cut_default
        self.active_event.save(config.EVENTS_DIR)
        print(f"[EDITOR-SAVE] OK: '{custom_name}' ({len(t.frames)} frames, "
              f"triple={is_triple}, 4x3={is_4x3}) → {fname}")
        # Sanity-check: lees direct het bestand terug zodat we zeker weten
        # dat de write op disk staat vóór de grid-rebuild
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = _json.load(f)
            disk_frames = len(disk.get("frames", []))
            disk_triple = disk.get("is_triple_strip")
            print(f"[EDITOR-SAVE] Disk check: {disk_frames} frames, triple={disk_triple}")
        except Exception as e:
            print(f"[EDITOR-SAVE] Disk check fout: {e}")

        # Eerst stack omschakelen → settings-page wordt visible
        self.stack.setCurrentIndex(self.pages["settings"])

        # Forceer Qt event-flush zodat de page-switch verwerkt is voor de rebuild
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        # Refresh layout grid + form-velden
        try:
            self._load_settings_for_event()
        except Exception as e:
            print(f"[EDITOR-SAVE] _load_settings_for_event waarschuwing: {e}")
        try:
            self._load_settings_templates()
        except Exception as e:
            print(f"[EDITOR-SAVE] _load_settings_templates waarschuwing: {e}")

        # Nogmaals processEvents zodat de nieuwe thumbs daadwerkelijk paint-ed worden
        QApplication.processEvents()
        print("[EDITOR-SAVE] UI refresh klaar")

    def _editor_change_background(self):
        """Change the layout background image."""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Layout achtergrond kiezen",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if path:
            self._editor_canvas.set_background(path)
            print(f"[EDITOR] Achtergrond gekozen: {os.path.basename(path)}")

    def _editor_back(self):
        """Go back to settings without saving."""
        self.stack.setCurrentIndex(self.pages["settings"])

    def _launch_event(self):
        """Launch event: auto-save all settings and go to idle (fullscreen)."""
        if self.active_event:
            self.active_event.status = "active"
            self.active_event.save(config.EVENTS_DIR)
            self._save_active_event_id()
            print(f"[EVENT] Gelanceerd: {self.active_event.name}")
        self._rebuild_idle_page()
        self._restore_fullscreen_flags()
        self._go_idle()

    def _settings_back(self):
        """Return to idle from settings — restore frameless fullscreen."""
        self._rebuild_idle_page()
        self._restore_fullscreen_flags()
        self._go_idle()

    def _change_idle_background(self):
        """Change the idle screen background, save to active event."""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Startscherm achtergrond kiezen",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return

        # Save to active event
        if self.active_event:
            self.active_event.idle_background = path
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Achtergrond opgeslagen: {os.path.basename(path)}")

        # Also save to global settings as fallback
        import json
        settings = {}
        if os.path.isfile(config.SETTINGS_FILE):
            try:
                with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception:
                pass
        settings["idle_background"] = path
        try:
            with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Update preview thumbnail
        self._update_bg_preview()

    def _get_default_idle_path(self):
        """Find the best default idle screen for current physical screen resolution."""
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            # Use physical pixel size (not logical) to ignore DPI scaling
            dpr = screen.devicePixelRatio()
            screen_w = int(screen.size().width() * dpr)
            screen_h = int(screen.size().height() * dpr)
        else:
            screen_w, screen_h = 1920, 1080

        # Look in idle_defaults/ for best matching ready{width} file
        defaults_dir = os.path.join(config.BUNDLE_DIR, "idle_defaults")
        if not os.path.isdir(defaults_dir):
            defaults_dir = os.path.join(config.BASE_DIR, "idle_defaults")
        if not os.path.isdir(defaults_dir):
            return "", screen_w, screen_h

        # Collect all ready{N} files with their width
        import re
        candidates = []
        for f in os.listdir(defaults_dir):
            if not f.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            m = re.match(r"ready(\d+)\.", f, re.IGNORECASE)
            if m:
                file_w = int(m.group(1))
                candidates.append((file_w, os.path.join(defaults_dir, f)))

        if not candidates:
            return "", screen_w, screen_h

        # Exact match first
        for file_w, path in candidates:
            if file_w == screen_w:
                return path, screen_w, screen_h

        # Closest match by width
        candidates.sort(key=lambda c: abs(c[0] - screen_w))
        best_path = candidates[0][1]
        print(f"[IDLE] Scherm {screen_w}x{screen_h}, beste match: {os.path.basename(best_path)}")
        return best_path, screen_w, screen_h

    def _on_idle_mode_changed(self):
        """Handle radio button toggle between default/custom idle screen."""
        is_default = self._idle_radio_default.isChecked()
        self._idle_default_container.setVisible(is_default)
        self._idle_custom_container.setVisible(not is_default)

        if self.active_event:
            self.active_event.idle_screen_mode = "default" if is_default else "custom"
            self.active_event.save(config.EVENTS_DIR)
        self._update_bg_preview()

    def _update_bg_preview(self):
        """Update the idle background preview thumbnail in settings."""
        mode = "default"
        if self.active_event:
            mode = getattr(self.active_event, 'idle_screen_mode', 'default')

        # Set radio buttons
        self._idle_radio_default.blockSignals(True)
        self._idle_radio_custom.blockSignals(True)
        self._idle_radio_default.setChecked(mode == "default")
        self._idle_radio_custom.setChecked(mode == "custom")
        self._idle_radio_default.blockSignals(False)
        self._idle_radio_custom.blockSignals(False)

        self._idle_default_container.setVisible(mode == "default")
        self._idle_custom_container.setVisible(mode == "custom")

        # Update default preview
        default_path, sw, sh = self._get_default_idle_path()
        if default_path and os.path.isfile(default_path):
            pixmap = QPixmap(default_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._bg_preview_label.size(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._bg_preview_label.setPixmap(scaled)
                self._bg_preview_label.setText("")
                self._bg_preview_label.setStyleSheet(
                    f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_SUCCESS}; border-radius: 6px;"
                )
                self._idle_default_info.setText(
                    f"Scherm: {sw}x{sh} px\nBestand: {os.path.basename(default_path)}"
                )
        else:
            self._bg_preview_label.setPixmap(QPixmap())
            self._bg_preview_label.setText(t("default_text"))
            self._bg_preview_label.setStyleSheet(
                f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 6px; color: {config.COLOR_TEXT_DIM};"
            )
            self._idle_default_info.setText(
                f"Scherm: {sw}x{sh} px\nGeen standaard gevonden voor ready{sw}"
            )

        # Update custom preview
        custom_path = ""
        if self.active_event and self.active_event.idle_background:
            custom_path = self.active_event.idle_background
        if custom_path and os.path.isfile(custom_path):
            pixmap = QPixmap(custom_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._custom_bg_preview.size(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._custom_bg_preview.setPixmap(scaled)
                self._custom_bg_preview.setText("")
                self._custom_bg_preview.setStyleSheet(
                    f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_SUCCESS}; border-radius: 6px;"
                )
        else:
            self._custom_bg_preview.setPixmap(QPixmap())
            self._custom_bg_preview.setText(t("none_text"))
            self._custom_bg_preview.setStyleSheet(
                f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 6px; color: {config.COLOR_TEXT_DIM};"
            )

        # Resolution hint for custom mode
        self._idle_resolution_hint.setText(
            f"Tip: Maak je eigen startscherm op {sw}x{sh} pixels\n"
            f"voor de beste kwaliteit op dit scherm."
        )

    def _change_capture_screen(self):
        """Choose a capture screen image, save to active event."""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Capture scherm afbeelding kiezen",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return
        if self.active_event:
            self.active_event.capture_screen_path = path
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Capture scherm opgeslagen: {os.path.basename(path)}")
        # Invalidate cached pixmap
        self._capture_screen_pixmap = None
        self._update_cap_preview()

    def _reset_capture_screen(self):
        """Reset capture screen to default white flash."""
        if self.active_event:
            self.active_event.capture_screen_path = ""
            self.active_event.save(config.EVENTS_DIR)
            print("[SETTINGS] Capture scherm gereset naar witte flits")
        self._capture_screen_pixmap = None
        self._update_cap_preview()

    def _on_cap_duration_changed(self, value):
        """Update capture screen duration."""
        config.CAPTURE_SCREEN_DURATION_MS = value
        print(f"[SETTINGS] Capture scherm duur: {value}ms")

    def _change_intro_screen(self):
        """Choose an intro screen image."""
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Intro scherm afbeelding kiezen",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return
        if self.active_event:
            self.active_event.intro_screen_path = path
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Intro scherm opgeslagen: {os.path.basename(path)}")
        self._update_intro_preview()

    def _reset_intro_screen(self):
        """Reset intro screen to default text."""
        if self.active_event:
            self.active_event.intro_screen_path = ""
            self.active_event.save(config.EVENTS_DIR)
            print("[SETTINGS] Intro scherm gereset naar standaard tekst")
        self._update_intro_preview()

    def _on_intro_duration_changed(self, value):
        """Update intro screen duration."""
        if self.active_event:
            self.active_event.intro_duration = value
            self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Intro scherm duur: {value}s")

    def _on_intro_text_toggled(self, checked):
        if self.active_event:
            self.active_event.intro_text_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
        if hasattr(self, '_intro_text_input'):
            self._intro_text_input.setEnabled(checked)

    def _on_intro_text_changed(self, text):
        if self.active_event:
            self.active_event.intro_text = text
            self.active_event.save(config.EVENTS_DIR)

    def _on_capture_text_toggled(self, checked):
        if self.active_event:
            self.active_event.capture_text_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
        if hasattr(self, '_capture_text_input'):
            self._capture_text_input.setEnabled(checked)

    def _on_capture_text_changed(self, text):
        if self.active_event:
            self.active_event.capture_text = text
            self.active_event.save(config.EVENTS_DIR)

    def _update_intro_preview(self):
        """Update the intro screen preview thumbnail in settings."""
        path = ""
        if self.active_event:
            path = self.active_event.intro_screen_path
        if path and os.path.isfile(path):
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._intro_preview_label.size(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._intro_preview_label.setPixmap(scaled)
                self._intro_preview_label.setText("")
        else:
            self._intro_preview_label.clear()
            self._intro_preview_label.setText(t("default_text_label"))

    def _update_cap_preview(self):
        """Update the capture screen preview thumbnail in settings."""
        cap_path = ""
        if self.active_event and self.active_event.capture_screen_path:
            cap_path = self.active_event.capture_screen_path
        if cap_path and os.path.isfile(cap_path):
            pixmap = QPixmap(cap_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._cap_preview_label.size(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._cap_preview_label.setPixmap(scaled)
                self._cap_preview_label.setText("")
                self._cap_preview_label.setStyleSheet(
                    f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_SUCCESS}; border-radius: 6px;"
                )
                return
        # No capture screen set — default white flash
        self._cap_preview_label.setPixmap(QPixmap())
        self._cap_preview_label.setText(t("white_flash"))
        self._cap_preview_label.setStyleSheet(
            f"background: {config.COLOR_INPUT_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 6px; color: {config.COLOR_TEXT_DIM};"
        )

    def _rebuild_idle_page(self):
        """Rebuild idle page fully — replaces the page in the stack.

        Re-uses _build_idle_page() so license banner is always correct.
        Only use this when a full rebuild is needed (e.g. background change).
        For license banner changes, use _update_idle_license_banner() instead.

        SAFETY: Only rebuilds when in IDLE or SETTINGS state to prevent
        layout corruption during active photo sessions.
        """
        # Never rebuild while user is in a photo session — it corrupts the
        # stack layout for all pages.  Use _update_idle_license_banner() instead.
        if hasattr(self, 'state') and self.state not in (State.IDLE, State.SETTINGS):
            print(f"[IDLE] Pagina rebuild genegeerd (state={self.state}) — gebruik _update_idle_license_banner")
            self._update_idle_license_banner()
            return
        old_page = self.stack.widget(self.pages["idle"])
        new_page = self._build_idle_page()
        self.stack.removeWidget(old_page)
        old_page.deleteLater()
        self.stack.insertWidget(self.pages["idle"], new_page)
        print(f"[IDLE] Pagina herbouwd (licentie: {'ja' if self._is_logged_in() else 'nee'})")

    def _update_idle_license_banner(self):
        """Show or hide the license banner without rebuilding the idle page.

        Call this instead of _rebuild_idle_page() when only the login state
        has changed — avoids the visible layout shift caused by widget replacement.
        """
        logged_in = self._is_logged_in()
        if hasattr(self, '_idle_license_banner_row') and self._idle_license_banner_row is not None:
            self._idle_license_banner_row.setVisible(not logged_in)
            print(f"[IDLE] Licentiebanner {'verborgen' if logged_in else 'zichtbaar'}")
        else:
            # Banner reference not available — fall back to full rebuild
            self._rebuild_idle_page()
            print(f"[IDLE] Pagina herbouwd als fallback (licentie: {'ja' if logged_in else 'nee'})")

    def _kill_dnp_dialog(self):
        """Termineer het off-screen rundll32-dialoogproces van de poller.
        Lock-vrij en non-blocking — veilig op het exit-pad."""
        try:
            poller = getattr(self, '_dnp_poller', None)
            if poller is not None:
                poller.kill_dialog()
        except Exception:
            pass

    def _on_quit(self):
        """Handle quit button click — force quit immediately."""
        print("[UI] Afsluiten knop geklikt", flush=True)
        self._kill_dnp_dialog()
        # Schedule a hard kill after 1 second as absolute safety net
        threading.Timer(1.0, lambda: os._exit(0)).start()
        # Signal worker thread to stop (non-blocking flag set)
        try:
            if hasattr(self, 'camera') and hasattr(self.camera, '_worker'):
                self.camera._worker._running = False
                self.camera._worker._live_view_active = False
        except Exception:
            pass
        # Do NOT call stop_digicam() — it calls EDSDK.terminate() from the
        # main thread which deadlocks (EDSDK uses COM STA on the worker thread).
        # Do NOT call camera.disconnect() — it queues a command that may never
        # be processed if the worker is stuck in an EDSDK call.
        print("[UI] App wordt afgesloten", flush=True)
        os._exit(0)

    def closeEvent(self, event):
        print("[CLEANUP] closeEvent gestart", flush=True)
        # Accept immediately so Qt doesn't wait for us
        event.accept()
        self._kill_dnp_dialog()
        # Schedule a hard kill after 1 second as absolute safety net
        threading.Timer(1.0, lambda: os._exit(0)).start()
        # Signal worker thread to stop (non-blocking flag set)
        try:
            if hasattr(self, 'camera') and hasattr(self.camera, '_worker'):
                self.camera._worker._running = False
                self.camera._worker._live_view_active = False
        except Exception:
            pass
        # Do NOT call stop_digicam() or camera.disconnect() — they call
        # EDSDK from the main thread which deadlocks (COM STA mismatch).
        print("[CLEANUP] closeEvent klaar, app sluit", flush=True)
        os._exit(0)
