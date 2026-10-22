@echo off
rem Lance l'outil SIE sur ce PC (double-clic). Premier lancement : quelques minutes d'installation.
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 ou plus recent est necessaire : https://www.python.org/downloads/
  echo Cochez "Add Python to PATH" pendant l'installation, puis relancez ce fichier.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Installation des dependances...
  python -m venv .venv || goto erreur
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt || goto erreur
)
set SIE_EXEMPLES=1
echo L'outil s'ouvre dans le navigateur. Fermez cette fenetre pour l'arreter.
.venv\Scripts\python -m streamlit run app.py --browser.gatherUsageStats false
goto :eof
:erreur
echo L'installation a echoue. Copiez le message ci-dessus et envoyez-le.
pause
