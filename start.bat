@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Scout parser

rem ---------------------------------------------------------------------------
rem  Запуск одной кнопкой: двойной клик.
rem    start.bat                      - полный сбор (Transfermarkt, Understat, сила лиг, векторы),
rem                                     затем по выбору шаги с Chrome: скорость и/или WhoScored
rem    start.bat update               - обновить текущий сезон: новые матчи, составы, стоимости
rem                                     (+ WhoScored в Chrome по выбору), затем векторы
rem    start.bat leagues              - каталог лиг: какие включены, какие можно добавить
rem    start.bat all --leagues "TUR-Super Lig"
rem                                   - догрузить новую лигу за все сезоны (WhoScored для неё:
rem                                     start.bat whoscored --leagues "TUR-Super Lig")
rem    start.bat transfermarkt --seasons 2025 --leagues "RUS-Premier League"
rem                                   - любой шаг main.py с аргументами, внутри Docker
rem ---------------------------------------------------------------------------

rem --- настройки из settings.env (при первом запуске - копия шаблона) ---
if not exist "settings.env" copy /y "settings.example.env" "settings.env" >nul
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("settings.env") do set "%%a=%%b"
if not defined DB_PORT set "DB_PORT=5433"

rem --- 1. Docker Desktop ---
docker info >nul 2>&1
if errorlevel 1 (
    echo [..] Docker не запущен, запускаю Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
    for /l %%i in (1,1,60) do (
        timeout /t 3 /nobreak >nul
        docker info >nul 2>&1 && goto docker_ok
    )
    echo [!!] Docker так и не запустился. Откройте Docker Desktop вручную и запустите start.bat снова.
    pause & exit /b 1
)
:docker_ok
echo [ok] Docker работает

rem --- 2. Порт БД не занят чужим контейнером (например, старой базой) ---
set "BUSY="
for /f "delims=" %%n in ('docker ps --filter "publish=%DB_PORT%" --format "{{.Names}}"') do set "BUSY=%%n"
if defined BUSY if /i not "%BUSY%"=="scout-db" (
    echo [!!] Порт %DB_PORT% занят контейнером "%BUSY%" - это, скорее всего, старая база.
    echo      Её данные не удалятся, контейнер просто остановится. Или поменяйте DB_PORT в settings.env
    choice /c YN /m "Остановить %BUSY%"
    if errorlevel 2 exit /b 1
    docker stop %BUSY%
)

rem --- 3. Tor (если USE_TOR=1 в settings.env) ---
set "TM_PROXY="
if "%USE_TOR%"=="1" (
    echo [..] Поднимаю Tor и жду подключения к сети ^(до пары минут^)...
    docker compose up -d --build --wait tor
    if errorlevel 1 ( echo [!!] Tor не подключился: docker compose logs tor & pause & exit /b 1 )
    set "TM_PROXY=http://tor:8118"
    echo [ok] Tor работает, Transfermarkt пойдёт через него
) else (
    docker compose stop tor >nul 2>&1
)

rem --- 4. БД + transfermarkt-api (первый раз собирается несколько минут) ---
rem  --force-recreate для tm-api: чтобы подхватить включение/выключение Tor
echo [..] Поднимаю PostgreSQL и transfermarkt-api...
docker compose up -d --build db
if errorlevel 1 ( echo [!!] Не удалось поднять БД & pause & exit /b 1 )
docker compose up -d --build --force-recreate tm-api
if errorlevel 1 ( echo [!!] Не удалось поднять контейнеры & pause & exit /b 1 )
docker compose build parser
if errorlevel 1 ( echo [!!] Не удалось собрать образ парсера & pause & exit /b 1 )
echo [ok] БД: localhost:%DB_PORT%/%DB_NAME%   API: http://localhost:%TM_PORT%/docs

rem --- Режим "свой шаг": start.bat <шаг> [аргументы] ---
rem  Шаги с Chrome (whoscored, bundesliga_speed, sofifa, speed) идут на компьютере, остальные - в Docker
set "HOST_STEP="
for %%h in (whoscored bundesliga_speed sofifa speed) do if /i "%~1"=="%%h" set "HOST_STEP=1"
if defined HOST_STEP (
    call :prepare_host || ( pause & exit /b 1 )
    echo [..] Шаг %*...
    ".venv\Scripts\python.exe" main.py %*
    if /i "%~1"=="sofifa" docker compose run --rm parser speed_model
    echo [..] Пересчитываю векторы...
    docker compose run --rm parser vectors
    goto done
)
if /i "%~1"=="update" (
    echo [..] Обновление текущего сезона: transfermarkt -^> transfers -^> understat -^> strength -^> vectors
    docker compose run --rm parser %*
    if errorlevel 1 echo [!!] Обновление завершилось с ошибкой, смотрите лог выше
    choice /c YN /t 30 /d N /m "Обновить и WhoScored (Chrome, новые матчи текущего сезона)"
    if errorlevel 2 goto done
    call :prepare_host || ( pause & exit /b 1 )
    ".venv\Scripts\python.exe" main.py whoscored --seasons current
    echo [..] Пересчитываю векторы...
    docker compose run --rm parser vectors
    goto done
)
if not "%~1"=="" (
    docker compose run --rm parser %*
    pause & exit /b
)

rem --- 5. Основной сбор в Docker ---
echo [..] Сбор данных: transfermarkt -^> transfers -^> understat -^> strength -^> vectors
echo      Первый раз долго (часы). Можно прервать и запустить снова - всё кэшируется.
docker compose run --rm parser all
if errorlevel 1 echo [!!] Сбор завершился с ошибкой, смотрите лог выше

rem --- 6. Шаги с браузером: нужен настоящий Chrome с окном, поэтому идут на компьютере ---
echo.
echo Шаги с браузером Google Chrome (окно не закрывать):
echo   S - скорость: bundesliga.com + рейтинги EA FC + калибровка км/ч  (несколько часов)
echo   W - WhoScored: отборы, перехваты, прогрессивные передачи...     (несколько часов)
echo   A - оба,  N - пропустить
set "HOST_STEPS=bundesliga_speed sofifa"
choice /c SWAN /t 30 /d N /m "Что запустить (через 30 с - N)"
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
    echo [..] Шаг %%s...
    ".venv\Scripts\python.exe" main.py %%s
)
echo %HOST_STEPS% | find "sofifa" >nul && docker compose run --rm parser speed_model
echo [..] Пересчитываю векторы...
docker compose run --rm parser vectors

:done
echo.
echo [ok] Готово. Подключение в DBeaver: localhost:%DB_PORT%, база %DB_NAME%, пользователь %DB_USER%, схема football
echo      Остановить всё: stop.bat
pause
exit /b

rem --- Подготовка Python на компьютере для шагов с Chrome ---
:prepare_host
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
    echo [..] Создаю виртуальное окружение Python...
    %PY% -m venv .venv || ( echo [!!] Нужен Python 3.10+ с python.org & exit /b 1 )
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
)
echo [..] Проверяю библиотеки Python...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@localhost:%DB_PORT%/%DB_NAME%"
set "SOCCERDATA_DIR=%~dp0data\soccerdata"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
exit /b 0
