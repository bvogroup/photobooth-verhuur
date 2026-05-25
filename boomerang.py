"""
Boomerang GIF creator for photobooth sessions.

Buffers live view frames and creates a forward-then-reverse
animated GIF (boomerang effect).
"""

import gc
import io
from collections import deque
from PIL import Image
from PyQt5.QtCore import QThread, pyqtSignal


class FrameBuffer:
    """Thread-safe circular buffer for live view JPEG frames."""

    def __init__(self, max_frames=20):
        self._buffer = deque(maxlen=max_frames)
        self._enabled = False

    def enable(self):
        self._enabled = True
        self._buffer.clear()

    def disable(self):
        self._enabled = False

    def add_frame(self, jpeg_bytes):
        """Add a JPEG frame to the buffer. Called from LiveViewThread signal."""
        if self._enabled and jpeg_bytes:
            self._buffer.append(jpeg_bytes)

    def get_frames(self):
        """Get a copy of all buffered frames."""
        return list(self._buffer)

    def clear(self):
        self._buffer.clear()


class BoomerangThread(QThread):
    """Background thread that creates a boomerang GIF from buffered frames."""

    gif_complete = pyqtSignal(str)   # path to saved GIF
    gif_failed = pyqtSignal(str)     # error message

    def __init__(self, frames, output_path, target_size=(480, 320),
                 frame_duration_ms=66):
        super().__init__()
        self._frames = frames
        self._output_path = output_path
        self._target_size = target_size
        self._frame_duration_ms = frame_duration_ms

    def run(self):
        try:
            if len(self._frames) < 3:
                self.gif_failed.emit("Te weinig frames voor boomerang")
                return

            # Convert JPEG bytes to PIL Images (use BILINEAR — lighter than LANCZOS)
            pil_frames = []
            for jpeg_bytes in self._frames:
                img = Image.open(io.BytesIO(jpeg_bytes))
                img = img.resize(self._target_size, Image.BILINEAR)
                pil_frames.append(img)

            # Free raw JPEG bytes — no longer needed
            self._frames = None

            # Create boomerang: forward + reverse (skip first and last to avoid stutter)
            boomerang = pil_frames + pil_frames[-2:0:-1]

            # Save as animated GIF
            boomerang[0].save(
                self._output_path,
                save_all=True,
                append_images=boomerang[1:],
                duration=self._frame_duration_ms,
                loop=0,
                optimize=True,
            )

            print(f"[BOOMERANG] GIF opgeslagen: {self._output_path} "
                  f"({len(boomerang)} frames)")

            # Free all PIL frames immediately (~50MB)
            del boomerang, pil_frames
            gc.collect()

            self.gif_complete.emit(self._output_path)

        except Exception as e:
            print(f"[BOOMERANG] FOUT: {e}")
            self._frames = None  # Free on error too
            gc.collect()
            self.gif_failed.emit(str(e))
