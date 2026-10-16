@echo off
rem NOTE: keep this file pure ASCII with CRLF line endings.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Scout snapshot

rem ---------------------------------------------------------------------------
rem  Saves the current DB into snapshot\scout.sqlite (one file).
rem  With this file run_app.bat works on any PC without Docker:
rem  copy the whole project folder (or push it to GitHub) together with snapshot\.
rem ---------------------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
    echo [!!] Run run_app.bat once first ^(it creates .venv^)
    pause & exit /b 1
)
if exist "settings.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
docker info >nul 2>&1
if errorlevel 1 (
    echo [!!] Docker is not running. Start Docker Desktop and run make_snapshot.bat again.
    pause & exit /b 1
)
docker compose up -d --wait db
if errorlevel 1 ( echo [!!] Failed to start the DB & pause & exit /b 1 )
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" export_snapshot.py
if errorlevel 1 ( echo [!!] Snapshot failed, see the log above & pause & exit /b 1 )
pause
