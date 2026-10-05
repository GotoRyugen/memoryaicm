@echo off
rem Double-clic : installe memoryaicm comme add-on Claude (Desktop / Cowork / Claude Code).
rem Le detail est copie dans install.log (a cote de ce fichier).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-claude.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (echo Installation incomplete - voir install.log) else (echo Installation terminee - redemarre Claude Desktop.)
echo.
pause
