@echo off
rem Packs the contents of dist\ into a shareable zip (without settings.json -
rem it contains personal paths/preferences and must not end up in the package)
cd /d "%~dp0"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\MediaForge.exe','dist\bin' -DestinationPath 'MediaForge-portable.zip' -Force"
echo.
echo Output: MediaForge-portable.zip
pause
