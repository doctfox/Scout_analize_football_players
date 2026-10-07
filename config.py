"""Настройки ETL-парсера. Любой параметр можно переопределить переменной окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- База данных -------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:change_me@localhost:5433/project_futbol",
)
DB_SCHEMA = "football"

# --- transfermarkt-api (https://github.com/felipeall/transfermarkt-api) --------
# docker run -d -p 8000:8000 transfermarkt-api
TM_API_URL = os.getenv("TM_API_URL", "http://localhost:8000")
TM_WORKERS = int(os.getenv("TM_WORKERS", "1"))        # параллельных запросов (больше -> блокировка 403)
TM_DELAY = float(os.getenv("TM_DELAY", "3.0"))        # мин. пауза между запросами к Transfermarkt, сек
TM_MAX_BLOCK_MIN = int(os.getenv("TM_MAX_BLOCK_MIN", "90"))  # сколько терпеть 403 подряд, потом стоп
TM_TIMEOUT = int(os.getenv("TM_TIMEOUT", "60"))
TM_CACHE_DIR = BASE_DIR / "cache" / "transfermarkt"   # ответы API кэшируются на диск

# --- Tor (settings.env: USE_TOR=1) ----------------------------------------------
# Прокси для прямых запросов парсера к transfermarkt.com; пусто = без прокси.
# tm-api получает тот же прокси через HTTPS_PROXY в docker-compose.yml.
TM_PROXY = os.getenv("TM_PROXY", "").strip()
TOR_CONTROL_HOST = os.getenv("TOR_CONTROL_HOST", "tor")
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "scout")
TOR_ROTATE_PAUSE = int(os.getenv("TOR_ROTATE_PAUSE", "15"))    # пауза после смены IP при 403, сек
TOR_ROTATIONS_BEFORE_PAUSE = int(os.getenv("TOR_ROTATIONS_BEFORE_PAUSE", "10"))  # потом обычная пауза 5 мин

# --- Сезоны: год начала сезона (2021 -> 2021/22) --------------------------------
def current_season(today=None) -> int:
    """Сезон, который идёт сейчас (или вот-вот начнётся): с июля — новый."""
    import datetime as _dt
    today = today or _dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


CURRENT_SEASON = current_season()
FIRST_SEASON = int(os.getenv("FIRST_SEASON", "2021"))
# SEASONS=auto (или пусто) — все сезоны с FIRST_SEASON по текущий, новый сезон добавляется сам.
# Можно перечислить вручную: SEASONS=2023,2024
_seasons = os.getenv("SEASONS", "auto").strip().lower()
SEASONS = (list(range(FIRST_SEASON, CURRENT_SEASON + 1)) if _seasons in ("", "auto")
           else [int(s) for s in _seasons.split(",")])

# Сколько дней доверять кэшу Transfermarkt для текущего сезона (составы и стоимости меняются)
TM_CURRENT_TTL_DAYS = float(os.getenv("TM_CURRENT_TTL_DAYS", "7"))
# Откуда брать клубы/игроков/составы Transfermarkt:
#   auto    — из открытого набора transfermarkt-datasets, а чего там нет (обычно текущий сезон) —
#             через transfermarkt-api (парсит сайт);
#   dataset — только набор, сайт не запрашивается никогда (текущий сезон — когда набор обновится);
#   api     — всё через transfermarkt-api.
TM_SOURCE = os.getenv("TM_SOURCE", "auto").strip().lower() or "auto"
if TM_SOURCE not in ("auto", "dataset", "api"):
    raise SystemExit(f"TM_SOURCE={TM_SOURCE}: допустимо auto, dataset или api")

# --- Лиги ---------------------------------------------------------------------
# Каталог: ключ -> идентификаторы лиги в каждом источнике. Чтобы добавить лигу — допишите строку
# и включите её в settings.env (LEAGUES=...). Поля:
#   name      — название в веб-приложении
#   tm        — код турнира Transfermarkt (из адреса страницы: .../wettbewerb/GB1)
#   whoscored — "Страна - Турнир" как на whoscored.com (для 5 топ-лиг soccerdata знает сам)
#   understat — True / код Understat (у Understat только топ-5 и РПЛ), иначе нет xG
#   sofifa    — (страна на sofifa, ключевые слова названия) для рейтингов EA FC
#   tier      — 2 для вторых дивизионов (иначе sofifa отбрасывает «2»/«B» в названии)
#   season_start / season_end — месяцы сезона (для soccerdata: закончился ли сезон)
def _league(name, tm, whoscored=None, understat=None, sofifa=None, tier=1,
            season_start="Aug", season_end="May"):
    return {"name": name, "tm": tm, "whoscored": whoscored, "understat": understat,
            "sofifa": sofifa, "tier": tier, "season_start": season_start, "season_end": season_end}


LEAGUE_CATALOG = {
    # исходные 8 лиг
    "ENG-Premier League": _league("АПЛ", "GB1", understat=True,
                                  sofifa=("England", ["premier league"])),
    "ESP-La Liga": _league("Ла Лига", "ES1", understat=True, sofifa=("Spain", ["laliga", "la liga"])),
    "GER-Bundesliga": _league("Бундеслига", "L1", understat=True, sofifa=("Germany", ["bundesliga"])),
    "ITA-Serie A": _league("Серия А", "IT1", understat=True, sofifa=("Italy", ["serie a"])),
    "FRA-Ligue 1": _league("Лига 1", "FR1", understat=True, sofifa=("France", ["ligue 1"])),
    "RUS-Premier League": _league("РПЛ", "RU1", "Russia - Premier League", understat="RFPL",
                                  sofifa=("Russia", ["premier"]), season_start="Jul"),
    "NED-Eredivisie": _league("Эредивизи", "NL1", "Netherlands - Eredivisie",
                              sofifa=("Netherlands", ["eredivisie"])),
    "POR-Liga Portugal": _league("Лига Португалии", "PO1", "Portugal - Liga Portugal",
                                 sofifa=("Portugal", ["liga portugal", "primeira", "liga nos", "liga bwin",
                                                      "betclic"])),
    # другие высшие лиги (названия WhoScored сверены со списком турниров сайта)
    "BEL-Pro League": _league("Про-лига (Бельгия)", "BE1", "Belgium - Jupiler Pro League",
                              sofifa=("Belgium", ["pro league", "jupiler"]), season_start="Jul", season_end="Jun"),
    "TUR-Super Lig": _league("Суперлига (Турция)", "TR1", "Turkey - Super Lig",
                             sofifa=("Turkey", ["süper lig", "super lig", "trendyol"])),
    "SCO-Premiership": _league("Премьершип (Шотландия)", "SC1", "Scotland - Premiership",
                               sofifa=("Scotland", ["premiership"]), season_start="Jul"),
    "SUI-Super League": _league("Суперлига (Швейцария)", "C1", "Switzerland - Super League",
                                sofifa=("Switzerland", ["super league"]), season_start="Jul"),
    "AUT-Bundesliga": _league("Бундеслига (Австрия)", "A1", "Austria - Bundesliga",
                              sofifa=("Austria", ["bundesliga"]), season_start="Jul"),
    "GRE-Super League": _league("Суперлига (Греция)", "GR1", "Greece - Super League",
                                sofifa=("Greece", ["super league"])),
    "DEN-Superliga": _league("Суперлига (Дания)", "DK1", "Denmark - Superliga",
                             sofifa=("Denmark", ["superliga"]), season_start="Jul"),
    "POL-Ekstraklasa": _league("Экстракласа (Польша)", "PL1", "Poland - Ekstraklasa",
                               sofifa=("Poland", ["ekstraklasa"]), season_start="Jul"),
    "UKR-Premier League": _league("УПЛ (Украина)", "UKR1", "Ukraine - Premier League"),
    "CZE-First League": _league("Первая лига (Чехия)", "TS1", "Czech Republic - Gambrinus League",
                                season_start="Jul"),
    "CRO-HNL": _league("HNL (Хорватия)", "KR1", "Croatia - Prva HNL", season_start="Jul"),
    "SRB-Super Liga": _league("Суперлига (Сербия)", "SER1", "Serbia - Super Liga",
                              season_start="Jul"),
    "SAU-Pro League": _league("Про-лига (Саудовская Аравия)", "SA1", "Saudi Arabia - Pro League",
                              sofifa=("Saudi Arabia", ["pro league", "roshn"])),
    # вторые дивизионы
    "ENG-Championship": _league("Чемпионшип", "GB2", "England - Championship",
                                sofifa=("England", ["championship"]), tier=2),
    "GER-2. Bundesliga": _league("Вторая Бундеслига", "L2", "Germany - 2. Bundesliga",
                                 sofifa=("Germany", ["2. bundesliga"]), tier=2),
    "ESP-Segunda Division": _league("Сегунда", "ES2", "Spain - Segunda División",
                                    sofifa=("Spain", ["hypermotion", "segunda"]), tier=2),
    "ITA-Serie B": _league("Серия B", "IT2", "Italy - Serie B", sofifa=("Italy", ["serie b"]), tier=2),
    "FRA-Ligue 2": _league("Лига 2", "FR2", "France - Ligue 2", sofifa=("France", ["ligue 2"]), tier=2),
}

# Какие лиги собирать: LEAGUES в settings.env через запятую (ключи из каталога выше)
DEFAULT_LEAGUES = ("ENG-Premier League,ESP-La Liga,GER-Bundesliga,ITA-Serie A,FRA-Ligue 1,"
                   "RUS-Premier League,NED-Eredivisie,POR-Liga Portugal")
_active = [s.strip() for s in (os.getenv("LEAGUES") or DEFAULT_LEAGUES).split(",") if s.strip()]
_unknown = [k for k in _active if k not in LEAGUE_CATALOG]
if _unknown:
    raise SystemExit(f"Неизвестные лиги в LEAGUES: {_unknown}. Доступны: {', '.join(LEAGUE_CATALOG)}")
LEAGUES = {k: LEAGUE_CATALOG[k] for k in _active}
TM_COMPETITION_TO_LEAGUE = {v["tm"]: k for k, v in LEAGUE_CATALOG.items()}

# Лиги, которых нет в soccerdata "из коробки" — пишутся в league_dict.json перед импортом soccerdata
# (sources/common.py). Собирается из каталога, руками не правится.
SOCCERDATA_CUSTOM_LEAGUES = {
    key: {k: v for k, v in {
        "WhoScored": cfg["whoscored"],
        "Understat": cfg["understat"] if isinstance(cfg["understat"], str) else None,
        "season_start": cfg["season_start"],
        "season_end": cfg["season_end"],
    }.items() if v}
    for key, cfg in LEAGUE_CATALOG.items() if cfg["whoscored"]
}

# --- Скорость -----------------------------------------------------------------
# Официальная максимальная скорость (трекинг лиги) — есть только у Бундеслиги
BUNDESLIGA_SPEED_URL = "https://www.bundesliga.com/en/bundesliga/stats/players/top-speed/{season}"  # 2024-2025
BUNDESLIGA_CACHE_DIR = BASE_DIR / "cache" / "bundesliga"
# Калибровочная модель "рейтинг EA FC -> км/ч": игроки с меньшим временем в обучение не берутся
MIN_MINUTES_FOR_SPEED_MODEL = int(os.getenv("MIN_MINUTES_FOR_SPEED_MODEL", "450"))

# --- Прочее -------------------------------------------------------------------
MIN_MINUTES_FOR_VECTOR = int(os.getenv("MIN_MINUTES_FOR_VECTOR", "450"))  # 5 полных матчей
# Признаки вектора профиля (шаг vectors). Порядок = координаты вектора; длина = размерность
# колонки feature_vector (init_db сам пересоздаёт колонку, если длина изменилась).
VECTOR_FEATURES = [
    "goals_p90", "assists_p90",
    "xg_p90", "npxg_p90", "xa_p90", "xg_buildup_p90", "shots_p90",
    "key_passes_p90", "passes_p90", "forward_passes_p90", "progressive_passes_p90", "pass_completion_pct",
    "dribbles_p90", "dribble_success_pct", "tackles_p90", "interceptions_p90", "blocks_p90",
    "clearances_p90", "aerials_won_p90", "duels_won_pct", "dispossessed_p90", "miscontrols_p90",
]
VECTOR_DIM = len(VECTOR_FEATURES)
REPORTS_DIR = BASE_DIR / "reports"  # CSV с несопоставленными игроками/командами
