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
    QCheckBox, QSpinBox, QTextEdit, QDialog, QProgressBar, QRadioButton
)
from PyQt5.QtCore import Qt, QTimer, QSize, QEventLoop, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QPixmapCache, QImage, QFont, QPainter, QColor, QCursor, QBitmap

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
        dialog.setFixedSize(440, 660)
        # Center on screen
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            dialog.move(
                sg.x() + (sg.width() - 440) // 2,
                sg.y() + (sg.height() - 660) // 2,
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

    def _calc_transform(self):
        """Calculate scale/offset to fit 1200x1800 canvas in widget.

        Voor display-rotatie 90/270 worden de effectieve widget-dimensies
        gewisseld (W↔H) bij het bepalen van scale, zodat het portrait canvas
        na rotatie netjes in de gedraaide weergave past. De _offset_x/_offset_y
        blijven gebaseerd op de oorspronkelijke widget (we roteren via de
        painter rond het centrum, dus het canvas-centrum valt samen met het
        widget-centrum — daarvoor moeten de offsets in original coords blijven).
        """
        rot = self._display_rotation()
        if rot in (90, 270):
            # Effectief gewisselde widget-afmetingen voor scale-berekening
            w, h = self.height(), self.width()
        else:
            w, h = self.width(), self.height()
        canvas_w, canvas_h = 1200, 1800
        sx = w / canvas_w
        sy = h / canvas_h
        self._scale = min(sx, sy) * 0.94
        # Offsets in originele widget-coords zodat canvas-centrum samenvalt
        # met widget-centrum (waarrond de painter geroteerd wordt).
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
        page_rect_x, page_rect_y = int(ox), int(oy)
        page_rect_w, page_rect_h = int(1200 * s), int(1800 * s)
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

        # Cut line for double strip (frames on left half, mirrored to right)
        if not self.template.is_double_strip:
            cut_x = int(ox + 600 * s)
            painter.setPen(QPen(QColor("#cc8888"), 2, Qt.DashLine))
            painter.drawLine(cut_x, int(oy), cut_x, int(oy + 1800 * s))

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

            # Mirror for double strip
            if not self.template.is_double_strip:
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
            if not self.template.is_double_strip:
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

        if self._drag_mode == "move":
            frame.x = max(0, ox + dx)
            frame.y = max(0, oy + dy)
        elif self._drag_mode == "resize_br":
            frame.width = max(self.MIN_FRAME, ow + dx)
            frame.height = max(self.MIN_FRAME, oh + dy)
        elif self._drag_mode == "resize_tl":
            new_w = max(self.MIN_FRAME, ow - dx)
            new_h = max(self.MIN_FRAME, oh - dy)
            frame.x = ox + ow - new_w
            frame.y = oy + oh - new_h
            frame.width = new_w
            frame.height = new_h
        elif self._drag_mode == "resize_tr":
            frame.width = max(self.MIN_FRAME, ow + dx)
            new_h = max(self.MIN_FRAME, oh - dy)
            frame.y = oy + oh - new_h
            frame.height = new_h
        elif self._drag_mode == "resize_bl":
            new_w = max(self.MIN_FRAME, ow - dx)
            frame.x = ox + ow - new_w
            frame.width = new_w
            frame.height = max(self.MIN_FRAME, oh + dy)

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

    def __init__(self, image_path, printer_name, copies=1):
        super().__init__()
        self.image_path = image_path
        self.printer_name = printer_name
        self.copies = copies

    def run(self):
        import subprocess

        self.print_status.emit("Bezig met printen...")

        # Determine Python executable and worker script path
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--print-worker",
                   self.image_path, self.printer_name,
                   str(self.copies), config.DATA_DIR]
        else:
            worker_script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "print_worker.py"
            )
            cmd = [sys.executable, worker_script,
                   self.image_path, self.printer_name,
                   str(self.copies), config.DATA_DIR]

        try:
            print(f"[PRINTER] Subprocess: {' '.join(str(c) for c in cmd[:4])}...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.stdout.strip():
                print(f"[PRINTER] {result.stdout.strip()}")
            if result.stderr.strip():
                print(f"[PRINTER] STDERR: {result.stderr.strip()}")
            if result.returncode != 0:
                print(f"[PRINTER] Subprocess exitcode {result.returncode} — print mogelijk mislukt")
            else:
                print("[PRINTER] Subprocess OK (exitcode 0)")

        except subprocess.TimeoutExpired:
            print("[PRINTER] Subprocess timeout (120s)")
        except Exception as e:
            print(f"[PRINTER] Subprocess fout: {e}")

        # Always emit complete — show "Geprint!" regardless of outcome
        self.print_complete.emit()


class PhotoboothWindow(QMainWindow):
    # Cross-thread signals for SumUp payment loop (daemon thread → main thread)
    _sumup_payment_signal = pyqtSignal()
    _sumup_status_signal = pyqtSignal(str)

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

        # Connect camera on main thread (EDSDK requires single-thread access)
        # Determine camera mode from saved active event JSON
        _cam_mode = "dslr"
        _wc_idx = 0
        _wc_res = ""
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

        self.state = State.IDLE
        # Connect cross-thread SumUp signals (daemon thread → main thread)
        self._sumup_payment_signal.connect(self._sumup_auto_start_session)
        self._sumup_status_signal.connect(self._sumup_update_idle)
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
        self.countdown_value = 0
        self.session_id = None     # Timestamp ID for this session
        self._settings_template_widgets = {}
        self.active_event = None  # Currently active Event
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
        self._idle_lock_btn.clicked.connect(self._go_settings)
        self._idle_lock_btn.raise_()

        return page

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

        # Print remaining indicator
        self._sharing_prints_remaining = QLabel("")
        self._sharing_prints_remaining.setAlignment(Qt.AlignCenter)
        self._sharing_prints_remaining.setFont(QFont("DM Sans", 11))
        self._sharing_prints_remaining.setStyleSheet("color: #888888; background: transparent;")
        self._sharing_prints_remaining.setFixedHeight(24)
        right_lay.addWidget(self._sharing_prints_remaining)

        right_lay.addSpacing(8)

        # --- QR-CODE button ---
        self._sharing_qr_btn = QPushButton("📱  " + t("btn_qr"))
        self._sharing_qr_btn.setCursor(Qt.PointingHandCursor)
        self._sharing_qr_btn.setFont(QFont("DM Sans", 18, QFont.Bold))
        self._sharing_qr_btn.setMinimumHeight(72)
        self._sharing_qr_btn.setStyleSheet(
            f"QPushButton {{ background: {config.COLOR_PRIMARY}; color: white; "
            f"border: none; border-radius: 16px; padding: 16px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {config.COLOR_PRIMARY_HOVER}; }}"
            f"QPushButton:pressed {{ background: {config.COLOR_PRIMARY_PRESSED}; }}"
        )
        self._sharing_qr_btn.clicked.connect(self._sharing_show_qr)
        right_lay.addWidget(self._sharing_qr_btn)

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

        # Build the layout directly — no dynamic wrapper reparenting
        # to avoid Windows fullscreen geometry corruption
        self._review_wrapper = QWidget()
        self._review_wrapper.setStyleSheet("background: transparent;")
        wrap_lay = QVBoxLayout(self._review_wrapper)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.setSpacing(0)
        wrap_lay.addWidget(self._review_photo_container, stretch=3)
        wrap_lay.addWidget(self._review_action_panel, stretch=0)
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
        self.stack.setCurrentIndex(self.pages["idle"])
        self._update_status()
        # Position lock button in bottom-right corner
        if hasattr(self, '_idle_lock_btn'):
            self._idle_lock_btn.show()
            self._idle_lock_btn.raise_()
            # Delay positioning until page is laid out (state is already IDLE
            # so resizeEvent will also call _position_idle_lock)
            QTimer.singleShot(150, self._position_idle_lock)
        # Show event info + disk warning on idle screen
        status_text = f"Event: {self.active_event.name}" if self.active_event else ""
        try:
            import shutil
            disk = shutil.disk_usage(config.PHOTO_DIR)
            free_gb = disk.free / (1024 ** 3)
            if free_gb < 10.0:
                status_text += f"  |  \u26a0 Schijfruimte: {free_gb:.1f} GB vrij"
        except Exception:
            pass
        self.status_label.setText(status_text)
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

        # Check for pre-selected template from active event
        saved_name = self.active_event.template_name if self.active_event else ""
        match = self._find_template_by_name(saved_name) if saved_name else None

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
        # Check internet connectivity in background
        self._has_internet = True  # Assume yes until check completes
        self._check_internet_bg()
        self.current_photo_num = 0
        self.photos = []
        self._processed_photos = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
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
            self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
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

        # Restart live view only if more photos needed (not after last photo)
        if self.current_photo_num < self.num_photos - 1:
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
            self._timestamp_filename(ext=ext, photo_num=self.current_photo_num + 1),
        )
        if file_path != dest:
            try:
                shutil.copy2(file_path, dest)
            except Exception:
                # Bij kopieerfout: val terug op originele location zodat de
                # strip-build nog kan lezen van het bronbestand.
                dest = file_path

        self.photos.append(dest)
        self._update_thumbnail(self.current_photo_num, dest)

        # Pre-process photo for strip building
        self._process_photo_for_strip(dest, self.current_photo_num)

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
                # Crop-to-fit: vult het frame exact, snijdt overtollige randen af
                img = ImageOps.fit(img, (frame.width, frame.height), Image.LANCZOS)
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
            self._go_review()
        else:
            self._show_error(t("error_cannot_make_strip"))

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

            PRINT_W = 1200
            PRINT_H = 1800
            STRIP_W = PRINT_W // 2

            template = self.selected_template
            if not template:
                print("[STRIP] FOUT: Geen template geselecteerd")
                return None

            # DNP verhuur-flow: triple strip → portrait 5x10cm, 3x gestapeld op vel.
            if getattr(template, 'is_triple_strip', False):
                return self._build_triple_strip_image(template)

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
            print(f"[STRIP] Template: '{template.name}', bg_path='{bg_path}', _strip_bg={'JA' if self._strip_bg else 'NEE'}")
            if self._strip_bg:
                strip = self._strip_bg.copy()
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
                    img = ImageOps.fit(img, (frame.width, frame.height), Image.LANCZOS)
                else:
                    continue

                if template.is_double_strip:
                    # 1200px canvas — frames are positioned on the full print,
                    # background already contains both strips. Just paste directly.
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

            # Strip-composiet naar photos/<event>/strips/
            strip_dir = self._get_strips_dir()
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

            # Generate single strip (left half) for sharing if enabled
            self._single_strip_path = None
            ev = self.active_event
            if ev and ev.share_single_strip and PRINT_W >= 1200:
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

        # Adapt layout for current orientation
        self._review_is_portrait = None  # Force re-evaluation
        self._adapt_review_layout()

        self.stack.setCurrentIndex(self.pages["review"])
        QTimer.singleShot(200, self._display_review_strip)
        # NO second re-render: printer GDI can break fullscreen between 200-500ms

        # Auto-print AFTER strip is displayed (800ms delay prevents GDI
        # from corrupting the geometry before the strip is rendered)
        if print_on and auto_print:
            self._sharing_print_status.setText(t("printing"))
            self._sharing_print_status.show()
            QTimer.singleShot(800, self._sharing_do_auto_print)

        # Prepare QR code in background (so it's ready when user taps QR button)
        if qr_on:
            QTimer.singleShot(200, self._prepare_qr_code)

        # Start the visual countdown bar (30 seconds)
        self._start_sharing_countdown()

        # Start Google Drive upload in background (non-blocking)
        self._start_gdrive_upload()

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

    def _do_print_job(self, copies=1):
        """Execute a print job (shared between auto-print and manual print).

        Event-quotum handhaving: als event.event_print_quota > 0 wordt
        gecontroleerd of er nog ruimte is. Bij overschrijding: toon
        "Maximum prints bereikt" en print niet.
        """
        if not self.strip_path:
            self._sharing_print_status.setText(t("no_strip"))
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

        self.print_thread = SubprocessPrintThread(self.strip_path, config.PRINTER_NAME, copies)
        self.print_thread.print_complete.connect(
            lambda c=copies: self._on_print_complete_with_quota(c)
        )
        self.print_thread.print_failed.connect(self._on_print_failed)
        self.print_thread.print_status.connect(self._on_print_status)
        self.print_thread.start()
        self._sharing_print_status.setText(t("checking_printer"))
        self._sharing_print_status.show()
        print(f"[PRINTER] Printen: {copies} kopie(ën)")

    def _on_print_complete_with_quota(self, copies):
        """Wrapper rond _on_print_complete die ook het event-quotum bijwerkt."""
        ev = self.active_event
        if ev:
            quota = int(getattr(ev, 'event_print_quota', 0) or 0)
            if quota > 0:
                ev.event_prints_used = int(getattr(ev, 'event_prints_used', 0) or 0) + int(copies)
                try:
                    ev.save(config.EVENTS_DIR)
                    print(f"[PRINT-QUOTA] Used: {ev.event_prints_used}/{quota}")
                except Exception as ex:
                    print(f"[PRINT-QUOTA] Save fout: {ex}")
                # UI bijwerken als de Print-tab open staat
                if hasattr(self, '_evlimit_status_label'):
                    try:
                        self._refresh_event_limit_ui()
                    except Exception:
                        pass
        # Roep originele handler aan
        self._on_print_complete()

    def _start_printing(self):
        """Legacy method — redirects to sharing screen print."""
        self._sharing_do_print()

    def _go_after_review(self):
        """Legacy method — go to done."""
        self.review_timer.stop()
        self._go_done()

    def _on_print_complete(self):
        """Print finished — update sharing screen status."""
        self._session_prints_used += 1
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

            session_id = self.session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
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
                print(f"[QR] Voorbereid met cloud URL: {url}")
            else:
                # Cloud upload still in progress — show animated spinner
                self.qr_label.hide()
                self.qr_url_label.hide()
                self._start_qr_spinner()
                self._qr_ready = False
                print("[QR] Cloud upload nog bezig, spinner getoond")
                # Register local session anyway (for fallback)
                generate_session_url(session_id, config.WEB_SERVER_PORT)
        except Exception as e:
            print(f"[QR] Fout bij voorbereiden: {e}")
            self._qr_ready = False

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

        if portrait:
            # Clamp all widgets to screen width — window geometry can be
            # corrupted by Windows DPI scaling (912→1237px).
            screen = self.screen()
            max_w = screen.geometry().width() if screen else 912
            self._review_action_panel.setMinimumSize(0, 0)
            self._review_action_panel.setMaximumWidth(max_w)
            self._review_photo_container.setMinimumSize(0, 0)
            self._review_photo_container.setMaximumWidth(max_w)
            self._review_wrapper.setMaximumWidth(max_w)
            self.review_strip_label.setMaximumWidth(max_w)
            # Margins to keep buttons narrower and centered
            side_margin = max(20, (max_w - 480) // 2) if max_w > 520 else 20
            self._review_photo_container.layout().setContentsMargins(0, 0, 0, 0)
            self._review_action_panel.layout().setContentsMargins(side_margin, 8, side_margin, 12)
            self._review_action_panel.layout().setSpacing(8)
            # Ensure vertical layout for portrait
            wrap_lay = self._review_wrapper.layout()
            if wrap_lay:
                wrap_lay.setDirection(wrap_lay.TopToBottom)
                wrap_lay.setStretch(0, 3)  # photo container
                wrap_lay.setStretch(1, 0)  # action panel
            self._review_action_panel.setMaximumWidth(16777215)  # reset landscape constraint
            for btn in [self._sharing_print_btn, self._sharing_qr_btn,
                        self._sharing_email_btn, self._sharing_done_btn]:
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
            self._review_photo_container.layout().setContentsMargins(10, 10, 0, 10)
            self._review_action_panel.layout().setContentsMargins(16, 12, 16, 12)
            self._review_action_panel.layout().setSpacing(8)
            # Change wrapper to horizontal layout
            wrap_lay = self._review_wrapper.layout()
            if wrap_lay:
                wrap_lay.setDirection(wrap_lay.LeftToRight)
                wrap_lay.setStretch(0, 3)  # photo container (left)
                wrap_lay.setStretch(1, 1)  # action panel (right)
            for btn in [self._sharing_print_btn, self._sharing_qr_btn,
                        self._sharing_email_btn, self._sharing_done_btn]:
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
        """Handle print failure — always show success to user (too many false positives).

        The pre-print check (check_printer_status) catches real issues.
        If that passed, the print almost certainly succeeded even if the
        subprocess reports an error (e.g. HiTi queue check race condition).
        """
        print(f"[PRINTER] Fout genegeerd (false positive): {error_msg}")
        # Always show success — real failures are caught by pre-print check
        if hasattr(self, '_sharing_print_status'):
            self._sharing_print_status.setText(t("printed"))
            self._sharing_print_status.setStyleSheet(f"color: {config.COLOR_SUCCESS}; font-size: 14px;")
            self._sharing_print_status.show()
            QTimer.singleShot(4000, lambda: self._sharing_print_status.hide()
                              if self.state == State.REVIEW else None)

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

    def _switch_settings_tab(self, index):
        """Switch the active settings tab."""
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

        row_lay = QHBoxLayout()
        row_lay.setSpacing(4)
        for i, name in enumerate(tab_names):
            btn = QPushButton(name)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(QFont("DM Sans", tab_font_size, QFont.Bold))
            btn.clicked.connect(lambda _, idx=i: self._switch_settings_tab(idx))
            self._settings_tab_buttons.append(btn)
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

        del_event_btn = QPushButton(t("delete").upper())
        del_event_btn.setCursor(Qt.PointingHandCursor)
        del_event_btn.setFont(QFont("DM Sans", 13, QFont.Bold))
        del_event_btn.setStyleSheet(btn_style_danger)
        del_event_btn.clicked.connect(self._on_event_delete)
        event_row.addWidget(del_event_btn)

        card_event_lay.addLayout(event_row)

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
        bg_row.addStretch()
        card_layout_lay.addLayout(bg_row)

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

        # Driver row
        driver_row = QHBoxLayout()
        driver_row.setSpacing(12)
        driver_label = QLabel(t("driver_settings"))
        driver_label.setFont(QFont("DM Sans", 13, QFont.Bold))
        driver_label.setStyleSheet(label_style)
        driver_row.addWidget(driver_label)
        self._devmode_status_label = QLabel(t("driver_not_configured"))
        self._devmode_status_label.setFont(QFont("DM Sans", 12))
        self._devmode_status_label.setStyleSheet(dim_label_style)
        driver_row.addWidget(self._devmode_status_label)
        configure_printer_btn = QPushButton(t("printer_setup"))
        configure_printer_btn.setCursor(Qt.PointingHandCursor)
        configure_printer_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        configure_printer_btn.setFixedHeight(36)
        configure_printer_btn.setStyleSheet(
            small_btn_style.replace("{bg}", config.COLOR_PRIMARY)
                           .replace("{hov}", config.COLOR_PRIMARY_HOVER)
        )
        configure_printer_btn.clicked.connect(self._on_configure_printer)
        driver_row.addWidget(configure_printer_btn)
        # Test print direct ernaast — zelfde rij, kleiner formaat
        test_print_btn = QPushButton(t("test_print"))
        test_print_btn.setCursor(Qt.PointingHandCursor)
        test_print_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        test_print_btn.setFixedHeight(36)
        test_print_btn.setStyleSheet(
            small_btn_style.replace("{bg}", config.COLOR_SECONDARY)
                           .replace("{hov}", config.COLOR_SECONDARY_HOVER)
        )
        test_print_btn.clicked.connect(self._on_test_print)
        driver_row.addWidget(test_print_btn)
        driver_row.addStretch()
        connect_lay.addLayout(driver_row)
        self._update_devmode_status()

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

        # Card: Printer-modus (verhuur)
        card_pmode, card_pmode_lay = self._settings_card("Printer-modus")
        pmode_row = QHBoxLayout()
        pmode_row.setSpacing(20)
        from PyQt5.QtWidgets import QButtonGroup as _BG
        self._printer_mode_group = _BG(self)
        self._printer_mode_canon_radio = QRadioButton("Canon (huidige flow)")
        self._printer_mode_canon_radio.setFont(QFont("DM Sans", 13))
        self._printer_mode_dnp_radio = QRadioButton("DNP (3-strip 5x10cm)")
        self._printer_mode_dnp_radio.setFont(QFont("DM Sans", 13))
        self._printer_mode_group.addButton(self._printer_mode_canon_radio)
        self._printer_mode_group.addButton(self._printer_mode_dnp_radio)
        # Default: DNP (verhuur)
        self._printer_mode_dnp_radio.setChecked(True)
        self._printer_mode_canon_radio.toggled.connect(self._on_printer_mode_changed)
        self._printer_mode_dnp_radio.toggled.connect(self._on_printer_mode_changed)
        pmode_row.addWidget(self._printer_mode_canon_radio)
        pmode_row.addWidget(self._printer_mode_dnp_radio)
        pmode_row.addStretch()
        card_pmode_lay.addLayout(pmode_row)
        tab5_lay.addWidget(card_pmode)

        # ── Card: Booth-modus (Standalone vs Gekoppeld) ──
        card_bmode, card_bmode_lay = self._settings_card("Modus")
        bmode_row = QHBoxLayout()
        bmode_row.setSpacing(20)
        from PyQt5.QtWidgets import QButtonGroup as _BG2
        self._booth_mode_group = _BG2(self)
        self._booth_mode_standalone_radio = QRadioButton("Standalone (huidige flow)")
        self._booth_mode_standalone_radio.setFont(QFont("DM Sans", 13))
        self._booth_mode_linked_radio = QRadioButton("Gekoppeld (event uit Clixibo)")
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

        # ── Card: Gekoppeld event (alleen zichtbaar in Linked-modus) ──
        self._card_linked, card_linked_lay = self._settings_card("Gekoppeld event")
        self._linked_status_label = QLabel("Geen event gekoppeld")
        self._linked_status_label.setFont(QFont("DM Sans", 13))
        self._linked_status_label.setStyleSheet(label_style)
        self._linked_status_label.setWordWrap(True)
        card_linked_lay.addWidget(self._linked_status_label)

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
        card_linked_lay.addLayout(linked_btn_row)

        # Foto-aantal selectie (alleen relevant als gekoppeld)
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
        card_linked_lay.addLayout(self._linked_count_row)

        # Upload-voortgang
        self._linked_progress_label = QLabel("")
        self._linked_progress_label.setFont(QFont("DM Sans", 11))
        self._linked_progress_label.setStyleSheet(dim_label_style)
        self._linked_progress_label.setWordWrap(True)
        card_linked_lay.addWidget(self._linked_progress_label)

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

        # App version at bottom of Advanced tab — dynamisch vanuit config.VERSION
        version_label = QLabel(t("version", version=config.VERSION))
        version_label.setFont(QFont("DM Sans", 9))
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; padding: 10px;")
        tab5_lay.addStretch()
        tab5_lay.addWidget(version_label)
        self._settings_tab_stack.addWidget(tab5_scroll)

        # Add stacked widget to main layout
        lay.addWidget(self._settings_tab_stack, stretch=1)

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

        if ev:
            _set(self._cut_checkbox, ev.cut_enabled)
            _set(self._print_enabled_toggle, ev.print_enabled)
            _set(self._auto_print_toggle, ev.auto_print)
            # Use the higher of auto_print_copies and legacy print_copies (backward compat)
            auto_copies = max(ev.auto_print_copies, ev.print_copies)
            _set(self._auto_copies_spin, auto_copies)
            _set(self._max_prints_spin, max(ev.max_prints, auto_copies if ev.auto_print else 1))
            _set(self._extra_prints_spin, ev.extra_prints_allowed)
            _set(self._qr_toggle, ev.gallery_enabled)
            # QR-branding state sync
            if hasattr(self, '_qr_branding_toggle'):
                _set(self._qr_branding_toggle, getattr(ev, 'qr_branding_enabled', False))
            if hasattr(self, '_qr_branding_text'):
                self._qr_branding_text.blockSignals(True)
                self._qr_branding_text.setPlainText(getattr(ev, 'qr_branding_text', '') or '')
                self._qr_branding_text.blockSignals(False)
                self._qr_branding_text.setVisible(getattr(ev, 'qr_branding_enabled', False))
            if hasattr(self, '_qr_branding_container'):
                self._qr_branding_container.setVisible(bool(ev.gallery_enabled))
            _set(self._email_toggle, ev.email_enabled)
            _set(self._email_collect_toggle, getattr(ev, 'email_collect', False))
            _set(self._email_subject_input, ev.email_subject)
            self._email_body_input.blockSignals(True)
            self._email_body_input.setPlainText(ev.email_body)
            self._email_body_input.blockSignals(False)
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
            if hasattr(self, '_cam_dslr_radio'):
                self._cam_dslr_radio.blockSignals(True)
                self._cam_webcam_radio.blockSignals(True)
                if ev.camera_mode == "webcam":
                    self._cam_webcam_radio.setChecked(True)
                else:
                    self._cam_dslr_radio.setChecked(True)
                self._webcam_select_row.setVisible(ev.camera_mode == "webcam")
                self._cam_dslr_radio.blockSignals(False)
                self._cam_webcam_radio.blockSignals(False)
                self._update_webcam_status()
                _set(self._cam_mirror_cb, ev.camera_mirror)
                rot_map = {0: 0, 90: 1, 180: 2, 270: 3}
                self._cam_rotation_combo.blockSignals(True)
                self._cam_rotation_combo.setCurrentIndex(rot_map.get(ev.camera_rotation, 0))
                self._cam_rotation_combo.blockSignals(False)
            # Live view positie radio + alignment toepassen
            if hasattr(self, '_live_view_pos_radios'):
                pos = getattr(ev, 'live_view_position', 'center') or 'center'
                if pos not in self._live_view_pos_radios:
                    pos = 'center'
                for v, rb in self._live_view_pos_radios.items():
                    rb.blockSignals(True)
                    rb.setChecked(v == pos)
                    rb.blockSignals(False)
                self._apply_live_view_alignment()
            self._update_pin_button_text()
        else:
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
            self._email_body_input.setPlainText("")
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

        # Update printer name label
        self._printer_name_label.setText(config.PRINTER_NAME or t("printer_not_selected"))

        # Update printer settings visibility
        self._update_printer_visibility()

        # Update Gmail status and email section visibility
        self._update_gmail_status()
        self._update_email_visibility()

        # Update idle background preview
        self._update_bg_preview()

        # Update layout background preview
        self._update_layout_bg_preview()

        # Update photo storage toggle
        if hasattr(self, '_save_photos_toggle') and ev:
            self._save_photos_toggle.blockSignals(True)
            self._save_photos_toggle.setChecked(getattr(ev, 'save_photos_locally', True))
            self._save_photos_toggle.blockSignals(False)

        # Update printer-modus radio (verhuur)
        if hasattr(self, '_printer_mode_dnp_radio') and ev:
            mode = getattr(ev, 'printer_mode', 'dnp')
            self._printer_mode_canon_radio.blockSignals(True)
            self._printer_mode_dnp_radio.blockSignals(True)
            if mode == 'canon':
                self._printer_mode_canon_radio.setChecked(True)
            else:
                self._printer_mode_dnp_radio.setChecked(True)
            self._printer_mode_canon_radio.blockSignals(False)
            self._printer_mode_dnp_radio.blockSignals(False)

        # Update booth-modus radio (Standalone/Linked) + linked event card
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

        # Update payment settings
        if hasattr(self, '_payment_toggle') and ev:
            self._payment_toggle.blockSignals(True)
            self._payment_toggle.setChecked(ev.payment_enabled)
            self._payment_toggle.blockSignals(False)
            self._update_payment_info()

        # Update SumUp/Clixibo terminal toggle
        if hasattr(self, '_sumup_toggle') and ev:
            self._sumup_toggle.blockSignals(True)
            self._sumup_toggle.setChecked(getattr(ev, 'sumup_enabled', False))
            self._sumup_toggle.blockSignals(False)
            self._update_sumup_status()

        # Update payment method radio + zichtbaarheid van payment-cards
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

        # Update event-limiet UI op basis van het actieve event
        if hasattr(self, '_evlimit_status_label'):
            try:
                self._refresh_event_limit_ui()
            except Exception as ex:
                print(f"[PRINT-QUOTA] UI refresh fout: {ex}")

        # Capture screen settings removed — freeze frame is used instead

        # Update intro screen preview + text fields
        if hasattr(self, '_intro_preview_label'):
            self._update_intro_preview()
            if self.active_event:
                _set(self._intro_duration_spin, self.active_event.intro_duration)
                if hasattr(self, '_intro_text_toggle'):
                    self._intro_text_toggle.setChecked(self.active_event.intro_text_enabled)
                    self._intro_text_input.setText(self.active_event.intro_text)
                    self._intro_text_input.setEnabled(self.active_event.intro_text_enabled)

        # Capture text settings removed — freeze frame used instead

        # Update account info
        self._update_account_info()

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

        # Filter op printer-modus: canon → geen triple strips, dnp → alleen triple
        pm = getattr(self.active_event, 'printer_mode', 'dnp') if self.active_event else 'dnp'
        for layout in self._preset_layouts:
            if pm == 'dnp':
                if not layout.is_triple_strip:
                    continue
            else:  # canon
                if layout.is_triple_strip:
                    continue
            # If user edited this preset, use the custom version for preview
            display_layout = custom_by_name.get(layout.name, layout)
            if layout.cut_default:
                cat_cut.append(display_layout)
            else:
                cat_nocut.append(display_layout)

        # Track custom template names for delete buttons
        for tmpl in custom:
            self._custom_template_names.add(tmpl.name)

        categories = [
            (t("double_strips"), cat_cut),
            (t("single_strips"), cat_nocut),
        ]

        # Custom templates are loaded internally but hidden from the UI
        # if custom:
        #     categories.append(("Eigen templates", custom))

        self._cat_grids = {}
        for cat_name, layouts in categories:
            if not layouts:
                continue
            # Check if selected layout is in this category
            cat_has_selected = any(l.name == selected for l in layouts)
            self._add_layout_category(cat_name, layouts, selected, start_open=cat_has_selected)

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

        # Draw layout preview
        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setFixedHeight(155)
        preview = self._render_layout_preview(layout, 148, 155)
        if preview:
            thumb_label.setPixmap(preview)
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

    def _render_layout_preview(self, layout, w, h):
        """Render a QPixmap showing the frame layout as colored rectangles."""
        from PyQt5.QtGui import QPainter, QPen, QBrush
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(config.COLOR_CARD_BG))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Canvas size hangt af van strip-type:
        #  triple_strip → 600x1200 portrait (5x10 cm DNP strip)
        #  anders       → 1200x1800 (4x6 vel)
        if getattr(layout, 'is_triple_strip', False):
            canvas_w = 600
            canvas_h = 1200
        else:
            canvas_w = 1200
            canvas_h = 1800
        scale_x = w / canvas_w
        scale_y = h / canvas_h
        scale = min(scale_x, scale_y) * 0.92
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

        self._test_print_thread = SubprocessPrintThread(test_path, config.PRINTER_NAME, 1)
        self._test_print_thread.start()
        print(f"[SETTINGS] Test print naar: {config.PRINTER_NAME}")

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

    def _on_print_enabled_toggled(self, checked):
        """Toggle printing on/off and show/hide printer settings."""
        if self.active_event:
            self.active_event.print_enabled = checked
            self.active_event.save(config.EVENTS_DIR)
            print(f"[SETTINGS] Printen: {'aan' if checked else 'uit'}")
        self._update_printer_visibility()

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
        # Tweede card (Printerinstellingen) ook verbergen als printen uit staat
        if hasattr(self, '_print_settings_card'):
            self._print_settings_card.setVisible(print_on)
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
        self._webcam_select_row.setVisible(is_webcam)
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

        def _populate(cameras, resolutions):
            if not cameras:
                status.setText(t("no_webcam_found"))
                return
            status.setText(t("select_webcam_prompt"))
            cam_combo.setVisible(True)
            for idx, name in cameras:
                cam_combo.addItem(name, idx)
            # Select saved
            if self.active_event:
                for i in range(cam_combo.count()):
                    if cam_combo.itemData(i) == self.active_event.webcam_index:
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
            if self.active_event:
                ci = cam_combo.currentIndex()
                cam_idx = cam_combo.itemData(ci)
                if cam_idx is None or cam_idx < 0:
                    cam_idx = 0
                res = res_combo.currentText()
                cam_name = cam_combo.currentText()
                self.active_event.webcam_index = int(cam_idx)
                self.active_event.webcam_name = cam_name
                self.active_event.webcam_resolution = res if res != "Standaard" else ""
                self.active_event.save(config.EVENTS_DIR)
                self._webcam_status_label.setText(f"{cam_name} ({res})")
                self._webcam_status_label.setStyleSheet(f"color: {config.COLOR_SUCCESS};")
                print(f"[SETTINGS] Webcam opgeslagen: index={cam_idx}, naam={cam_name}, resolutie={res}")
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
        ev = self.active_event
        if ev and ev.camera_mode == "webcam":
            name = ev.webcam_name or f"Camera {ev.webcam_index}"
            res = ev.webcam_resolution or "Standaard"
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
        """Wissel tussen Standalone en Linked-modus."""
        if not checked:
            return
        mode = "linked" if self._booth_mode_linked_radio.isChecked() else "standalone"
        if self.active_event:
            self.active_event.booth_mode = mode
            self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Booth-modus: {mode}")
        self._update_linked_card_visibility()

    def _update_linked_card_visibility(self):
        """Toon/verberg de Gekoppeld-event-kaart en knoppen-state."""
        if not hasattr(self, '_card_linked'):
            return
        ev = self.active_event
        mode = getattr(ev, 'booth_mode', 'standalone') if ev else 'standalone'
        self._card_linked.setVisible(mode == 'linked')

        if mode != 'linked':
            return

        booking_id = getattr(ev, 'linked_booking_id', '') if ev else ''
        label = getattr(ev, 'linked_booking_label', '') if ev else ''

        if booking_id:
            self._linked_status_label.setText(f"🟢 {label or booking_id}\nID: {booking_id}")
            self._btn_couple_event.setVisible(False)
            self._btn_refresh_event.setVisible(True)
            self._btn_unlink_event.setVisible(True)
            self._linked_count_label.setVisible(True)
            self._linked_count_spin.setVisible(True)
            self._touch_spin_set(self._linked_count_spin, getattr(ev, 'linked_photo_count', 2))
            self._update_linked_progress()
        else:
            self._linked_status_label.setText("Geen event gekoppeld")
            self._btn_couple_event.setVisible(True)
            self._btn_refresh_event.setVisible(False)
            self._btn_unlink_event.setVisible(False)
            self._linked_count_label.setVisible(False)
            self._linked_count_spin.setVisible(False)
            self._linked_progress_label.setText("")

    def _update_linked_progress(self):
        """Werk de upload-voortgang regel bij."""
        ev = self.active_event
        if not ev or not getattr(ev, 'linked_booking_id', ''):
            self._linked_progress_label.setText("")
            return
        try:
            from cloud_uploader import get_status
            s = get_status(ev.linked_booking_id)
        except Exception:
            return
        if s["total"] == 0:
            self._linked_progress_label.setText("Nog geen foto's geüpload.")
            return
        pct = int(100 * s["uploaded"] / max(1, s["total"]))
        msg = f"Upload: {s['uploaded']}/{s['total']} foto's ({pct}%)"
        if s["pending"] > 0:
            msg += f" — {s['pending']} wacht op upload"
        if s["failed"] > 0:
            msg += f" — {s['failed']} mislukt"
        self._linked_progress_label.setText(msg)

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
        """Re-fetch booking metadata + design uit cloud."""
        ev = self.active_event
        token = getattr(ev, 'linked_token', '') if ev else ''
        if not token:
            return
        from cloud_booking import fetch_booking
        b, err = fetch_booking(token, use_cache_on_offline=True)
        if err and not b:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Ververs mislukt", err)
            return
        # Update event metadata
        if b and self.active_event:
            self._apply_linked_booking(b)
            self._update_linked_card_visibility()
        if err:
            print(f"[LINKED] Ververs waarschuwing: {err}")

    def _on_unlink_event_clicked(self):
        """Loskoppelen — clear linked_* velden."""
        from PyQt5.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Loskoppelen",
            "Loskoppelen van het event? Foto's blijven in de queue tot ze geüpload zijn."
            ) != QMessageBox.Yes:
            return
        ev = self.active_event
        if ev:
            ev.linked_booking_id = ""
            ev.linked_token = ""
            ev.linked_booking_label = ""
            ev.linked_design_path = ""
            ev.save(config.EVENTS_DIR)
        self._update_linked_card_visibility()
        print("[LINKED] Event losgekoppeld")

    def _on_linked_count_changed(self, value):
        """Aantal foto's per strip aangepast (operator-keuze)."""
        ev = self.active_event
        if not ev:
            return
        ev.linked_photo_count = int(value)
        ev.save(config.EVENTS_DIR)
        print(f"[LINKED] Foto-aantal: {value}")
        # Fase 4 zal hier template-frames opnieuw genereren

    def _apply_linked_booking(self, booking_data: dict):
        """Schrijf booking-metadata naar active_event (na coupling of refresh)."""
        if not self.active_event:
            return
        b = booking_data.get("booking", {})
        q = booking_data.get("quote", {})
        bid = b.get("id", "")
        # Display label: customer + event_date als beschikbaar
        name = (b.get("customer_name") or q.get("customer_name")
                or b.get("event_name") or q.get("event_name") or "Gekoppeld event")
        date = (b.get("event_date") or b.get("event_start_date")
                or q.get("event_date") or q.get("event_start_date") or "")
        label = f"{name}" + (f" · {date}" if date else "")
        self.active_event.linked_booking_id = str(bid)
        self.active_event.linked_token = q.get("token", self.active_event.linked_token)
        self.active_event.linked_booking_label = label
        self.active_event.linked_design_path = b.get("photostrip_design_url", "")
        self.active_event.linked_photo_count = int(booking_data.get("photo_count_preset", 2))
        # Printer-mode override vanuit cloud
        cloud_pm = booking_data.get("printer_mode", "")
        if cloud_pm in ("standard", "premium"):
            # Map naar bestaande "canon"/"dnp" waarden
            self.active_event.printer_mode = "canon" if cloud_pm == "standard" else "dnp"
        self.active_event.save(config.EVENTS_DIR)

    def _show_couple_event_dialog(self):
        """Placeholder — wordt in Fase 3 ingevuld met QR-scan + handmatig."""
        from PyQt5.QtWidgets import QInputDialog
        token, ok = QInputDialog.getText(
            self, "Event koppelen",
            "QR-scan komt in volgende update.\nVoor nu: plak de event-token (40 chars uit /offerte/<id>):",
        )
        if not ok or not token.strip():
            return
        token = token.strip()
        from cloud_booking import fetch_booking
        b, err = fetch_booking(token, use_cache_on_offline=False)
        if not b:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Koppelen mislukt", err or "Onbekende fout")
            return
        self._apply_linked_booking(b)
        self._update_linked_card_visibility()
        # Fase 4: trigger design fetch + start uploader
        print(f"[LINKED] Event gekoppeld: {self.active_event.linked_booking_label}")

    def _on_printer_mode_changed(self, checked):
        """Persisteer printer-modus (canon|dnp) booth-wide via active_event.

        Wordt in event_model.Event.save() automatisch naar booth_settings.json
        gepropageerd zodat het over alle events geldt. Ververst de layout-lijst
        zodat de gebruiker meteen de juiste templates ziet.
        """
        if not checked:
            return  # ignore the unchecking signal of the other radio
        mode = "canon" if self._printer_mode_canon_radio.isChecked() else "dnp"
        if self.active_event:
            self.active_event.printer_mode = mode
            self.active_event.save(config.EVENTS_DIR)
        print(f"[SETTINGS] Printer-modus: {mode}")
        # Ververs layout-lijst zodat juiste presets zichtbaar worden
        if hasattr(self, '_layout_categories_container'):
            try:
                self._load_settings_templates()
            except Exception as ex:
                print(f"[SETTINGS] Layout-refresh overgeslagen: {ex}")

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

        # --- Background image button ---
        bg_btn = QPushButton(t("editor_change_bg"))
        bg_btn.setCursor(Qt.PointingHandCursor)
        bg_btn.setFont(QFont("DM Sans", 11, QFont.Bold))
        bg_btn.setStyleSheet(dark_btn_style)
        bg_btn.clicked.connect(self._editor_change_background)
        sidebar.addWidget(bg_btn)

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
        if not self.active_event or not self.active_event.template_name:
            return
        target = self.active_event.template_name
        # Search presets first, then custom templates
        all_layouts = list(get_preset_layouts())
        if os.path.isdir(config.TEMPLATES_DIR):
            from template_model import Template as TModel
            for fname in os.listdir(config.TEMPLATES_DIR):
                if fname.lower().endswith(".json"):
                    try:
                        all_layouts.append(TModel.load(os.path.join(config.TEMPLATES_DIR, fname)))
                    except Exception:
                        pass
        # Find last match (custom templates override presets with same name)
        match = None
        for layout in all_layouts:
            if layout.name == target:
                match = layout
        if match:
            self._editor_canvas.set_template(match)
            self._editor_canvas.set_event_background(self.active_event.background_path if self.active_event else "")
            self._editor_title.setText(t("editor_title"))
            self._editor_name_input.setText(match.name)
            self._editor_info.setText("")
            self._editor_update_count_label()
            # Update XY input fields if they exist
            if hasattr(self, '_editor_x_input'):
                self._update_editor_xy_fields()
            self.stack.setCurrentIndex(self.pages["layout_editor"])
            return

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
        # Default 3:2 landscape frame, positioned below existing frames
        strip_w = 600 if not t.is_double_strip else 1200
        margin = 30
        frame_w = strip_w - 2 * margin
        frame_h = int(frame_w / 1.5)  # 3:2 aspect ratio
        # Find lowest frame to place below it
        max_y = margin
        for f in t.frames:
            bottom = f.y + f.height
            if bottom > max_y:
                max_y = bottom
        y = max_y + 30  # spacing
        if y + frame_h > 1800:
            y = max(30, 1800 - frame_h - 30)
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
        """Save edited layout as custom template and go back to settings."""
        canvas = self._editor_canvas
        if not canvas.template or not self.active_event:
            self.stack.setCurrentIndex(self.pages["settings"])
            return

        import json as _json

        # Save custom layout to templates dir
        t = canvas.template
        custom_name = self._editor_name_input.text().strip()
        if not custom_name:
            custom_name = f"{t.name} (aangepast)"

        # Derive cut_default from is_double_strip (double strip = snijden)
        cut_default = not t.is_double_strip

        data = {
            "name": custom_name,
            "background_path": t.background_path or "",
            "is_double_strip": t.is_double_strip,
            "cut_default": cut_default,
            "frames": [{"x": f.x, "y": f.y, "width": f.width, "height": f.height,
                         "rotation": getattr(f, 'rotation', 0.0)}
                        for f in t.frames],
        }

        # Check if a template with this name already exists — overwrite it
        fname = None
        if os.path.isdir(config.TEMPLATES_DIR):
            for existing_fname in os.listdir(config.TEMPLATES_DIR):
                if existing_fname.lower().endswith(".json"):
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
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)

        # Update event to use custom layout
        self.active_event.template_name = custom_name
        self.active_event.cut_enabled = cut_default
        self.active_event.save(config.EVENTS_DIR)
        print(f"[EDITOR] Layout opgeslagen: {custom_name} -> {path}")

        # Go back to settings
        self._load_settings_for_event()
        self._load_settings_templates()
        self.stack.setCurrentIndex(self.pages["settings"])

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

    def _on_quit(self):
        """Handle quit button click — force quit immediately."""
        print("[UI] Afsluiten knop geklikt", flush=True)
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
