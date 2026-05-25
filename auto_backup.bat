@echo off
:: Automatische backup elke 30 minuten
:: Start dit script 1x, het draait op de achtergrond

set BACKUP_DIR=C:\Users\User\Documents\Bootharoo Backup

:loop
cd /d C:\Photobooth

:: Git backup
git add -A >nul 2>&1
git diff --cached --quiet 2>nul
if errorlevel 1 (
    git commit -m "Auto-backup %date% %time%" >nul 2>&1
    echo [%date% %time%] Git backup gemaakt
)

:: Kopieer bestanden naar backup map
robocopy "C:\Photobooth" "%BACKUP_DIR%" *.py *.bat *.txt *.json /mir /xd .git __pycache__ node_modules /xf *.pyc *.log /njh /njs /ndl /nc /ns /np >nul 2>&1
echo [%date% %time%] Bestanden gekopieerd naar %BACKUP_DIR%

timeout /t 1800 /nobreak >nul
goto loop
