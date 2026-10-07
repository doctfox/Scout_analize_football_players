@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Scout web app

rem ---------------------------------------------------------------------------
rem  Веб-приложение (Streamlit): дашборды, карточка игрока, поиск похожих.
rem  Нужна запущенная база (start.bat или docker compose up -d db).
rem  Откроется http://localhost:8501
rem ---------------------------------------------------------------------------

docker compose up -d db >nul 2>&1
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1 && (py -3 -m venv .venv) || (python -m venv .venv)
)
".venv\Scripts\python.exe" -m pip install -q -r webapp\requirements.txt
".venv\Scripts\python.exe" -m streamlit run webapp\Главная.py --server.port 8501 --browser.gatherUsageStats false
pause
