@echo off
echo ============================================
echo   Bootharoo Photobooth - EXE Builder
echo ============================================
echo.

cd /d C:\Photobooth

for /f "delims=" %%v in ('python -c "import config; print(config.VERSION)"') do set VERSION=%%v
echo Versie: %VERSION%
echo.

echo [1/3] Controleren of PyInstaller geinstalleerd is...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller niet gevonden, installeren...
    pip install pyinstaller
)

echo.
echo [2/3] Building EXE met PyInstaller...
pyinstaller bootharoo.spec --noconfirm
if errorlevel 1 (
    echo FOUT: Build mislukt!
    pause
    exit /b 1
)

echo.
echo [3/3] Klaar!
echo.
echo EXE locatie: C:\Photobooth\dist\Bootharoo_%VERSION%\Bootharoo_%VERSION%.exe
echo.
pause
