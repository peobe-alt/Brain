@echo off
REM Double-cliquez sur ce fichier pour ouvrir CarExpert. (Windows)
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python n'est pas installe sur cet ordinateur.
  echo   Telechargez-le sur https://www.python.org/downloads/ puis reessayez.
  echo   Pensez a cocher "Add Python to PATH" pendant l'installation.
  echo.
  pause
  exit /b 1
)

REM Tout s'installe dans un dossier .venv a cote du programme, jamais dans le
REM Python du systeme: rien a desinstaller, et aucun conflit avec le reste.
if not exist ".venv\Scripts\python.exe" (
  echo   Premiere ouverture: installation en cours, comptez une minute...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo   La creation de l'environnement a echoue.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import carexpert" >nul 2>nul
if errorlevel 1 (
  echo   Installation des composants, comptez une minute...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -e ".[photos]"
  if errorlevel 1 (
    echo.
    echo   L'installation a echoue. Envoyez le message ci-dessus pour diagnostic.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m carexpert.cli serve
pause
