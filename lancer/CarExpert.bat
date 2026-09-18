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

python -c "import carexpert" >nul 2>nul
if errorlevel 1 (
  echo   Premiere ouverture: installation en cours, comptez une minute...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e ".[photos]"
  if errorlevel 1 (
    echo.
    echo   L'installation a echoue. Envoyez le message ci-dessus pour diagnostic.
    pause
    exit /b 1
  )
)

python -m carexpert.cli serve
pause
