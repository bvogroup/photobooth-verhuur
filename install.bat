@echo off
echo ============================================
echo   Photobooth - Installatie
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FOUT] Python is niet geinstalleerd!
    echo Download Python van https://www.python.org/downloads/
    echo Zorg dat je "Add Python to PATH" aanvinkt bij installatie.
    pause
    exit /b 1
)

echo [OK] Python gevonden
echo.

:: Install dependencies
echo Bezig met installeren van Python packages...
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [FOUT] Kon packages niet installeren.
    pause
    exit /b 1
)
echo.
echo [OK] Packages geinstalleerd
echo.

:: Create photos directory
if not exist "%~dp0photos" mkdir "%~dp0photos"
echo [OK] Photos map aangemaakt
echo.

echo ============================================
echo   Installatie voltooid!
echo ============================================
echo.
echo Volgende stappen:
echo   1. Installeer digiCamControl: https://digicamcontrol.com/download
echo   2. Sluit Canon EOS 1200D aan via USB
echo   3. Start digiCamControl en schakel de webserver in:
echo      File ^> Settings ^> Web server ^> Enable
echo   4. Installeer HiTi P525L printer drivers
echo   5. Start de photobooth: python main.py
echo.
pause
