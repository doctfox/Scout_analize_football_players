@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Останавливает контейнеры. Данные БД сохраняются (том scout_pgdata).
rem Полностью удалить базу: docker compose down -v
docker compose down
echo [ok] Контейнеры остановлены, данные сохранены.
pause
