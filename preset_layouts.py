"""
Preset layout dialog for the photobooth template editor.

Provides predefined layouts (like DSLR Remote Pro) with configurable
margins, spacing, and aspect ratio. Shows a live preview.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton,
    QSpinBox, QLabel, QCheckBox, QPushButton, QButtonGroup, QWidget,
    QFormLayout, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QFont, QPen

from template_model import PhotoFrame

# Canvas dimensions
SINGLE_STRIP_W = 600
SINGLE_STRIP_H = 1800
DOUBLE_STRIP_W = 1200
DOUBLE_STRIP_H = 1800

DEFAULT_ASPECT = 3.0 / 2.0  # landscape camera output (w:h)

# Preset definitions
PRESETS = [
    # Dubbele strips (snijden)
    {"id": "double_strip_3",  "label": "Dubbele strip met 3 foto's", "count": 3, "double": True,  "layout": "vertical"},
    {"id": "double_strip_4",  "label": "Dubbele strip met 4 foto's", "count": 4, "double": True,  "layout": "vertical"},
    {"id": "double_2_land",   "label": "2 foto's liggend",           "count": 2, "double": True,  "layout": "vertical"},
    # Enkele strips (niet snijden)
    {"id": "1_large_photo",   "label": "1 grote foto",               "count": 1, "double": False, "layout": "single"},
    {"id": "single_2x2",      "label": "2x2 liggend",                "count": 4, "double": False, "layout": "grid_2x2"},
    {"id": "3_small_vertical","label": "3 foto's onder elkaar",      "count": 3, "double": False, "layout": "vertical"},
    {"id": "2_single_photos", "label": "2 enkele foto's",            "count": 2, "double": False, "layout": "vertical"},
    {"id": "1_single_photo",  "label": "1 enkele foto",              "count": 1, "double": False, "layout": "single_centered"},
]


class _PreviewWidget(QWidget):
    """Widget that draws a scaled preview of frame layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frames = []
        self.canvas_w = SINGLE_STRIP_W
        self.canvas_h = SINGLE_STRIP_H
        self.setMinimumSize(250, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_layout(self, frames, canvas_w, canvas_h):
        self.frames = frames
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate scale to fit widget
        w = self.width() - 20
        h = self.height() - 20
        scale_x = w / self.canvas_w
        scale_y = h / self.canvas_h
        scale = min(scale_x, scale_y)

        offset_x = (self.width() - self.canvas_w * scale) / 2
        offset_y = (self.height() - self.canvas_h * scale) / 2

        # Draw canvas background (white with border)
        painter.setPen(QPen(QColor("#333333"), 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(
            int(offset_x), int(offset_y),
            int(self.canvas_w * scale), int(self.canvas_h * scale)
        )

        # Draw cut line for double strip
        if self.canvas_w == DOUBLE_STRIP_W:
            cx = offset_x + (SINGLE_STRIP_W * scale)
            painter.setPen(QPen(QColor("#999999"), 1, Qt.DashLine))
            painter.drawLine(int(cx), int(offset_y), int(cx), int(offset_y + self.canvas_h * scale))

        # Draw frames
        font = QFont("DM Sans", max(8, int(30 * scale)), QFont.Bold)
        painter.setFont(font)

        for i, frame in enumerate(self.frames):
            x = offset_x + frame.x * scale
            y = offset_y + frame.y * scale
            fw = frame.width * scale
            fh = frame.height * scale

            # Green filled rectangle
            painter.setPen(QPen(QColor("#000000"), max(1, int(2 * scale))))
            painter.setBrush(QColor(0, 220, 0, 180))
            painter.drawRect(int(x), int(y), int(fw), int(fh))

            # Diagonal cross lines
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawLine(int(x), int(y), int(x + fw), int(y + fh))
            painter.drawLine(int(x + fw), int(y), int(x), int(y + fh))

            # Number label centered
            painter.setPen(QColor("#000000"))
            text = str(i + 1)
            tr = painter.fontMetrics().boundingRect(text)
            tx = x + (fw - tr.width()) / 2
            ty = y + (fh + tr.height()) / 2 - 4
            painter.drawText(int(tx), int(ty), text)

        painter.end()


class PresetLayoutDialog(QDialog):
    """Dialog for selecting a preset layout with margins and spacing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preset Layouts")
        self.setMinimumSize(750, 600)
        self._build_ui()
        self._update_preview()

    def _build_ui(self):
        main_lay = QHBoxLayout(self)

        # --- Left panel ---
        left = QVBoxLayout()

        # Preset radio buttons
        preset_group = QGroupBox("Preset layouts")
        preset_lay = QVBoxLayout(preset_group)
        self._radio_group = QButtonGroup(self)

        for i, preset in enumerate(PRESETS):
            radio = QRadioButton(preset["label"])
            self._radio_group.addButton(radio, i)
            preset_lay.addWidget(radio)
            if i == 0:
                radio.setChecked(True)

        self._radio_group.buttonClicked.connect(lambda: self._update_preview())
        left.addWidget(preset_group)

        # Margins
        margin_group = QGroupBox("Marges")
        margin_form = QFormLayout(margin_group)

        self.margin_top = QSpinBox()
        self.margin_top.setRange(0, 200)
        self.margin_top.setValue(30)
        self.margin_top.setSuffix(" px")
        margin_form.addRow("Boven:", self.margin_top)

        self.margin_bottom = QSpinBox()
        self.margin_bottom.setRange(0, 200)
        self.margin_bottom.setValue(30)
        self.margin_bottom.setSuffix(" px")
        margin_form.addRow("Onder:", self.margin_bottom)

        self.margin_left = QSpinBox()
        self.margin_left.setRange(0, 200)
        self.margin_left.setValue(30)
        self.margin_left.setSuffix(" px")
        margin_form.addRow("Links:", self.margin_left)

        self.margin_right = QSpinBox()
        self.margin_right.setRange(0, 200)
        self.margin_right.setValue(30)
        self.margin_right.setSuffix(" px")
        margin_form.addRow("Rechts:", self.margin_right)

        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 200)
        self.spacing_spin.setValue(30)
        self.spacing_spin.setSuffix(" px")
        margin_form.addRow("Tussenruimte:", self.spacing_spin)

        for spin in (self.margin_top, self.margin_bottom, self.margin_left,
                     self.margin_right, self.spacing_spin):
            spin.valueChanged.connect(self._update_preview)

        left.addWidget(margin_group)

        # Aspect ratio
        self.aspect_check = QCheckBox("Beeldverhouding 3:2")
        self.aspect_check.setChecked(True)
        self.aspect_check.stateChanged.connect(self._update_preview)
        left.addWidget(self.aspect_check)

        left.addStretch()

        # Buttons
        btn_lay = QHBoxLayout()
        ok_btn = QPushButton("Toepassen")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Annuleren")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        left.addLayout(btn_lay)

        main_lay.addLayout(left, stretch=0)

        # --- Right panel: preview ---
        self._preview = _PreviewWidget()
        main_lay.addWidget(self._preview, stretch=1)

    def _selected_preset(self):
        idx = self._radio_group.checkedId()
        if 0 <= idx < len(PRESETS):
            return PRESETS[idx]
        return PRESETS[0]

    def _calculate_frames(self):
        """Calculate frame positions for the selected preset."""
        preset = self._selected_preset()
        canvas_w = DOUBLE_STRIP_W if preset["double"] else SINGLE_STRIP_W
        canvas_h = SINGLE_STRIP_H

        mt = self.margin_top.value()
        mb = self.margin_bottom.value()
        ml = self.margin_left.value()
        mr = self.margin_right.value()
        spacing = self.spacing_spin.value()
        use_aspect = self.aspect_check.isChecked()

        usable_w = canvas_w - ml - mr
        usable_h = canvas_h - mt - mb
        count = preset["count"]
        layout = preset["layout"]

        if usable_w <= 0 or usable_h <= 0:
            return []

        frames = []

        if layout == "vertical":
            frame_h = (usable_h - (count - 1) * spacing) // count
            frame_w = usable_w

            if use_aspect and frame_h > 0:
                target_w = int(frame_h * DEFAULT_ASPECT)
                if target_w <= usable_w:
                    frame_w = target_w
                else:
                    frame_h = int(usable_w / DEFAULT_ASPECT)

            x_offset = ml + (usable_w - frame_w) // 2
            # Center vertically
            total_h = count * frame_h + (count - 1) * spacing
            y_start = mt + (usable_h - total_h) // 2
            for i in range(count):
                y = y_start + i * (frame_h + spacing)
                frames.append(PhotoFrame(x=x_offset, y=y, width=frame_w, height=frame_h))

        elif layout == "grid_2x2":
            # 2 rows x 2 columns grid with 3:2 aspect ratio
            rows, cols = 2, 2
            frame_w = (usable_w - (cols - 1) * spacing) // cols
            frame_h = int(frame_w / DEFAULT_ASPECT) if use_aspect else (usable_h - (rows - 1) * spacing) // rows

            # Check if all rows fit
            total_h = rows * frame_h + (rows - 1) * spacing
            if total_h > usable_h:
                frame_h = (usable_h - (rows - 1) * spacing) // rows
                if use_aspect:
                    frame_w = int(frame_h * DEFAULT_ASPECT)

            # Center the grid
            grid_w = cols * frame_w + (cols - 1) * spacing
            grid_h = rows * frame_h + (rows - 1) * spacing
            x_start = ml + (usable_w - grid_w) // 2
            y_start = mt + (usable_h - grid_h) // 2

            for r in range(rows):
                for c in range(cols):
                    x = x_start + c * (frame_w + spacing)
                    y = y_start + r * (frame_h + spacing)
                    frames.append(PhotoFrame(x=x, y=y, width=frame_w, height=frame_h))

        elif layout == "single":
            # Full page single photo (fills as much as possible)
            frame_w = usable_w
            frame_h = usable_h

            x = ml + (usable_w - frame_w) // 2
            y = mt + (usable_h - frame_h) // 2
            frames.append(PhotoFrame(x=x, y=y, width=frame_w, height=frame_h))

        elif layout == "single_centered":
            # Centered single photo with 3:2 aspect ratio
            frame_w = usable_w
            frame_h = int(frame_w / DEFAULT_ASPECT)

            if frame_h > usable_h:
                frame_h = usable_h
                frame_w = int(frame_h * DEFAULT_ASPECT)

            x = ml + (usable_w - frame_w) // 2
            y = mt + (usable_h - frame_h) // 2
            frames.append(PhotoFrame(x=x, y=y, width=frame_w, height=frame_h))

        return frames

    def _update_preview(self):
        preset = self._selected_preset()
        canvas_w = DOUBLE_STRIP_W if preset["double"] else SINGLE_STRIP_W
        frames = self._calculate_frames()
        self._preview.set_layout(frames, canvas_w, SINGLE_STRIP_H)

    def get_result(self):
        """Return (list[PhotoFrame], is_double_strip)."""
        preset = self._selected_preset()
        return self._calculate_frames(), preset["double"]
