@echo off
rem Builds MediaForge.exe (PyInstaller)
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name MediaForge ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  MediaForge.py
echo.
echo Output: dist\MediaForge.exe
pause
