"""
Template model for photobooth photo strips.

A Template defines how photos are placed on a background:
- Background image (PNG/JPG)
- List of PhotoFrames with position, size, and rotation

Templates are saved as JSON files in the templates directory.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class PhotoFrame:
    """A rectangular area where a photo will be placed on the strip."""
    x: int          # left position in pixels (on 1200x1800 canvas)
    y: int          # top position in pixels
    width: int      # frame width in pixels
    height: int     # frame height in pixels
    rotation: float = 0.0  # rotation in degrees


@dataclass
class Template:
    """A photo strip template with background and photo frame positions."""
    name: str
    background_path: str        # path to background image (PNG/JPG)
    frames: List[PhotoFrame] = field(default_factory=list)
    is_double_strip: bool = False  # True = full 1200x1800, False = 600x1800 duplicated
    cut_default: bool = True     # Default cut setting for this layout
    # Verhuur DNP-modus: True = 5x10 cm strip-ontwerp in 600x1200 portrait canvas
    # dat bij printen 90° gedraaid en 3x gestapeld wordt op het 1200x1800 vel.
    # Bij triple strip wordt is_double_strip genegeerd.
    is_triple_strip: bool = False

    @property
    def num_photos(self):
        return len(self.frames)

    def save(self, path: str):
        """Save template as JSON file."""
        data = {
            "name": self.name,
            "background_path": self.background_path,
            "frames": [asdict(f) for f in self.frames],
            "is_double_strip": self.is_double_strip,
            "cut_default": self.cut_default,
            "is_triple_strip": self.is_triple_strip,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "Template":
        """Load template from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        frames = [PhotoFrame(**fd) for fd in data.get("frames", [])]
        return cls(
            name=data["name"],
            background_path=data.get("background_path", ""),
            frames=frames,
            is_double_strip=data.get("is_double_strip", False),
            cut_default=data.get("cut_default", True),
            is_triple_strip=data.get("is_triple_strip", False),
        )

    @classmethod
    def default_template(cls) -> "Template":
        """Create a default template (white background, 3 photos vertically).

        Uses same margins as get_preset_layouts() to leave room for
        template header/footer (logos, text).
        """
        frames = _make_strip_frames_ar(3, 600, 30, 30, 3.0 / 2.0)
        return cls(name="Dubbele strip met 3 foto's", background_path="", frames=frames)

    @classmethod
    def single_photo_template(cls) -> "Template":
        """Create a default template with 1 large centered photo."""
        STRIP_W = 600
        PADDING = 30
        PRINT_H = 1800

        photo_w = STRIP_W - (2 * PADDING)
        photo_h = int(photo_w / (3.0 / 2.0))  # 3:2 landscape aspect ratio

        # Center vertically
        y = (PRINT_H - photo_h) // 2

        frames = [PhotoFrame(x=PADDING, y=y, width=photo_w, height=photo_h)]
        return cls(name="1 enkele foto", background_path="", frames=frames)

    @classmethod
    def from_legacy_background(cls, bg_path: str) -> "Template":
        """Create a template from a legacy background image.

        Uses the same 3-photo vertical layout as the default template
        but with the specified background image.
        """
        default = cls.default_template()
        name = os.path.splitext(os.path.basename(bg_path))[0]
        return cls(name=name, background_path=bg_path, frames=default.frames)

    def get_thumbnail_path(self) -> Optional[str]:
        """Get path to use as thumbnail (background image or None)."""
        if self.background_path and os.path.isfile(self.background_path):
            return self.background_path
        return None


def _make_strip_frames(num: int, strip_w: int = 600, margin: int = 30,
                       spacing: int = 30) -> List[PhotoFrame]:
    """Calculate evenly spaced vertical frames for a strip layout."""
    photo_w = strip_w - 2 * margin
    available_h = 1800 - 2 * margin - (num - 1) * spacing
    photo_h = available_h // num
    frames = []
    for i in range(num):
        y = margin + i * (photo_h + spacing)
        frames.append(PhotoFrame(x=margin, y=y, width=photo_w, height=photo_h))
    return frames


def _make_strip_frames_ar(num: int, strip_w: int = 600, margin: int = 30,
                           spacing: int = 30, aspect: float = 3.0 / 2.0,
                           canvas_h: int = 1800) -> List[PhotoFrame]:
    """Calculate evenly spaced vertical frames with enforced aspect ratio.

    Photos maintain the given aspect ratio (default 3:2 landscape like Canon).
    Frames are centered horizontally + vertically (gelijke ruimte boven/onder).
    """
    usable_w = strip_w - 2 * margin
    usable_h = canvas_h - 2 * margin - (num - 1) * spacing

    # Start with full width, calculate height from aspect ratio
    frame_w = usable_w
    frame_h = int(frame_w / aspect)

    # Check if all frames fit vertically
    total_h = num * frame_h + (num - 1) * spacing
    if total_h > usable_h:
        # Too tall: calculate from available height
        frame_h = (usable_h - (num - 1) * spacing) // num
        frame_w = int(frame_h * aspect)

    x_offset = margin + (usable_w - frame_w) // 2
    # Center the block vertically
    total_h = num * frame_h + (num - 1) * spacing
    y_start = margin + (usable_h - total_h) // 2

    frames = []
    for i in range(num):
        y = y_start + i * (frame_h + spacing)
        frames.append(PhotoFrame(x=x_offset, y=y, width=frame_w, height=frame_h))
    return frames


def _make_grid_frames(rows: int, cols: int, strip_w: int = 600,
                      margin: int = 30, spacing: int = 30,
                      aspect: float = 3.0 / 2.0) -> List[PhotoFrame]:
    """Calculate grid-arranged frames with enforced aspect ratio.

    Creates a rows × cols grid of frames, each with the given aspect ratio.
    """
    usable_w = strip_w - 2 * margin - (cols - 1) * spacing
    usable_h = 1800 - 2 * margin - (rows - 1) * spacing

    frame_w = usable_w // cols
    frame_h = int(frame_w / aspect)

    # Check if all rows fit
    total_h = rows * frame_h + (rows - 1) * spacing
    if total_h > usable_h:
        frame_h = (usable_h) // rows
        frame_w = int(frame_h * aspect)

    # Center the grid
    grid_w = cols * frame_w + (cols - 1) * spacing
    grid_h = rows * frame_h + (rows - 1) * spacing
    x_start = margin + (usable_w + (cols - 1) * spacing - grid_w) // 2
    y_start = margin + (1800 - 2 * margin - grid_h) // 2

    frames = []
    for r in range(rows):
        for c in range(cols):
            x = x_start + c * (frame_w + spacing)
            y = y_start + r * (frame_h + spacing)
            frames.append(PhotoFrame(x=x, y=y, width=frame_w, height=frame_h))
    return frames


def get_preset_layouts() -> List[Template]:
    """Return all preset layout templates.

    Each preset defines frame positions, cut behaviour, and strip mode.
    Page size: 1200x1800 pixels (4x6 inch @ 300 DPI).
    All photos maintain Canon camera 3:2 landscape aspect ratio.

    Dubbele strips (snijden): left 600px has frames, mirrored to right half.
    Enkele strips (niet snijden): full 1200px width, no mirroring.
    """
    M = 30   # margin
    S = 30   # spacing
    STRIP_W = 600
    FULL_W = 1200
    AR = 3.0 / 2.0  # Canon camera aspect ratio (landscape)

    presets: List[Template] = []

    # ===== DUBBELE STRIPS (snijden) =====

    # --- 1. Dubbele strip met 3 foto's ---
    presets.append(Template(
        name="Dubbele strip met 3 foto's",
        background_path="",
        frames=_make_strip_frames_ar(3, STRIP_W, M, S, AR),
        is_double_strip=False,
        cut_default=True,
    ))

    # --- 2. Dubbele strip met 4 foto's ---
    presets.append(Template(
        name="Dubbele strip met 4 foto's",
        background_path="",
        frames=_make_strip_frames_ar(4, STRIP_W, M, S, AR),
        is_double_strip=False,
        cut_default=True,
    ))

    # --- 3. 2 foto's liggend (rotation=90, 9:16 frames for rotated 16:9 photos) ---
    land2_usable_h = 1800 - 2 * M
    land2_frame_h = (land2_usable_h - S) // 2
    land2_frame_w = int(land2_frame_h * 9 / 16)
    land2_x = M + (STRIP_W - 2 * M - land2_frame_w) // 2
    presets.append(Template(
        name="2 foto's liggend",
        background_path="",
        frames=[
            PhotoFrame(x=land2_x, y=M, width=land2_frame_w, height=land2_frame_h, rotation=90.0),
            PhotoFrame(x=land2_x, y=M + land2_frame_h + S, width=land2_frame_w, height=land2_frame_h, rotation=90.0),
        ],
        is_double_strip=False,
        cut_default=True,
    ))

    # ===== ENKELE STRIPS (niet snijden) =====

    # --- 5. 1 grote foto (rotation=90, 9:16 frame for rotated 16:9 photo) ---
    grote_frame_h = 1800 - 2 * M   # 1740
    grote_frame_w = int(grote_frame_h * 9 / 16)  # 978
    grote_x = (FULL_W - grote_frame_w) // 2  # center horizontally
    presets.append(Template(
        name="1 grote foto",
        background_path="",
        frames=[PhotoFrame(x=grote_x, y=M, width=grote_frame_w, height=grote_frame_h, rotation=90.0)],
        is_double_strip=True,
        cut_default=False,
    ))

    # --- 6. 2x2 liggend (rotation=90, 9:16 frames for rotated 16:9 photos) ---
    grid_usable_w = FULL_W - 2 * M - S  # available width for 2 columns
    grid_usable_h = 1800 - 2 * M - S    # available height for 2 rows
    grid_frame_h = grid_usable_h // 2
    grid_frame_w = int(grid_frame_h * 9 / 16)
    # Center the grid horizontally
    grid_total_w = 2 * grid_frame_w + S
    grid_x_start = M + (FULL_W - 2 * M - grid_total_w) // 2
    grid_frames = []
    for r in range(2):
        for c in range(2):
            gx = grid_x_start + c * (grid_frame_w + S)
            gy = M + r * (grid_frame_h + S)
            grid_frames.append(PhotoFrame(x=gx, y=gy, width=grid_frame_w, height=grid_frame_h, rotation=90.0))
    presets.append(Template(
        name="2x2 liggend",
        background_path="",
        frames=grid_frames,
        is_double_strip=True,
        cut_default=False,
    ))

    # --- 7. 3 foto's onder elkaar ---
    presets.append(Template(
        name="3 foto's onder elkaar",
        background_path="",
        frames=_make_strip_frames_ar(3, FULL_W, M, S, AR),
        is_double_strip=True,
        cut_default=False,
    ))

    # --- 8. 2 enkele foto's ---
    presets.append(Template(
        name="2 enkele foto's",
        background_path="",
        frames=_make_strip_frames_ar(2, FULL_W, M, S, AR),
        is_double_strip=True,
        cut_default=False,
    ))

    # --- 9. 1 enkele foto (centered, 3:2 aspect ratio) ---
    single_w = FULL_W - 2 * M   # 1140
    single_h = int(single_w / AR)  # 760 - landscape 3:2
    single_y = (1800 - single_h) // 2
    presets.append(Template(
        name="1 enkele foto",
        background_path="",
        frames=[PhotoFrame(x=M, y=single_y, width=single_w, height=single_h)],
        is_double_strip=True,
        cut_default=False,
    ))

    # ===== TRIPLE STRIPS (DNP 2-inch cut, 5x10 cm portrait per strip) =====
    # Design canvas: 600x1200 portrait. Print: 3x gestapeld (90° gedraaid)
    # op 1200x1800. DNP-driver snijdt elke 2" = 3 fysieke strips van 5x10 cm.
    TRIPLE_W = 600    # portrait canvas width (= 2" = 5 cm @ 300 DPI)
    TRIPLE_H = 1200   # portrait canvas height (= 4" = 10 cm)

    # --- Triple strip: 2 foto's onder elkaar (vierkant) ---
    presets.append(Template(
        name="DNP strip — 2 foto's",
        background_path="",
        frames=_make_triple_frames(2, TRIPLE_W, TRIPLE_H, aspect=_triple_aspect_for(2)),
        is_double_strip=False,
        cut_default=True,
        is_triple_strip=True,
    ))

    # --- Triple strip: 3 foto's onder elkaar (3:2) ---
    presets.append(Template(
        name="DNP strip — 3 foto's",
        background_path="",
        frames=_make_triple_frames(3, TRIPLE_W, TRIPLE_H, aspect=_triple_aspect_for(3)),
        is_double_strip=False,
        cut_default=True,
        is_triple_strip=True,
    ))

    return presets


def make_linked_template(printer_mode: str, photo_count: int,
                          design_path: str, booking_id: str) -> Template:
    """Bouw een Template voor een Linked-modus event.

    printer_mode: 'canon' = dubbele strip 600x1800, 'dnp' = triple 600x1200.
    photo_count:  aantal frames verticaal in de strip.
    design_path:  lokaal pad naar het uit-cloud-gehaalde design (PNG/JPG).
    booking_id:   gebruikt in naam.

    Frames zijn standaard-posities. Operator kan ze achteraf bewerken via de
    editor; bij count-wijziging worden ze opnieuw op default gezet.
    """
    M = 30
    S = 30
    AR = 3.0 / 2.0  # Canon camera aspect

    if printer_mode == "dnp":
        # 600x1200 portrait — frames op ~75% van canvas, gelijke marges + spacing
        # aspect: 2 foto's = vierkant, 3 foto's = 3:2 (Surface Pro)
        frames = _make_triple_frames(
            photo_count, 600, 1200,
            aspect=_triple_aspect_for(photo_count),
        )
        is_triple = True
        is_double = False
    else:
        # Canon: 600x1800 single strip (gedupliceerd naar 1200 bij print)
        frames = _make_strip_frames_ar(photo_count, 600, M, S, AR, canvas_h=1800)
        is_triple = False
        is_double = False

    short_id = booking_id[:8] if booking_id else "linked"
    return Template(
        name=f"Event {short_id}",
        background_path=design_path,
        frames=frames,
        is_double_strip=is_double,
        cut_default=True,
        is_triple_strip=is_triple,
    )


def _make_triple_frames(num: int, canvas_w: int = 600, canvas_h: int = 1200,
                         aspect: float = 16.0 / 9.0) -> List[PhotoFrame]:
    """Frames voor DNP 5x10cm triple strip — top-anchored.

    Spec per fotoaantal (gebruiker-wens):
      - 3 foto's: 16:9 landscape, top-anchored met kleine top-margin,
        ruimte onderin behouden voor design/tekst.
      - 2 foto's: 3:2 (tussen 16:9 en vierkant), iets langer dan voorheen,
        ook top-anchored met meer ademruimte onderin.

    Aspect parameter wordt voor 2-foto overruled naar 3:2 ongeacht meegegeven
    waarde, voor consistente UX.
    """
    TOP_MARGIN = 50
    SIDE_MARGIN_MIN = 20
    SPACING = 30

    # Voor 2-foto: gebruikerseis 'tussen 16:9 en vierkant'. Override naar 3:2.
    effective_aspect = (3.0 / 2.0) if num == 2 else aspect

    # Bepaal frame-grootte: vul breedte maximaal binnen marges
    frame_w = canvas_w - 2 * SIDE_MARGIN_MIN
    frame_h = int(frame_w / effective_aspect)

    # Zorg dat alles in de hoogte past (top-margin + frames + spacings)
    total_block = TOP_MARGIN + num * frame_h + (num - 1) * SPACING
    if total_block > canvas_h:
        # Schaal terug op hoogte
        available_h = canvas_h - TOP_MARGIN - (num - 1) * SPACING
        frame_h = available_h // num
        frame_w = int(frame_h * effective_aspect)

    x_offset = (canvas_w - frame_w) // 2

    frames = []
    for i in range(num):
        y = TOP_MARGIN + i * (frame_h + SPACING)
        frames.append(PhotoFrame(x=x_offset, y=y, width=frame_w, height=frame_h))
    return frames


def _triple_aspect_for(num: int) -> float:
    """Photo aspect-ratio voor DNP strip: 16:9 widescreen.

    Bredere foto's vullen de strip mooier dan 4:3 (vorige). Surface Pro 7
    webcam kan 1920x1080 capturen — die aspect ratio matcht 16:9 exact, dus
    geen crop bij capture. Bij 4:3 capture wordt top/bottom iets weggesneden.
    """
    return 16.0 / 9.0


def _triple_spacing_for(num: int) -> int:
    """LEGACY — niet meer gebruikt sinds _make_triple_frames gelijke gaps berekent."""
    return 20


def list_templates(templates_dir: str, backgrounds_dir: str) -> List[Template]:
    """List all available templates.

    Loads JSON templates from templates_dir and creates legacy
    templates from background images in backgrounds_dir.
    Returns default template first, then JSON templates, then legacy backgrounds.
    """
    templates = []

    # Default template (white, 3 photos)
    templates.append(Template.default_template())

    # Single photo template (white, 1 large photo)
    templates.append(Template.single_photo_template())

    # JSON templates
    if os.path.isdir(templates_dir):
        for fname in sorted(os.listdir(templates_dir)):
            if fname.lower().endswith(".json"):
                try:
                    t = Template.load(os.path.join(templates_dir, fname))
                    templates.append(t)
                except Exception as e:
                    print(f"[TEMPLATE] Fout bij laden {fname}: {e}")

    # Legacy backgrounds (only those not already referenced by a JSON template)
    # Normalize all paths for comparison (forward vs backslash on Windows)
    json_backgrounds = {os.path.normcase(os.path.normpath(t.background_path))
                        for t in templates if t.background_path}
    if os.path.isdir(backgrounds_dir):
        import glob
        bg_files = set()
        for pattern in ("*.jpg", "*.jpeg", "*.png"):
            bg_files.update(glob.glob(os.path.join(backgrounds_dir, pattern)))
            bg_files.update(glob.glob(os.path.join(backgrounds_dir, pattern.upper())))
        # Deduplicate (Windows is case-insensitive, so normalize paths)
        seen = set()
        for bg_path in sorted(bg_files):
            norm = os.path.normcase(os.path.normpath(bg_path))
            if norm not in seen and norm not in json_backgrounds:
                seen.add(norm)
                templates.append(Template.from_legacy_background(bg_path))

    return templates
