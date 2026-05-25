@echo off
echo Photobooth starten...
cd /d "%~dp0"
python main.py %*
if errorlevel 1 (
    echo.
    echo [FOUT] Photobooth kon niet starten.
    echo Voer eerst install.bat uit als je dat nog niet gedaan hebt.
    pause
)
