@echo off
REM ============================================================================
REM  JARVIS PORTABLE  --  se lance depuis la cle USB, n'ecrit rien sur le PC hote
REM ----------------------------------------------------------------------------
REM  Pose ce fichier a la RACINE du dossier JARVIS de la cle, a cote de :
REM     app\        le depot JARVIS (copie par portable\sync_to_key.ps1)
REM     data\       cree ici : toutes les donnees (home, cache, temp, modeles)
REM     runtime\    cree ici : le Python portable et, en option, ollama.exe
REM
REM  1er lancement : ~5 min (telecharge Python portable + pip install), INTERNET requis.
REM  Lancements suivants : quelques secondes.
REM
REM  Le secret du "rien sur le PC" : on redirige USERPROFILE / LOCALAPPDATA /
REM  APPDATA / TEMP / HF_HOME / OLLAMA_MODELS vers des dossiers de la cle AVANT
REM  de demarrer Python. Aucun fichier de core/ n'est modifie (contrat V2).
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "APP=%ROOT%\app"
set "DATA=%ROOT%\data"
set "RUNTIME=%ROOT%\runtime"
set "PYDIR=%RUNTIME%\python"
set "PY=%PYDIR%\python.exe"

echo [JARVIS portable] Cle         : %ROOT%
echo [JARVIS portable] Application : %APP%

if not exist "%APP%\main.py" (
  echo [JARVIS portable] ERREUR : %APP%\main.py introuvable.
  echo                   Lance d'abord portable\sync_to_key.ps1 depuis le depot pour copier JARVIS sur la cle.
  pause & exit /b 1
)

REM --- Dossiers de donnees sur la cle ----------------------------------------
for %%D in ("%DATA%" "%DATA%\home" "%DATA%\home\AppData\Local" "%DATA%\home\AppData\Roaming" ^
            "%DATA%\home\Documents" "%DATA%\tmp" "%DATA%\hf" "%DATA%\logs" ^
            "%DATA%\ollama-models" "%DATA%\ms-playwright") do if not exist %%~D mkdir %%~D

REM --- Animation de demarrage (reacteur ARC) pendant tout le boot --------------
set "STATUS=%DATA%\tmp\boot-status.txt"
echo Demarrage du boot portable de JARVIS...> "%STATUS%"
start "" powershell -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%APP%\portable\splash.ps1" -StatusFile "%STATUS%" -SettingsFile "%DATA%\portable.json"

REM Preuve zero-trace : memoriser les VRAIS dossiers de l'hote AVANT de rediriger
set "JARVIS_HOST_USERPROFILE=%USERPROFILE%"
set "JARVIS_HOST_LOCALAPPDATA=%LOCALAPPDATA%"
set "JARVIS_HOST_APPDATA=%APPDATA%"
set "JARVIS_HOST_TEMP=%TEMP%"

REM --- Redirection : tout ce que le code ecrit "chez l'utilisateur" va sur la cle
set "JARVIS_PORTABLE=1"
set "JARVIS_DATA_DIR=%DATA%"
set "USERPROFILE=%DATA%\home"
set "HOMEDRIVE=%ROOT:~0,2%"
set "HOMEPATH=%DATA:~2%\home"
set "HOME=%DATA%\home"
set "LOCALAPPDATA=%DATA%\home\AppData\Local"
set "APPDATA=%DATA%\home\AppData\Roaming"
set "TEMP=%DATA%\tmp"
set "TMP=%DATA%\tmp"
set "HF_HOME=%DATA%\hf"
set "HUGGINGFACE_HUB_CACHE=%DATA%\hf\hub"
set "OLLAMA_MODELS=%DATA%\ollama-models"
set "PLAYWRIGHT_BROWSERS_PATH=%DATA%\ms-playwright"
set "PIP_CACHE_DIR=%DATA%\tmp\pip-cache"
REM Responsivite sur cle USB : pas de .pyc ecrits sur la cle (I/O lente), UTF-8 partout
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "SETTINGS=%DATA%\portable.json"

REM --- 1. Python portable present ? sinon bootstrap --------------------------
if exist "%PY%" goto :have_python
echo [JARVIS portable] Python portable absent -- telechargement initial (une fois)...
echo Installation du moteur Python portable (une fois, internet requis)...>> "%STATUS%"
call :bootstrap_python
if errorlevel 1 (
  echo [JARVIS portable] FATAL : impossible d'installer Python portable.
  echo                   Solution manuelle : telecharge un "python-build-standalone"
  echo                   (install_only, x86_64-pc-windows-msvc) et extrais-le dans %PYDIR%
  echo                   de sorte que %PY% existe, puis relance ce fichier.
  pause & exit /b 1
)
:have_python
echo [JARVIS portable] Python      : %PY%

REM --- 2. Dependances installees ? (marqueur .deps-ok) -----------------------
if exist "%PYDIR%\.deps-ok" goto :have_deps
echo [JARVIS portable] Installation des dependances (INTERNET requis, ~5 min)...
echo Installation des dependances de JARVIS (~5 min la premiere fois)...>> "%STATUS%"
"%PY%" -m pip install --upgrade pip wheel setuptools
"%PY%" -m pip install -r "%APP%\requirements.txt"
if errorlevel 1 (
  echo [JARVIS portable] FATAL : pip install a echoue. Verifie ta connexion et relance.
  pause & exit /b 1
)
echo ok> "%PYDIR%\.deps-ok"
:have_deps

REM --- 3a. Continuite d'identite + snapshot de la preuve zero-trace -----------
"%PY%" "%APP%\portable\continuity.py" start
"%PY%" "%APP%\portable\trace_audit.py" snapshot >nul 2>&1
REM recap de reprise (conscient de la machine) affiche sur l'animation
"%PY%" "%APP%\portable\continuity.py" recap --to "%STATUS%" >nul 2>&1

REM --- 3. Ollama portable (cerveau local hors-ligne), si fourni --------------
set "OLLAMA_EXE=%RUNTIME%\ollama\ollama.exe"
if exist "%OLLAMA_EXE%" (
  tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
  if errorlevel 1 (
    echo [JARVIS portable] Demarrage d'Ollama (modeles sur la cle)...
    start "ollama-portable" /B "%OLLAMA_EXE%" serve
  )
) else (
  echo [JARVIS portable] Ollama non fourni -- JARVIS utilisera Gemini/cloud ^(cle dans app\config^).
)

REM --- 3c. Premiere config : ni cle en clair ni coffre -> assistant -----------
if not exist "%APP%\config\api_keys.json" if not exist "%APP%\config\api_keys.enc" (
  echo Premiere configuration : saisis ta cle Gemini...>> "%STATUS%"
  powershell -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%APP%\portable\setup_wizard.ps1" -ConfigDir "%APP%\config"
)

REM --- 3b. Coffre : si app\config\api_keys.enc existe, deverrouiller la session -
echo Ouverture du coffre securise...>> "%STATUS%"
"%PY%" "%APP%\portable\vault.py" unlock --dir "%APP%\config"
if errorlevel 1 (
  echo __CLOSE__>> "%STATUS%"
  echo [JARVIS portable] Coffre non deverrouille -- arret.
  pause & exit /b 1
)

REM --- 4. Lancement sous le gardien d'ejection --------------------------------
echo [JARVIS portable] Demarrage de JARVIS...
echo JARVIS est pret. Bonjour, Sir.>> "%STATUS%"
REM laisse voir le message une seconde, puis ferme l'animation (JARVIS prend la main)
ping -n 2 127.0.0.1 >nul
echo __READY__>> "%STATUS%"
"%PY%" "%APP%\portable\eject_guard.py" --anchor "%~f0" -- "%PY%" "%APP%\main.py" --local

REM --- 5. Fin de session : continuite, preuve zero-trace, re-chiffrement -------
"%PY%" "%APP%\portable\continuity.py" end >nul 2>&1
"%PY%" "%APP%\portable\trace_audit.py" verify > "%DATA%\tmp\farewell.txt" 2>nul
"%PY%" "%APP%\portable\vault.py" relock --dir "%APP%\config"

REM --- 6. Animation de retrait (montre le resultat de l'audit zero-trace) ------
echo [JARVIS portable] JARVIS s'est arrete.
powershell -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%APP%\portable\farewell.ps1" -SettingsFile "%DATA%\portable.json" -MessageFile "%DATA%\tmp\farewell.txt"
endlocal
exit /b 0


REM ============================================================================
REM  Sous-routine : telecharge un Python portable complet (astral standalone)
REM  install_only = relogeable, pip inclus, aucun admin, aucune trace hors cle.
REM ============================================================================
:bootstrap_python
set "PBS_TAG=20250106"
set "PBS_VER=3.12.8"
set "PBS_FILE=cpython-%PBS_VER%+%PBS_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz"
set "PBS_URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PBS_TAG%/%PBS_FILE%"
set "STAGED=%RUNTIME%\%PBS_FILE%"
set "ARCHIVE=%DATA%\tmp\%PBS_FILE%"

if not exist "%RUNTIME%" mkdir "%RUNTIME%"

if exist "%STAGED%" (
  echo [JARVIS portable] Archive Python pre-posee sur la cle, extraction...
  copy /Y "%STAGED%" "%ARCHIVE%" >nul
) else (
  echo [JARVIS portable] Telechargement de Python %PBS_VER%...
  powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri '%PBS_URL%' -OutFile '%ARCHIVE%' -UseBasicParsing } catch { exit 1 }"
  if errorlevel 1 exit /b 1
)

echo [JARVIS portable] Extraction...
if not exist "%DATA%\tmp\pyextract" mkdir "%DATA%\tmp\pyextract"
tar -xf "%ARCHIVE%" -C "%DATA%\tmp\pyextract"
if errorlevel 1 exit /b 1

REM  L'archive extrait un dossier "python\" ; on le deplace en %PYDIR%.
if exist "%PYDIR%" rmdir /S /Q "%PYDIR%"
move "%DATA%\tmp\pyextract\python" "%PYDIR%" >nul
if errorlevel 1 exit /b 1
del /q "%ARCHIVE%" 2>nul
rmdir /S /Q "%DATA%\tmp\pyextract" 2>nul

if not exist "%PY%" exit /b 1
echo [JARVIS portable] Python portable installe.
exit /b 0
