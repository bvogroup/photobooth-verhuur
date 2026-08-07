"""
Animated countdown ring widget for premium photobooth capture flow.

Renders a large centered number with a circular progress ring that fills
smoothly over each second of the countdown. Uses QPainter for lightweight
rendering at ~60fps.
"""

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QElapsedTimer
from PyQt5.QtGui import QPainter, QPen, QColor

import merk

import config


class CountdownRingWidget(QWidget):
    """Overlay widget: animated circular ring + large countdown number."""

    RING_WIDTH = 14          # pen width for the arc
    RING_SIZE_RATIO = 0.55   # ring diameter relative to min(width, height)
    TRACK_OPACITY = 0.25     # opacity of unfilled background circle
    ANIM_INTERVAL_MS = 16    # ~60fps animation timer

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        self._current_number = 0
        self._progress = 0.0       # 0.0 → 1.0 within current second
        self._number_str = "0"     # cached string conversion

        # De ring is het merkgroen: dit is een actieve toestand die aftelt, en
        # daar is het accent voor. Het getal blijft wit — dat moet ook leesbaar
        # zijn boven een lichte achtergrond in het live beeld, en daarvoor is de
        # donkere omtrek hieronder.
        self._ring_color = QColor(merk.GROEN)
        self._text_color = QColor(merk.OP_DONKER)

        # Cached font — updated when widget resizes
        self._cached_font = merk.letter(merk.TEKST_REUS, vet=True, kop=True)
        self._cached_diameter = -1

        # Pre-created pen objects (avoid per-frame allocation)
        # De schaduw en de omtrek staan in de merkinkt in plaats van in zuiver
        # zwart: zwart slaat op een warm beeld grijs uit.
        _i = QColor(merk.INKT)
        self._shadow_pen = QPen(QColor(_i.red(), _i.green(), _i.blue(), 70),
                                self.RING_WIDTH + 4, Qt.SolidLine, Qt.RoundCap)
        self._outline_pen = QPen(QColor(_i.red(), _i.green(), _i.blue(), 140),
                                 4, Qt.SolidLine, Qt.RoundCap)
        self._arc_pen = QPen(self._ring_color, self.RING_WIDTH, Qt.SolidLine, Qt.RoundCap)

        # Track pen (semi-transparent) — cached
        track_color = QColor(self._ring_color)
        track_color.setAlphaF(self.TRACK_OPACITY)
        self._track_pen = QPen(track_color, self.RING_WIDTH, Qt.SolidLine, Qt.RoundCap)

        # Precise elapsed time for smooth animation
        self._elapsed = QElapsedTimer()

        # Animation timer (~60fps)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self.ANIM_INTERVAL_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)

    def start_second(self, number):
        """Begin animating a new countdown second."""
        self._current_number = number
        self._number_str = str(number)
        self._progress = 0.0
        self._elapsed.start()
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def stop(self):
        """Stop animation timer."""
        self._anim_timer.stop()
        self._progress = 0.0
        self.update()

    def reset(self):
        """Full reset: stop animation, clear number, hide."""
        self.stop()
        self._current_number = 0
        self._number_str = "0"
        self.hide()

    def _on_anim_tick(self):
        """Update progress based on real elapsed time (drift-free)."""
        elapsed_ms = self._elapsed.elapsed()
        if self._current_number == 0:
            # At "0": keep looping the ring until externally stopped
            self._progress = (elapsed_ms % 1000) / 1000.0
        else:
            self._progress = min(1.0, elapsed_ms / 1000.0)
        self.update()

    def paintEvent(self, event):
        if self._current_number < 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        center_x, center_y = w // 2, h // 2

        # Ring dimensions
        diameter = int(min(w, h) * self.RING_SIZE_RATIO)
        radius = diameter // 2
        rx = center_x - radius
        ry = center_y - radius

        # 1. Dark shadow track
        painter.setPen(self._shadow_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(rx, ry, diameter, diameter)

        # 2. Background track circle (semi-transparent)
        painter.setPen(self._track_pen)
        painter.drawEllipse(rx, ry, diameter, diameter)

        # 3. Progress arc (clockwise from 12 o'clock)
        if self._progress > 0.001:
            painter.setPen(self._arc_pen)
            start_angle = 90 * 16
            span_angle = -int(self._progress * 360 * 16)
            painter.drawArc(rx, ry, diameter, diameter, start_angle, span_angle)

        # 4. Countdown number — cache font when diameter changes
        if diameter != self._cached_diameter:
            self._cached_diameter = diameter
            self._cached_font = merk.letter(
                max(merk.TEKST_REUS, diameter // 3), vet=True, kop=True)

        painter.setFont(self._cached_font)

        # Dark outline for contrast (4 diagonal offsets)
        painter.setPen(self._outline_pen)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            painter.drawText(rx + dx, ry + dy, diameter, diameter,
                             Qt.AlignCenter, self._number_str)
        # White number on top
        painter.setPen(self._text_color)
        painter.drawText(rx, ry, diameter, diameter,
                         Qt.AlignCenter, self._number_str)

        painter.end()
