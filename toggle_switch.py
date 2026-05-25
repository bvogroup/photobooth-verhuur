"""
iOS-style toggle switch widget.

Drop-in replacement voor `QCheckBox` als het om aan/uit-instellingen gaat:
duidelijke gekleurde "pil" met een witte cirkel die soepel naar links/rechts
animeert. Veel beter zichtbaar dan een gewone checkbox.

API-compatibel met QCheckBox/QAbstractButton:
  - isChecked() / setChecked(bool)
  - toggled(bool) signal
  - clicked() signal
  - setText() / text()
  - blockSignals()

Gebruik:
    toggle = ToggleSwitch("Mijn instelling")
    toggle.setChecked(True)
    toggle.toggled.connect(self._on_my_setting)
"""

from PyQt5.QtCore import (
    QPropertyAnimation, QRectF, QSize, Qt, pyqtProperty, pyqtSignal,
    QEasingCurve
)
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter
from PyQt5.QtWidgets import QAbstractButton, QSizePolicy


class ToggleSwitch(QAbstractButton):
    """Custom iOS-stijl toggle. Erft van QAbstractButton zodat het de
    standaard Qt button-API krijgt (toggled signal, isChecked, etc.).

    Extra signal voor QCheckBox-compatibiliteit: ``stateChanged(int)``
    wordt geemit met Qt.Checked (2) / Qt.Unchecked (0) zodat bestaande
    code die ``.stateChanged.connect()`` doet ook werkt."""

    # QCheckBox-compatibel signal — emit Qt.Checked (2) of Qt.Unchecked (0).
    stateChanged = pyqtSignal(int)

    # Vaste afmetingen — laten zich nog overriden via property als nodig.
    TRACK_W = 52
    TRACK_H = 28
    THUMB_MARGIN = 3                    # padding tussen thumb en track-rand
    SPACING = 12                         # ruimte tussen pil en tekst

    # Kleuren (matchen het Clixibo design system maar fallback hardcoded).
    OFF_TRACK_COLOR = QColor("#D4D1CA")  # grijs
    ON_TRACK_COLOR = QColor("#4A9B6E")   # groen (COLOR_SUCCESS)
    THUMB_COLOR = QColor("#FFFFFF")
    TEXT_COLOR = QColor("#53565A")
    DISABLED_OPACITY = 0.45

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        # Geen achtergrond renderen via stylesheet — wij doen alles in paintEvent
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._thumb_diameter = self.TRACK_H - 2 * self.THUMB_MARGIN
        # Thumb-positie (x-coordinaat van de cirkel) — geanimeerd
        self._thumb_pos = float(self.THUMB_MARGIN)

        self._anim = QPropertyAnimation(self, b"thumbPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        # Sync thumb bij elke toggle (animatie)
        self.toggled.connect(self._animate_thumb)
        # Emit QCheckBox-compatibel stateChanged signal naast toggled
        self.toggled.connect(
            lambda on: self.stateChanged.emit(Qt.Checked if on else Qt.Unchecked)
        )

        # Default font — kan overschreven worden via setFont
        self.setFont(QFont("DM Sans", 11))

    # ── Geometrie helpers ──────────────────────────────────────────

    def _thumb_end_pos(self, checked: bool) -> float:
        if checked:
            return float(self.TRACK_W - self._thumb_diameter - self.THUMB_MARGIN)
        return float(self.THUMB_MARGIN)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        w = self.TRACK_W + (self.SPACING + text_w if text_w else 0)
        h = max(self.TRACK_H, fm.height())
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ── Animatie property ──────────────────────────────────────────

    def _get_thumb_pos(self) -> float:
        return self._thumb_pos

    def _set_thumb_pos(self, v: float):
        self._thumb_pos = float(v)
        self.update()

    thumbPos = pyqtProperty(float, fget=_get_thumb_pos, fset=_set_thumb_pos)

    def _animate_thumb(self, on: bool):
        end = self._thumb_end_pos(on)
        if self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        self._anim.setStartValue(self._thumb_pos)
        self._anim.setEndValue(end)
        self._anim.start()

    # ── State setters die thumb-positie syncen ─────────────────────

    def setChecked(self, on: bool):
        was_checked = self.isChecked()
        super().setChecked(on)
        # Als de state niet veranderde (bv. al checked en nog eens setChecked(True)),
        # toch de thumb forceren naar de juiste positie (bv. eerste keer laden).
        if was_checked == bool(on):
            self._thumb_pos = self._thumb_end_pos(bool(on))
            self.update()

    # ── Hit test: hele widget reageert op klikken ──────────────────

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    # ── Paint event ────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)

        # Verticaal centreren van de pil
        track_y = (self.height() - self.TRACK_H) / 2

        # Disabled state: alles wat lichter
        if not self.isEnabled():
            p.setOpacity(self.DISABLED_OPACITY)

        # Track (gekleurde pil)
        track_color = self.ON_TRACK_COLOR if self.isChecked() else self.OFF_TRACK_COLOR
        p.setBrush(QBrush(track_color))
        track_rect = QRectF(0, track_y, self.TRACK_W, self.TRACK_H)
        p.drawRoundedRect(track_rect, self.TRACK_H / 2, self.TRACK_H / 2)

        # Thumb (witte cirkel met subtiele schaduw via tweede ellips)
        thumb_y = track_y + self.THUMB_MARGIN
        # Schaduw (kleine offset, half-transparant)
        shadow = QColor(0, 0, 0, 35)
        p.setBrush(QBrush(shadow))
        p.drawEllipse(
            QRectF(self._thumb_pos, thumb_y + 1.5,
                   self._thumb_diameter, self._thumb_diameter)
        )
        # De cirkel zelf
        p.setBrush(QBrush(self.THUMB_COLOR))
        p.drawEllipse(
            QRectF(self._thumb_pos, thumb_y,
                   self._thumb_diameter, self._thumb_diameter)
        )

        # Tekst-label rechts naast de pil
        if self.text():
            p.setOpacity(1.0 if self.isEnabled() else self.DISABLED_OPACITY)
            p.setPen(self.TEXT_COLOR)
            p.setFont(self.font())
            text_x = self.TRACK_W + self.SPACING
            text_rect = QRectF(text_x, 0, self.width() - text_x, self.height())
            p.drawText(
                text_rect,
                Qt.AlignVCenter | Qt.AlignLeft,
                self.text()
            )

    # ── No-op stylesheet override ──────────────────────────────────
    # De bestaande code roept `setStyleSheet(toggle_style)` met QCheckBox-CSS
    # aan. Voor onze custom widget heeft die CSS geen effect (we tekenen
    # alles zelf in paintEvent), dus we accepteren de aanroep stilletjes.
