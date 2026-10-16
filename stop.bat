@echo off
rem NOTE: keep this file pure ASCII with CRLF line endings.
cd /d "%~dp0"
rem Stops the containers. DB data is kept (volume scout_pgdata).
rem To wipe the DB completely: docker compose down -v
docker compose down
echo [ok] Containers stopped, data kept.
pause
