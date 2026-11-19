@echo off
rem Valence EMC Scanner app. Runs locally, no AI, no internet needed.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m emcscan app %*
