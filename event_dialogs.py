"""
Event dialogs for photobooth event management.
Warm beige/gold themed dialog for creating and editing events.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFormLayout, QWidget, QScrollArea, QGridLayout,
    QFrame, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap

import config
from event_model import Event
from template_model import Template, list_templates


DIALOG_STYLE = f"""
QDialog {{
    background-color: {config.COLOR_BG};
    color: {config.COLOR_TEXT};
}}
QLabel {{
    color: {config.COLOR_TEXT};
    background: transparent;
    font-size: 14px;
}}
QLineEdit {{
    background-color: {config.COLOR_INPUT_BG};
    color: {config.COLOR_TEXT};
    border: 2px solid {config.COLOR_BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 15px;
    min-height: 20px;
}}
QLineEdit:focus {{
    border-color: {config.COLOR_PRIMARY};
}}
QPushButton {{
    border: none;
    border-radius: 12px;
    padding: 12px 30px;
    font-size: 16px;
    font-weight: bold;
    min-height: 40px;
}}
QPushButton#primaryBtn {{
    background-color: {config.COLOR_PRIMARY};
    color: {config.COLOR_TEXT_ON_PRIMARY};
}}
QPushButton#primaryBtn:hover {{
    background-color: {config.COLOR_PRIMARY_HOVER};
}}
QPushButton#secondaryBtn {{
    background-color: {config.COLOR_SECONDARY};
    color: {config.COLOR_TEXT_ON_PRIMARY};
}}
QPushButton#secondaryBtn:hover {{
    background-color: {config.COLOR_SECONDARY_HOVER};
}}
QPushButton#dangerBtn {{
    background-color: {config.COLOR_DANGER};
    color: {config.COLOR_TEXT_ON_PRIMARY};
}}
QPushButton#dangerBtn:hover {{
    background-color: #e74c3c;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
"""


class EventDialog(QDialog):
    """Simple dialog for creating or editing an event."""

    def __init__(self, parent=None, event: Event = None):
        super().__init__(parent)
        self.event = event
        self._selected_template_name = event.template_name if event else ""
        self._template_widgets = {}

        self.setWindowTitle("Event Bewerken" if event else "Nieuw Event")
        self.setMinimumSize(600, 500)
        self.setStyleSheet(DIALOG_STYLE)

        self._build_ui()
        if event:
            self._populate(event)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 20)

        # Title
        title = QLabel("Event Bewerken" if self.event else "Nieuw Event")
        title.setFont(QFont("DM Sans", 24, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(4)

        # Form fields
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Bijv. Bruiloft Jan & Lisa")
        form.addRow("Naam:", self.name_edit)

        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("JJJJ-MM-DD")
        form.addRow("Datum:", self.date_edit)

        self.location_edit = QLineEdit()
        self.location_edit.setPlaceholderText("Bijv. Hotel De Kroon, Amsterdam")
        form.addRow("Locatie:", self.location_edit)

        layout.addLayout(form)

        # Template section
        layout.addSpacing(6)
        tmpl_header = QHBoxLayout()
        tmpl_title = QLabel("Template")
        tmpl_title.setFont(QFont("DM Sans", 16, QFont.Bold))
        tmpl_title.setStyleSheet(f"color: {config.COLOR_PRIMARY};")
        tmpl_header.addWidget(tmpl_title)
        tmpl_header.addStretch()
        self._template_label = QLabel("Geen geselecteerd")
        self._template_label.setFont(QFont("DM Sans", 12))
        self._template_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        tmpl_header.addWidget(self._template_label)
        layout.addLayout(tmpl_header)

        # Template grid in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            f"QScrollBar:vertical {{ background: {config.COLOR_BG}; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {config.COLOR_BORDER}; border-radius: 4px; }}"
        )
        scroll_widget = QWidget()
        self._template_grid = QGridLayout(scroll_widget)
        self._template_grid.setSpacing(8)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, stretch=1)
        self._load_template_grid()

        # Buttons
        layout.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton("Annuleren")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        btn_row.addStretch()

        if self.event:
            delete_btn = QPushButton("Verwijderen")
            delete_btn.setObjectName("dangerBtn")
            delete_btn.setCursor(Qt.PointingHandCursor)
            delete_btn.clicked.connect(self._on_delete)
            btn_row.addWidget(delete_btn)

        save_btn = QPushButton("Opslaan")
        save_btn.setObjectName("primaryBtn")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _load_template_grid(self):
        templates = list_templates(config.TEMPLATES_DIR, config.BACKGROUNDS_DIR)
        self._template_widgets.clear()
        col, row, max_cols = 0, 0, 5
        for t in templates:
            card = self._make_template_card(t)
            self._template_grid.addWidget(card, row, col)
            self._template_widgets[t.name] = card
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        self._update_template_selection()

    def _make_template_card(self, template: Template) -> QFrame:
        card = QFrame()
        card.setFixedSize(100, 120)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(
            f"QFrame {{ background: {config.COLOR_CARD_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; }}"
        )

        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        thumb = QLabel()
        thumb.setFixedSize(92, 80)
        thumb.setAlignment(Qt.AlignCenter)

        bg_path = template.get_thumbnail_path()
        if bg_path:
            pix = QPixmap(bg_path).scaled(92, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.setPixmap(pix)
        else:
            thumb.setText(str(template.num_photos))
            thumb.setFont(QFont("DM Sans", 20, QFont.Bold))
            thumb.setStyleSheet(f"color: {config.COLOR_PRIMARY}; background: {config.COLOR_ACCENT}; border-radius: 4px;")
        lay.addWidget(thumb)

        name = QLabel(template.name)
        name.setFont(QFont("DM Sans", 8))
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setMaximumHeight(24)
        name.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")
        lay.addWidget(name)

        card.mousePressEvent = lambda ev, n=template.name: self._select_template(n)
        return card

    def _select_template(self, name: str):
        self._selected_template_name = name
        self._update_template_selection()

    def _update_template_selection(self):
        for tname, card in self._template_widgets.items():
            if tname == self._selected_template_name:
                card.setStyleSheet(
                    f"QFrame {{ background: {config.COLOR_CARD_BG}; border: 3px solid {config.COLOR_PRIMARY}; border-radius: 8px; }}"
                )
                self._template_label.setText(tname)
                self._template_label.setStyleSheet(f"color: {config.COLOR_PRIMARY}; font-weight: bold;")
            else:
                card.setStyleSheet(
                    f"QFrame {{ background: {config.COLOR_CARD_BG}; border: 2px solid {config.COLOR_BORDER}; border-radius: 8px; }}"
                )
        if not self._selected_template_name:
            self._template_label.setText("Geen geselecteerd")
            self._template_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM};")

    def _populate(self, event: Event):
        self.name_edit.setText(event.name)
        self.date_edit.setText(event.date)
        self.location_edit.setText(event.location)
        self._selected_template_name = event.template_name
        self._update_template_selection()

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Fout", "Voer een eventnaam in.")
            return

        if self.event:
            self.event.name = name
            self.event.date = self.date_edit.text().strip()
            self.event.location = self.location_edit.text().strip()
            self.event.template_name = self._selected_template_name
            self.result_event = self.event
        else:
            self.result_event = Event.create_new(
                name=name,
                date=self.date_edit.text().strip(),
                location=self.location_edit.text().strip(),
            )
            self.result_event.template_name = self._selected_template_name

        self.accept()

    def _on_delete(self):
        reply = QMessageBox.question(
            self, "Verwijderen",
            f"Weet je zeker dat je '{self.event.name}' wilt verwijderen?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.result_event = None
            self.done(2)
