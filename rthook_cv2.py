"""
Runtime hook for OpenCV (cv2) in PyInstaller frozen apps.

Problem: cv2's bootstrap pops itself from sys.modules and re-imports to load
the native cv2.pyd extension. If cv2 is in PyInstaller's PKG archive
(FrozenImporter), the second import is intercepted and triggers recursion
detection, causing ImportError.

Solution: cv2 is NOT added to hiddenimports (so FrozenImporter never knows
about it). Instead, cv2 files are on disk as datas/binaries. This hook ensures
_MEIPASS/cv2/ is in sys.path so Python's standard FileFinder loads cv2
directly from disk on both the first and second import.
"""
import sys
import os

if hasattr(sys, '_MEIPASS'):
    cv2_dir = os.path.join(sys._MEIPASS, 'cv2')
    if os.path.isdir(cv2_dir):
        # Insert at position 0 so it takes priority over _MEIPASS root
        if cv2_dir in sys.path:
            sys.path.remove(cv2_dir)
        sys.path.insert(0, cv2_dir)
