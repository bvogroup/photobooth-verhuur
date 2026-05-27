"""
Interactive template editor for the photobooth.

Provides a QGraphicsView-based editor where users can:
- Load a background image (from Canva or any PNG/JPG)
- Add, move, and resize photo frames (touch + mouse)
- Save templates as JSON for use in the photobooth

Open via F12 in the photobooth app.
"""

import os
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem,
    QFileDialog, QInputDialog, QMessageBox, QToolBar, QAction, QStatusBar,
    QDialog, QGraphicsSimpleTextItem
)
from PyQt5.QtCore import Qt, QRectF, QPointF, QSize
from PyQt5.QtGui import (
    QPixmap, QPen, QBrush, QColor, QFont, QPainter, QCursor
)

import config
from template_model import Template, PhotoFrame

# Canvas dimensions (4x6 inch @ 300 DPI, one strip half)
CANVAS_W = 600
CANVAS_H = 1800

# Default frame size
DEFAULT_FRAME_W = 540
DEFAULT_FRAME_H = 360


class ResizeHandle(QGraphicsRectItem):
    """Corner handle for resizing a PhotoFrameItem. Large for touch screens."""

    HANDLE_SIZE = 36  # Large for touch

    def __init__(self, parent_frame, corner):
        super().__init__(0, 0, self.HANDLE_SIZE, self.HANDLE_SIZE)
        self.parent_frame = parent_frame
        self.corner = corner  # "tl", "tr", "bl", "br"
        self.setBrush(QBrush(QColor(233, 69, 96, 200)))
        self.setPen(QPen(QColor("#ffffff"), 2))
        self.setFlag(QGraphicsRectItem.ItemIsMovable, False)
        self.setCursor(QCursor(Qt.SizeFDiagCursor if corner in ("tl", "br") else Qt.SizeBDiagCursor))
        self.setZValue(100)
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._start_pos = event.scenePos()
            self._start_rect = self.parent_frame.rect()
            self._start_frame_pos = self.parent_frame.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        delta = event.scenePos() - self._start_pos
        r = QRectF(self._start_rect)
        pos = QPointF(self._start_frame_pos)

        min_size = 80

        if self.corner == "br":
            r.setWidth(max(min_size, r.width() + delta.x()))
            r.setHeight(max(min_size, r.height() + delta.y()))
        elif self.corner == "bl":
            new_w = max(min_size, r.width() - delta.x())
            pos.setX(pos.x() + (r.width() - new_w))
            r.setWidth(new_w)
            r.setHeight(max(min_size, r.height() + delta.y()))
        elif self.corner == "tr":
            r.setWidth(max(min_size, r.width() + delta.x()))
            new_h = max(min_size, r.height() - delta.y())
            pos.setY(pos.y() + (r.height() - new_h))
            r.setHeight(new_h)
        elif self.corner == "tl":
            new_w = max(min_size, r.width() - delta.x())
            new_h = max(min_size, r.height() - delta.y())
            pos.setX(pos.x() + (r.width() - new_w))
            pos.setY(pos.y() + (r.height() - new_h))
            r.setWidth(new_w)
            r.setHeight(new_h)

        self.parent_frame.setPos(pos)
        self.parent_frame.setRect(0, 0, r.width(), r.height())
        self.parent_frame.update_handles()
        self.parent_frame.update_label()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        event.accept()


class PhotoFrameItem(QGraphicsRectItem):
    """Draggable, resizable rectangle representing a photo frame on the strip.

    - Drag anywhere on the frame to move it
    - Drag red corner handles to resize
    - Click to select (shows highlighted border)
    """

    def __init__(self, x, y, width, height, index=0):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.index = index

        # Styling - more visible fill for touch
        self.setPen(QPen(QColor("#00aaff"), 4, Qt.SolidLine))
        self.setBrush(QBrush(QColor(0, 170, 255, 80)))
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self.setZValue(50)

        # Label
        self._label = QGraphicsSimpleTextItem(self)
        self._label.setFont(QFont("DM Sans", 28, QFont.Bold))
        self._label.setBrush(QBrush(QColor("#ffffff")))

        # Size label (shows dimensions) — must be created BEFORE update_label()
        self._size_label = QGraphicsSimpleTextItem(self)
        self._size_label.setFont(QFont("DM Sans", 16))
        self._size_label.setBrush(QBrush(QColor(255, 255, 255, 180)))

        self.update_label()  # updates both _label and _size_label

        # Resize handles (large red corners)
        self._handles = {}
        for corner in ("tl", "tr", "bl", "br"):
            handle = ResizeHandle(self, corner)
            handle.setParentItem(self)
            self._handles[corner] = handle
        self.update_handles()

    def update_label(self):
        self._label.setText(f"Foto {self.index + 1}")
        lr = self._label.boundingRect()
        self._label.setPos(
            (self.rect().width() - lr.width()) / 2,
            (self.rect().height() - lr.height()) / 2 - 15
        )
        self._update_size_label()

    def _update_size_label(self):
        r = self.rect()
        self._size_label.setText(f"{int(r.width())}x{int(r.height())}")
        sr = self._size_label.boundingRect()
        self._size_label.setPos(
            (r.width() - sr.width()) / 2,
            (r.height() - sr.height()) / 2 + 25
        )

    def update_handles(self):
        hs = ResizeHandle.HANDLE_SIZE
        r = self.rect()
        self._handles["tl"].setPos(-hs / 2, -hs / 2)
        self._handles["tr"].setPos(r.width() - hs / 2, -hs / 2)
        self._handles["bl"].setPos(-hs / 2, r.height() - hs / 2)
        self._handles["br"].setPos(r.width() - hs / 2, r.height() - hs / 2)

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionHasChanged:
            self.update_handles()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.setCursor(QCursor(Qt.ClosedHandCursor))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self._update_size_label()
        super().mouseReleaseEvent(event)

    def to_photo_frame(self):
        """Convert to a PhotoFrame dataclass."""
        pos = self.pos()
        r = self.rect()
        return PhotoFrame(
            x=int(pos.x()),
            y=int(pos.y()),
            width=int(r.width()),
            height=int(r.height()),
        )


class TemplateEditorWindow(QMainWindow):
    """Template editor window with QGraphicsView canvas."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Template Editor")
        self.resize(800, 900)

        self._bg_path = ""
        self._frame_items = []
        self._template_name = "Nieuw template"
        self._is_double_strip = False
        self._is_triple_strip = False

        self._build_ui()

    def _build_ui(self):
        # Toolbar with larger buttons
        toolbar = QToolBar("Tools")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(28, 28))
        toolbar.setStyleSheet(
            "QToolBar { spacing: 4px; padding: 4px; }"
            "QToolButton { font-size: 13px; padding: 6px 10px; }"
        )
        self.addToolBar(toolbar)

        load_bg_action = QAction("Achtergrond laden", self)
        load_bg_action.triggered.connect(lambda _=False: self._load_background())
        toolbar.addAction(load_bg_action)

        add_frame_action = QAction("+ Frame toevoegen", self)
        add_frame_action.triggered.connect(lambda _=False: self._add_frame())
        toolbar.addAction(add_frame_action)

        remove_frame_action = QAction("- Frame verwijderen", self)
        remove_frame_action.triggered.connect(lambda _=False: self._remove_selected_frame())
        toolbar.addAction(remove_frame_action)

        toolbar.addSeparator()

        save_action = QAction("Opslaan", self)
        save_action.triggered.connect(lambda _=False: self._save_template())
        toolbar.addAction(save_action)

        load_action = QAction("Template laden", self)
        load_action.triggered.connect(lambda _=False: self._load_template())
        toolbar.addAction(load_action)

        toolbar.addSeparator()

        preset_action = QAction("Preset layouts", self)
        preset_action.triggered.connect(lambda _=False: self._open_preset_dialog())
        toolbar.addAction(preset_action)

        toolbar.addSeparator()

        idle_bg_action = QAction("Beginscherm achtergrond", self)
        idle_bg_action.triggered.connect(lambda _=False: self._set_idle_background())
        toolbar.addAction(idle_bg_action)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(5, 5, 5, 5)

        # Help text
        help_label = QLabel("Sleep de blauwe vlakken om te verplaatsen | Gebruik rode hoeken om te resizen")
        help_label.setAlignment(Qt.AlignCenter)
        help_label.setStyleSheet(f"color: {config.COLOR_TEXT_DIM}; font-size: 13px; padding: 4px;")
        layout.addWidget(help_label)

        # Graphics view with touch support
        self.scene = QGraphicsScene()
        self.scene.setSceneRect(0, 0, CANVAS_W, CANVAS_H)

        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setRenderHint(QPainter.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.NoDrag)
        self.view.setStyleSheet("background: #2a2a2a; border: 1px solid #555;")
        # Enable touch scrolling/interaction
        self.view.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.view.setInteractive(True)
        layout.addWidget(self.view, stretch=1)

        # Draw canvas boundary
        self._canvas_rect = self.scene.addRect(
            0, 0, CANVAS_W, CANVAS_H,
            QPen(QColor("#555555"), 2),
            QBrush(QColor("#ffffff"))
        )
        self._canvas_rect.setZValue(-10)

        # Fit view
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

        # Status bar
        self.statusBar().showMessage("Laad een achtergrond en voeg frames toe | Sleep om te verplaatsen")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _load_background(self):
        """Open file dialog to load a background image."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Achtergrond laden",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return

        self._bg_path = os.path.normpath(path)
        pixmap = QPixmap(self._bg_path)
        if pixmap.isNull():
            self.statusBar().showMessage("Fout: Kon afbeelding niet laden")
            return

        # Determine current canvas width
        canvas_w = int(self.scene.sceneRect().width())

        # Remove old background pixmap
        for item in self.scene.items():
            if isinstance(item, QGraphicsPixmapItem):
                self.scene.removeItem(item)

        # Scale to canvas
        scaled = pixmap.scaled(canvas_w, CANVAS_H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        bg_item = self.scene.addPixmap(scaled)
        bg_item.setZValue(-5)
        bg_item.setPos(0, 0)

        self.statusBar().showMessage(f"Achtergrond geladen: {os.path.basename(path)}")

    def _add_frame(self):
        """Add a new photo frame to the canvas."""
        try:
            index = len(self._frame_items)
            canvas_w = int(self.scene.sceneRect().width())

            # Calculate smart position: spread frames vertically with spacing
            total_spacing = 30
            frame_w = min(DEFAULT_FRAME_W, canvas_w - 60)
            frame_h = DEFAULT_FRAME_H

            # Stack vertically with gaps
            y = total_spacing + index * (frame_h + total_spacing)
            if y + frame_h > CANVAS_H:
                y = total_spacing  # Wrap around

            x = (canvas_w - frame_w) // 2  # Center horizontally

            frame = PhotoFrameItem(x, y, frame_w, frame_h, index)
            self.scene.addItem(frame)
            self._frame_items.append(frame)
            self._update_frame_indices()

            # Ensure view is zoomed correctly
            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

            self.statusBar().showMessage(
                f"Frame {index + 1} toegevoegd | Sleep om te verplaatsen, rode hoeken om te resizen"
            )
            print(f"[EDITOR] Frame {index + 1} toegevoegd op ({x}, {y}) {frame_w}x{frame_h}")
        except Exception as e:
            print(f"[EDITOR] Fout bij frame toevoegen: {e}")
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"Fout bij frame toevoegen: {e}")

    def _remove_selected_frame(self):
        """Remove the currently selected frame."""
        selected = [item for item in self.scene.selectedItems() if isinstance(item, PhotoFrameItem)]
        if not selected:
            self.statusBar().showMessage("Tik eerst op een frame om het te selecteren, dan 'Frame verwijderen'")
            return

        for frame in selected:
            self.scene.removeItem(frame)
            if frame in self._frame_items:
                self._frame_items.remove(frame)

        self._update_frame_indices()
        self.statusBar().showMessage("Frame verwijderd")

    def _update_frame_indices(self):
        """Re-number all frames sequentially."""
        for i, frame in enumerate(self._frame_items):
            frame.index = i
            frame.update_label()

    def _save_template(self):
        """Save the current template as JSON."""
        if not self._frame_items:
            QMessageBox.warning(self, "Opslaan", "Voeg eerst minimaal 1 frame toe.")
            return

        name, ok = QInputDialog.getText(
            self, "Template opslaan", "Template naam:",
            text=self._template_name
        )
        if not ok or not name.strip():
            return

        self._template_name = name.strip()

        # Build template
        frames = [frame.to_photo_frame() for frame in self._frame_items]
        template = Template(
            name=self._template_name,
            background_path=self._bg_path,
            frames=frames,
            is_double_strip=self._is_double_strip,
            is_triple_strip=getattr(self, '_is_triple_strip', False),
        )

        # Save as JSON
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in self._template_name)
        filename = f"{safe_name}.json"
        save_path = os.path.join(config.TEMPLATES_DIR, filename)
        template.save(save_path)

        # Copy background to backgrounds dir if not already there
        if self._bg_path:
            bg_norm = os.path.normcase(os.path.normpath(self._bg_path))
            dir_norm = os.path.normcase(os.path.normpath(config.BACKGROUNDS_DIR))
            if not bg_norm.startswith(dir_norm):
                bg_dest = os.path.join(config.BACKGROUNDS_DIR, os.path.basename(self._bg_path))
                if not os.path.exists(bg_dest):
                    try:
                        import shutil
                        shutil.copy2(self._bg_path, bg_dest)
                    except Exception as e:
                        print(f"[EDITOR] Kon achtergrond niet kopieren: {e}")
                # Update template to use the backgrounds dir path
                template.background_path = bg_dest
                self._bg_path = bg_dest
                template.save(save_path)

        QMessageBox.information(
            self, "Opgeslagen",
            f"Template '{self._template_name}' is opgeslagen!\n\n"
            f"Bestand: {filename}\n"
            f"Frames: {len(frames)}"
        )
        self.statusBar().showMessage(f"Template opgeslagen: {save_path}")

    def _load_template(self):
        """Load an existing template JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Template laden",
            config.TEMPLATES_DIR,
            "Templates (*.json)"
        )
        if not path:
            return

        try:
            template = Template.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Fout", f"Kon template niet laden:\n{e}")
            return

        # Clear current frames
        for frame in self._frame_items:
            self.scene.removeItem(frame)
        self._frame_items = []

        self._template_name = template.name
        self._is_double_strip = template.is_double_strip
        self._is_triple_strip = getattr(template, 'is_triple_strip', False)

        # Resize canvas based on strip type:
        # triple_strip (DNP)  = 600x1200 portrait (5x10 cm)
        # double_strip        = 1200x1800 (volledige print)
        # single (default)    = 600x1800 (halve print, gedupliceerd)
        if self._is_triple_strip:
            new_w, new_h = 600, 1200
        elif template.is_double_strip:
            new_w, new_h = 1200, CANVAS_H
        else:
            new_w, new_h = CANVAS_W, CANVAS_H
        self.scene.setSceneRect(0, 0, new_w, new_h)
        self._canvas_rect.setRect(0, 0, new_w, new_h)

        # Load background
        if template.background_path and os.path.isfile(template.background_path):
            self._bg_path = template.background_path
            pixmap = QPixmap(template.background_path)
            if not pixmap.isNull():
                # Remove old bg
                for item in self.scene.items():
                    if isinstance(item, QGraphicsPixmapItem):
                        self.scene.removeItem(item)
                scaled = pixmap.scaled(new_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                bg_item = self.scene.addPixmap(scaled)
                bg_item.setZValue(-5)
        else:
            self._bg_path = ""
            # Remove old bg
            for item in self.scene.items():
                if isinstance(item, QGraphicsPixmapItem):
                    self.scene.removeItem(item)

        # Add frames
        for i, pf in enumerate(template.frames):
            frame = PhotoFrameItem(pf.x, pf.y, pf.width, pf.height, i)
            self.scene.addItem(frame)
            self._frame_items.append(frame)

        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.statusBar().showMessage(f"Template geladen: {template.name} ({template.num_photos} frames)")

    def _open_preset_dialog(self):
        """Open the preset layout dialog."""
        from preset_layouts import PresetLayoutDialog
        dialog = PresetLayoutDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            frames, is_double = dialog.get_result()

            # Clear existing frames
            for frame in self._frame_items:
                self.scene.removeItem(frame)
            self._frame_items = []

            # Update canvas size
            new_w = 1200 if is_double else CANVAS_W
            self.scene.setSceneRect(0, 0, new_w, CANVAS_H)
            self._canvas_rect.setRect(0, 0, new_w, CANVAS_H)
            self._is_double_strip = is_double

            # Reload background at new canvas size if needed
            if self._bg_path and os.path.isfile(self._bg_path):
                for item in self.scene.items():
                    if isinstance(item, QGraphicsPixmapItem):
                        self.scene.removeItem(item)
                pixmap = QPixmap(self._bg_path)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(new_w, CANVAS_H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    bg_item = self.scene.addPixmap(scaled)
                    bg_item.setZValue(-5)

            # Add frames from preset
            for i, pf in enumerate(frames):
                item = PhotoFrameItem(pf.x, pf.y, pf.width, pf.height, i)
                self.scene.addItem(item)
                self._frame_items.append(item)

            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
            self.statusBar().showMessage(
                f"Preset layout toegepast: {len(frames)} frames "
                f"({'dubbele' if is_double else 'enkele'} strip)"
            )

    def _set_idle_background(self):
        """Set a background image for the idle/start screen."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Beginscherm achtergrond kiezen",
            config.BACKGROUNDS_DIR,
            "Afbeeldingen (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not path:
            return

        # Save to settings.json
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
            QMessageBox.information(
                self, "Beginscherm",
                f"Achtergrond ingesteld:\n{os.path.basename(path)}\n\n"
                "Herstart de photobooth om de wijziging te zien."
            )
        except Exception as e:
            QMessageBox.critical(self, "Fout", f"Kon instelling niet opslaan:\n{e}")
