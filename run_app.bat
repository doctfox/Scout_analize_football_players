@echo off
rem NOTE: keep this file pure ASCII with CRLF line endings.
rem Cyrillic text in a .bat breaks cmd.exe on many PCs.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Scout web app

rem ---------------------------------------------------------------------------
rem  Web app (Streamlit): dashboards, player card, similar player search.
rem  Needs a running DB (start.bat or docker compose up -d db).
rem  Opens http://localhost:8501
rem ---------------------------------------------------------------------------

rem --- settings from settings.env (DB port/password for docker compose) ---
if not exist "settings.env" if exist "settings.example.env" copy /y "settings.example.env" "settings.env" >nul
if exist "settings.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
if not defined DB_PORT set "DB_PORT=5433"

rem --- Docker Desktop: start it if it is not running ---
rem  Without Docker the app still works: it reads the data snapshot
rem  snapshot\scout.sqlite (made by make_snapshot.bat on the author's PC)
where docker >nul 2>&1 || goto no_docker
docker info >nul 2>&1 && goto docker_ok
echo [..] Docker is not running, starting Docker Desktop...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
for /l %%i in (1,1,60) do (
    timeout /t 3 /nobreak >nul
    docker info >nul 2>&1 && goto docker_ok
)
echo [!!] Docker did not start.
goto no_docker

:docker_ok
echo [ok] Docker is running
rem --- containers the app needs: only the DB (scout-db) ---
rem  "up -d" does nothing if it is already running, starts it if stopped,
rem  creates it on a new PC; --wait waits until PostgreSQL accepts connections
docker ps --filter "name=scout-db" --filter "status=running" --format "{{.Names}}" | find "scout-db" >nul
if errorlevel 1 (
    echo [..] Starting the database container...
) else (
    echo [ok] Database container is already running
)
docker compose up -d --wait db
if errorlevel 1 (
    echo [!!] Failed to start the DB container: docker compose logs db
    goto no_docker
)
echo [ok] Database is ready on localhost:%DB_PORT%
goto python

:no_docker
if exist "snapshot\scout.sqlite" (
    echo [ok] No Docker - the app will use the data snapshot snapshot\scout.sqlite
    goto python
)
echo [!!] No Docker and no data snapshot snapshot\scout.sqlite.
echo      Either install Docker Desktop: https://www.docker.com/products/docker-desktop/
echo      or get snapshot\scout.sqlite from the author ^(make_snapshot.bat^) and put it here.
pause & exit /b 1

:python
set "PY="
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo [!!] Python 3.10+ not found. Install it from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" in the installer.
    pause & exit /b 1
)

rem .venv is recreated if missing or built by another Python version
rem (otherwise numpy/pandas fail with "No module named numpy._core._multiarray_umath")
%PY% -c "import sys,subprocess;r=subprocess.run([r'.venv\Scripts\python.exe','-c','import sys;print(sys.version_info[:2])'],capture_output=True,text=True);sys.exit(r.stdout.strip()!=str(sys.version_info[:2]))" 2>nul || (
    echo [..] Creating Python virtual environment...
    if exist ".venv" rmdir /s /q ".venv"
    %PY% -m venv .venv || ( echo [!!] Failed to create .venv & pause & exit /b 1 )
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
)
echo [..] Checking Python packages...
".venv\Scripts\python.exe" -m pip install -q -r webapp\requirements.txt
if errorlevel 1 ( echo [!!] pip install failed, see the log above & pause & exit /b 1 )

rem The main page file has a Cyrillic name, so Python finds it instead of cmd
".venv\Scripts\python.exe" -c "import sys,streamlit.web.cli as c;sys.argv=['streamlit','run','webapp/\u0413\u043b\u0430\u0432\u043d\u0430\u044f.py','--server.port','8501','--browser.gatherUsageStats','false'];sys.exit(c.main())"
pause
