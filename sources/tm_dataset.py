"""Данные Transfermarkt из открытого набора transfermarkt-datasets — без запросов к сайту.

Источник: https://github.com/dcaribou/transfermarkt-datasets (CC0). Автор набора сам собирает
Transfermarkt и выкладывает CSV; id игроков и клубов — родные id Transfermarkt, поэтому они
совпадают с tm_player_id / tm_club_id в нашей БД.

Отсюда берутся клубы, игроки (дата рождения, позиция, нога, рост, гражданство, стоимость),
составы по сезонам (кто попадал в заявку на матчи лиги), стоимость на конец сезона и трансферы
(шаг transfers). Файлы перекачиваются, только когда на сервере появилась новая версия.
"""
import logging
import time
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from config import BASE_DIR, LEAGUE_CATALOG
from utils import season_label, to_millions

log = logging.getLogger(__name__)

BASE_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
CACHE_DIR = BASE_DIR / "cache" / "transfermarkt-datasets"
CHECK_EVERY_HOURS = 12   # чаще не спрашиваем сервер, есть ли новая версия
_checked: set[str] = set()


def fetch(name: str, refresh: bool = False):
    """Путь к локальной копии <name>.csv.gz; скачивает, если на сервере версия новее."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"{name}.csv.gz"
    url = f"{BASE_URL}/{name}.csv.gz"
    if name in _checked:
        return f
    recent = f.exists() and time.time() - f.stat().st_mtime < CHECK_EVERY_HOURS * 3600
    if recent and not refresh:
        _checked.add(name)
        return f
    try:
        head = requests.head(url, timeout=30)
        head.raise_for_status()
        remote = parsedate_to_datetime(head.headers["Last-Modified"]).timestamp()
        if f.exists() and f.stat().st_mtime >= remote:
            log.info("%s: актуальная версия уже в кэше", f.name)
            f.touch()   # отметка «проверено» — следующая проверка через CHECK_EVERY_HOURS
        else:
            log.info("Скачиваю %s ...", url)
            tmp = f.with_suffix(".part")
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp, "wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
            tmp.replace(f)
            log.info("%s: %.1f МБ", f.name, f.stat().st_size / 1e6)
    except (requests.RequestException, KeyError, ValueError) as e:
        if not f.exists():
            raise SystemExit(f"Не удалось скачать {url}: {e}")
        log.warning("Не удалось проверить обновление %s (%s), использую старую копию", f.name, e)
    _checked.add(name)
    return f


def read(name: str, refresh: bool = False, **kw) -> pd.DataFrame:
    return pd.read_csv(fetch(name, refresh), compression="gzip", **kw)


def coverage(leagues, seasons, refresh: bool = False) -> set[tuple[str, int]]:
    """Какие пары (лига, сезон) есть в наборе (по сыгранным матчам лиги)."""
    codes = {LEAGUE_CATALOG[lg]["tm"]: lg for lg in leagues}
    games = read("games", refresh, usecols=["competition_id", "season"])
    have = games[games["competition_id"].isin(codes)].drop_duplicates()
    wanted = set(seasons)
    return {(codes[c], int(s)) for c, s in zip(have["competition_id"], have["season"]) if int(s) in wanted}


def load(pairs: set[tuple[str, int]], refresh: bool = False):
    """Клубы, игроки и составы для пар (лига, сезон) из набора.

    Возвращает (teams, players, squad_rows) в том же виде, что и шаг через transfermarkt-api:
      teams:      {tm_club_id: {"team_name", "league"}}
      players:    [{"tm_player_id", "player_name", "birth_date", ..., "tm_current_club_id"}]
      squad_rows: [{"tm_club_id", "tm_player_id", "league", "season", "market_value"}]
    """
    if not pairs:
        return {}, [], []
    code_to_league = {LEAGUE_CATALOG[lg]["tm"]: lg for lg, _ in pairs}
    wanted = pd.DataFrame([(LEAGUE_CATALOG[lg]["tm"], s) for lg, s in pairs], columns=["competition_id", "season"])

    games = read("games", refresh, usecols=["game_id", "competition_id", "season"])
    games = games.merge(wanted, on=["competition_id", "season"])
    app = read("appearances", refresh, usecols=["game_id", "player_id", "player_club_id"])
    app = app.merge(games, on="game_id")
    squads = app.drop_duplicates(["competition_id", "season", "player_club_id", "player_id"]).copy()
    squads["league"] = squads["competition_id"].map(code_to_league)
    log.info("Набор: составов (игрок-клуб-сезон) %d, игроков %d", len(squads), squads["player_id"].nunique())

    # стоимость на конец сезона: последняя оценка не позже 30 июня года окончания сезона
    val = read("player_valuations", refresh, usecols=["player_id", "date", "market_value_in_eur"])
    val["date"] = pd.to_datetime(val["date"], errors="coerce")
    val = val.dropna(subset=["date"]).sort_values("date")
    squads["season_end"] = pd.to_datetime((squads["season"] + 1).astype(str) + "-06-30")
    squads = pd.merge_asof(squads.sort_values("season_end"), val, left_on="season_end", right_on="date",
                           by="player_id", direction="backward")

    clubs = read("clubs", refresh, usecols=["club_id", "name"])
    club_name = dict(zip(clubs["club_id"], clubs["name"]))
    teams = {}
    for r in squads.sort_values("season").itertuples(index=False):   # последний сезон задаёт лигу
        teams[str(r.player_club_id)] = {"team_name": club_name.get(r.player_club_id, "Unknown"), "league": r.league}

    squad_rows = [{
        "tm_club_id": str(r.player_club_id), "tm_player_id": str(r.player_id), "league": r.league,
        "season": season_label(int(r.season)), "market_value": to_millions(r.market_value_in_eur),
    } for r in squads.itertuples(index=False)]

    ids = set(squads["player_id"])
    p = read("players", refresh)
    p = p[p["player_id"].isin(ids)]
    players = [{
        "tm_player_id": str(r.player_id),
        "player_name": r.name,
        "birth_date": pd.to_datetime(r.date_of_birth, errors="coerce").date() if pd.notna(r.date_of_birth) else None,
        "position": r.sub_position if pd.notna(r.sub_position) else r.position,
        "nationality": r.country_of_citizenship,
        "foot": r.foot,
        "height_cm": int(r.height_in_cm) if pd.notna(r.height_in_cm) else None,
        "current_market_value": to_millions(r.market_value_in_eur),
        "tm_current_club_id": str(int(r.current_club_id)) if pd.notna(r.current_club_id) else None,
    } for r in p.itertuples(index=False)]
    return teams, players, squad_rows
