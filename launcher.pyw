"""
MyBoothBox Photobooth Launcher.

.pyw extension = no console window.
Redirects to splash_starter for instant visual feedback.
For direct python use: pythonw splash_starter.pyw
"""
import sys
import os

# Set working directory to the script/exe location
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Import and run splash starter (instant tkinter splash + app load)
from splash_starter import run
run()
