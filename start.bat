@echo off
rem NOTE: keep this file pure ASCII with CRLF line endings.
rem Cyrillic text in a .bat breaks cmd.exe on many PCs (lines get cut into
rem pieces like 'roka' / 'aza' and run as commands).
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Scout parser

rem ---------------------------------------------------------------------------
rem  One-click start: double click.
rem    start.bat                      - full collection (Transfermarkt, Understat,
rem                                     league strength, vectors), then optional
rem                                     Chrome steps: speed and/or WhoScored
rem    start.bat update               - refresh current season only: new matches,
rem                                     squads, values (+ WhoScored in Chrome
rem                                     if chosen), then vectors
rem    start.bat leagues              - list of leagues: enabled / available
rem    start.bat all --leagues "TUR-Super Lig"
rem                                   - add a new league for all seasons
rem                                     (WhoScored for it: start.bat whoscored
rem                                     --leagues "TUR-Super Lig")
rem    start.bat transfermarkt --seasons 2025 --leagues "RUS-Premier League"
rem                                   - any main.py step with arguments, in Docker
rem ---------------------------------------------------------------------------

rem --- settings from settings.env (created from the template on first run) ---
if not exist "settings.env" copy /y "settings.example.env" "settings.env" >nul
if not exist "settings.env" (
    echo [!!] settings.env and settings.example.env not found in "%~dp0"
    pause & exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
if not defined DB_PORT set "DB_PORT=5433"

rem --- 1. Docker Desktop ---
where docker >nul 2>&1
if errorlevel 1 (
    echo [!!] Docker is not installed. Install Docker Desktop:
    echo      https://www.docker.com/products/docker-desktop/
    pause & exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [..] Docker is not running, starting Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
    for /l %%i in (1,1,60) do (
        timeout /t 3 /nobreak >nul
        docker info >nul 2>&1 && goto docker_ok
    )
    echo [!!] Docker did not start. Open Docker Desktop manually and run start.bat again.
    pause & exit /b 1
)
:docker_ok
echo [ok] Docker is running

rem --- 2. DB port must not be taken by another container (e.g. an old DB) ---
set "BUSY="
for /f "delims=" %%n in ('docker ps --filter "publish=%DB_PORT%" --format "{{.Names}}"') do set "BUSY=%%n"
if defined BUSY if /i not "%BUSY%"=="scout-db" (
    echo [!!] Port %DB_PORT% is used by container "%BUSY%" - probably an old DB.
    echo      Its data is kept, the container is only stopped. Or change DB_PORT in settings.env
    choice /c YN /m "Stop %BUSY%"
    if errorlevel 2 exit /b 1
    docker stop %BUSY%
)

rem --- 3. Tor (if USE_TOR=1 in settings.env) ---
set "TM_PROXY="
if "%USE_TOR%"=="1" (
    echo [..] Starting Tor and waiting for the network ^(a couple of minutes^)...
    docker compose up -d --build --wait tor
    if errorlevel 1 ( echo [!!] Tor failed to connect: docker compose logs tor & pause & exit /b 1 )
    set "TM_PROXY=http://tor:8118"
    echo [ok] Tor is running, Transfermarkt goes through it
) else (
    docker compose stop tor >nul 2>&1
)

rem --- 4. DB + transfermarkt-api (first build takes a few minutes) ---
rem  --force-recreate for tm-api so that Tor on/off is picked up
echo [..] Starting PostgreSQL and transfermarkt-api...
docker compose up -d --build db
if errorlevel 1 ( echo [!!] Failed to start the DB & pause & exit /b 1 )
docker compose up -d --build --force-recreate tm-api
if errorlevel 1 ( echo [!!] Failed to start containers & pause & exit /b 1 )
docker compose build parser
if errorlevel 1 ( echo [!!] Failed to build the parser image & pause & exit /b 1 )
echo [ok] DB: localhost:%DB_PORT%/%DB_NAME%   API: http://localhost:%TM_PORT%/docs

rem --- "single step" mode: start.bat <step> [args] ---
rem  Chrome steps (whoscored, bundesliga_speed, sofifa, speed) run on this PC, the rest in Docker
set "HOST_STEP="
for %%h in (whoscored bundesliga_speed sofifa speed) do if /i "%~1"=="%%h" set "HOST_STEP=1"
if defined HOST_STEP (
    call :prepare_host || ( pause & exit /b 1 )
    echo [..] Step %*...
    ".venv\Scripts\python.exe" main.py %*
    if /i "%~1"=="sofifa" docker compose run --rm parser speed_model
    echo [..] Recomputing vectors...
    docker compose run --rm parser vectors
    goto done
)
if /i "%~1"=="update" (
    echo [..] Current season update: transfermarkt -^> transfers -^> understat -^> strength -^> vectors
    docker compose run --rm parser %*
    if errorlevel 1 echo [!!] Update finished with an error, see the log above
    choice /c YN /t 30 /d N /m "Update WhoScored too (Chrome, new matches of the current season)"
    if errorlevel 2 goto done
    call :prepare_host || ( pause & exit /b 1 )
    ".venv\Scripts\python.exe" main.py whoscored --seasons current
    echo [..] Recomputing vectors...
    docker compose run --rm parser vectors
    goto done
)
if not "%~1"=="" (
    docker compose run --rm parser %*
    pause & exit /b
)

rem --- 5. Main collection in Docker ---
echo [..] Collecting data: transfermarkt -^> transfers -^> understat -^> strength -^> vectors
echo      First run takes long (hours). You can stop and restart - everything is cached.
docker compose run --rm parser all
if errorlevel 1 echo [!!] Collection finished with an error, see the log above

rem --- 6. Browser steps: need a real Chrome window, so they run on this PC ---
echo.
echo Browser steps with Google Chrome (do not close the window):
echo   S - speed: bundesliga.com + EA FC ratings + km/h calibration  (a few hours)
echo   W - WhoScored: selections, transfers, progressive passes...    (a few hours)
echo   A - both,  N - skip
set "HOST_STEPS=bundesliga_speed sofifa"
choice /c SWAN /t 30 /d N /m "What to run (N after 30 s)"
if errorlevel 4 goto done
if errorlevel 3 (
    set "HOST_STEPS=whoscored bundesliga_speed sofifa"
    goto host
)
if errorlevel 2 (
    set "HOST_STEPS=whoscored"
    goto host
)

:host
call :prepare_host || ( pause & exit /b 1 )
for %%s in (%HOST_STEPS%) do (
    echo [..] Step %%s...
    ".venv\Scripts\python.exe" main.py %%s
)
echo %HOST_STEPS% | find "sofifa" >nul && docker compose run --rm parser speed_model
echo [..] Recomputing vectors...
docker compose run --rm parser vectors

:done
echo.
echo [ok] Done. DBeaver connection: localhost:%DB_PORT%, database %DB_NAME%, user %DB_USER%, schema football
echo      Stop everything: stop.bat
pause
exit /b

rem --- Python on this PC for the Chrome steps ---
:prepare_host
call :find_python || exit /b 1
rem .venv is recreated if missing or built by another Python version
%PY% -c "import sys,subprocess;r=subprocess.run([r'.venv\Scripts\python.exe','-c','import sys;print(sys.version_info[:2])'],capture_output=True,text=True);sys.exit(r.stdout.strip()!=str(sys.version_info[:2]))" 2>nul || (
    echo [..] Creating Python virtual environment...
    if exist ".venv" rmdir /s /q ".venv"
    %PY% -m venv .venv || ( echo [!!] Failed to create .venv & exit /b 1 )
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
)
echo [..] Checking Python packages...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 ( echo [!!] pip install failed, see the log above & exit /b 1 )
set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@localhost:%DB_PORT%/%DB_NAME%"
set "SOCCERDATA_DIR=%~dp0data\soccerdata"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
exit /b 0

rem --- Find a working Python 3.10+ (py launcher or python on PATH) ---
:find_python
set "PY="
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo [!!] Python 3.10+ not found. Install it from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" in the installer.
    exit /b 1
)
exit /b 0
