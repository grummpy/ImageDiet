@echo off
setlocal
cd /d "%~dp0"
set "IMAGEDIET_ICON=%cd%\art\icon.png"
set "IMAGEDIET_COVER=%cd%\art\cover.png"
if not exist "%IMAGEDIET_ICON%" (
  echo ImageDiet needs art\icon.png and art\cover.png.
  exit /b 1
)
if not exist "%IMAGEDIET_COVER%" (
  echo ImageDiet needs art\icon.png and art\cover.png.
  exit /b 1
)
set IMAGEDIET_HOST=127.0.0.1
if "%IMAGEDIET_PORT%"=="" set IMAGEDIET_PORT=8766
if not exist .venv (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo ImageDiet — dry-run is the default. Nothing is pushed unless you type the domain in the UI.
echo Icon: %IMAGEDIET_ICON%
echo Cover: %IMAGEDIET_COVER%
start "" "http://127.0.0.1:%IMAGEDIET_PORT%"
python -m imagediet
