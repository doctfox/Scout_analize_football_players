"""Шаг 1. Transfermarkt: команды лиг, составы по сезонам, игроки.

Источник (TM_SOURCE в settings.env):
  * auto (по умолчанию) — всё, что есть в открытом наборе transfermarkt-datasets
    (sources/tm_dataset.py), берётся оттуда без запросов к сайту; через transfermarkt-api
    догружаются только пары лига-сезон, которых в наборе ещё нет (обычно — текущий сезон);
  * dataset — только набор, сайт не запрашивается никогда;
  * api — всё через transfermarkt-api (парсит сайт, возможны блокировки 403).
Трансферы — только из набора (шаг transfers), с сайта не скачиваются.

Как устроен путь через transfermarkt-api (чтобы не ловить блокировку 403):
  * клубы лиг и составы — через transfermarkt-api;
  * данные игрока (дата рождения, позиция, нога, рост, стоимость) берутся ИЗ СОСТАВОВ —
    отдельный запрос профиля не нужен (ранее скачанные профили используются из кэша);
  * статистика с TM — только с флагом --tm-stats (её дают Understat и WhoScored);
  * один общий ограничитель скорости на все потоки, при 403/429 — общая пауза 5→10→20→30 мин;
  * с USE_TOR=1 запросы идут через Tor, и при 403/429 вместо долгой паузы меняется выходной IP
    (до TOR_ROTATIONS_BEFORE_PAUSE раз подряд, потом всё-таки пауза);
  * в БД пишется по этапам: игроки/составы сразу, трансферы — даже если сбор прервался.
Всё скачанное кэшируется: повторный запуск докачивает только недостающее.
"""
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import requests
from dateutil import parser as dateparser
from tqdm import tqdm

import repository as repo
from config import (CURRENT_SEASON, LEAGUE_CATALOG, LEAGUES, REPORTS_DIR, SEASONS, TM_API_URL, TM_CACHE_DIR,
                    TM_COMPETITION_TO_LEAGUE, TM_CURRENT_TTL_DAYS, TM_DELAY, TM_MAX_BLOCK_MIN, TM_SOURCE,
                    TM_TIMEOUT, TM_WORKERS, TOR_ROTATE_PAUSE, TOR_ROTATIONS_BEFORE_PAUSE)
from sources import tm_dataset, tor
from database import SessionLocal
from utils import age_at_season, safe_div, season_from_tm, season_label, to_millions

log = logging.getLogger(__name__)

CHALLENGE_LIMIT = 5  # после стольких 202 подряд шаг останавливается


class TransfermarktBlocked(RuntimeError):
    """Transfermarkt долго отвечает 403/429 — IP заблокирован, продолжать бессмысленно."""


class Throttle:
    """Общий для всех потоков ограничитель: пауза между запросами + общая пауза при блокировке."""

    def __init__(self, delay: float = TM_DELAY, max_block_min: int = TM_MAX_BLOCK_MIN, on_rotate=None):
        self.delay = delay
        self.max_block = max_block_min * 60
        self.on_rotate = on_rotate      # вызывается после смены IP (закрыть старые соединения)
        self._rotations = 0             # смен IP подряд без единого успешного ответа
        self._lock = threading.Lock()
        self._next = 0.0
        self._cooldown_until = 0.0
        self._cooldown = 0.0
        self._blocked_since = None

    def wait(self):
        while True:
            with self._lock:
                now = time.monotonic()
                start = max(self._next, self._cooldown_until)
                if now >= start:
                    self._next = now + self.delay
                    return
                pause = start - now
            time.sleep(min(pause, 5))

    def blocked(self, what: str, status: int):
        with self._lock:
            now = time.monotonic()
            if self._blocked_since is None:
                self._blocked_since = now
            if now - self._blocked_since > self.max_block:
                raise TransfermarktBlocked(
                    f"Transfermarkt отвечает {status} уже больше {self.max_block // 60} мин — IP заблокирован. "
                    "Подождите несколько часов (или смените сеть/VPN) и запустите шаг снова: "
                    "всё скачанное сохранено в кэше.")
            if now < self._cooldown_until:  # паузу/смену IP уже назначил другой поток
                return
            rotate = tor.enabled() and self._rotations < TOR_ROTATIONS_BEFORE_PAUSE
            if rotate:
                self._rotations += 1
                self._cooldown_until = now + TOR_ROTATE_PAUSE
                log.warning("TM HTTP %s (%s) — меняю IP через Tor (%d/%d)",
                            status, what, self._rotations, TOR_ROTATIONS_BEFORE_PAUSE)
            else:
                self._rotations = 0
                self._cooldown = min(max(self._cooldown * 2, 300), 1800)
                self._cooldown_until = now + self._cooldown
                log.warning("TM HTTP %s (%s) — пауза всех запросов на %d мин",
                            status, what, self._cooldown // 60)
        if rotate and tor.new_identity() and self.on_rotate:  # вне блокировки: NEWNYM ждёт до 10 с
            self.on_rotate()

    def ok(self):
        with self._lock:
            self._blocked_since = None
            self._cooldown = 0.0
            self._rotations = 0


class TransfermarktClient:
    def __init__(self, base_url: str = TM_API_URL, cache_dir=TM_CACHE_DIR, use_cache: bool = True):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api = requests.Session()
        self.api.trust_env = False          # до tm-api ходим напрямую, без прокси из окружения
        self.throttle = Throttle()
        self.failed: list[str] = []
        self._challenges = 0  # ответов 202 (проверка на бота) подряд

    # ------------------------------------------------------------------ кэш
    def _cache_path(self, path: str, params: dict | None):
        key = path + json.dumps(params or {}, sort_keys=True)
        return self.cache_dir / (hashlib.md5(key.encode()).hexdigest() + ".json")

    def cached(self, path: str, params: dict | None = None, max_age_days: float | None = None):
        """Ответ из кэша; max_age_days — для данных, которые ещё меняются (текущий сезон)."""
        f = self._cache_path(path, params)
        if not f.exists():
            return None
        if max_age_days is not None and time.time() - f.stat().st_mtime > max_age_days * 86400:
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    # ------------------------------------------------------- общий запрос
    def _fetch(self, session, url, params, what, retries: int = 8):
        for attempt in range(retries):
            self.throttle.wait()
            try:
                r = session.get(url, params=params, timeout=TM_TIMEOUT)
            except requests.RequestException as e:
                log.warning("TM %s: %s (попытка %d)", what, e, attempt + 1)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:  # вместо JSON пришла страница-заглушка антибота
                    self.throttle.blocked(what, 403)
                    continue
                self.throttle.ok()
                self._challenges = 0
                return data
            if r.status_code == 404:
                return None
            if r.status_code == 202 and not r.content.strip():
                # пустой 202 = защита сайта требует пройти JS-проверку в браузере; повторы не помогут
                self._challenges += 1
                log.warning("TM %s -> HTTP 202: сайт требует проверку браузера", what)
                if self._challenges >= CHALLENGE_LIMIT:
                    raise TransfermarktBlocked(
                        f"Transfermarkt {CHALLENGE_LIMIT} раз подряд ответил 202 (проверка на бота через "
                        "JavaScript) — простыми запросами эти данные сейчас не получить. "
                        "Трансферы: шаг transfers (открытый набор transfermarkt-datasets).")
                break
            if r.status_code in (403, 429):
                self.throttle.blocked(what, r.status_code)
                continue
            # 5xx: transfermarkt-api не смог разобрать страницу — долго не повторяем
            log.warning("TM %s -> HTTP %s (попытка %d)", what, r.status_code, attempt + 1)
            if attempt >= 2:
                break
            time.sleep(3 * 2 ** attempt)
        log.error("TM %s: не удалось получить данные", what)
        self.failed.append(what)
        return None

    def get(self, path: str, params: dict | None = None, max_age_days: float | None = None) -> dict | None:
        """Запрос к transfermarkt-api с кэшем."""
        if self.use_cache:
            hit = self.cached(path, params, max_age_days)
            if hit is not None:
                return hit
        data = self._fetch(self.api, f"{self.base_url}{path}", params, path)
        if data is not None:
            self._cache_path(path, params).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # --- эндпоинты transfermarkt-api ---
    @staticmethod
    def _ttl(season: int) -> float | None:
        # прошедшие сезоны не меняются; текущий (составы, стоимости) — перекачиваем раз в TTL
        return TM_CURRENT_TTL_DAYS if season >= CURRENT_SEASON else None

    def competition_clubs(self, comp_id: str, season: int):
        return self.get(f"/competitions/{comp_id}/clubs", {"season_id": season}, self._ttl(season))

    def club_players(self, club_id: str, season: int):
        return self.get(f"/clubs/{club_id}/players", {"season_id": season}, self._ttl(season))

    def player_stats(self, pid: str):
        return self.get(f"/players/{pid}/stats")

    def cached_profile(self, pid: str):
        """Профиль только из кэша — новых запросов не делаем."""
        return self.cached(f"/players/{pid}/profile")



# ------------------------------------------------------------------ утилиты
def _date(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        try:
            return dateparser.parse(str(v)).date()
        except (ValueError, OverflowError):
            return None


def _parallel(fn, items, desc):
    """Параллельно, с прогрессом. При долгой блокировке возвращает то, что успели, и ошибку."""
    out, blocked = {}, None
    with ThreadPoolExecutor(max_workers=TM_WORKERS) as ex:
        futures = {ex.submit(fn, it): it for it in items}
        for f in tqdm(as_completed(futures), total=len(futures), desc=desc):
            if f.cancelled():
                continue
            try:
                out[futures[f]] = f.result()
            except TransfermarktBlocked as e:
                if blocked is None:
                    blocked = e
                    for other in futures:
                        other.cancel()
    return out, blocked


def _report_failed(tm):
    if tm.failed:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"request": tm.failed}).to_csv(REPORTS_DIR / "tm_failed.csv", index=False)
        log.warning("TM: %d запросов не удалось (reports/tm_failed.csv) — перезапустите шаг позже.",
                    len(tm.failed))


# ---------------------------------------------------------------------- run
def _from_api(tm, pairs):
    """Клубы, игроки и составы через transfermarkt-api (парсит сайт) — для пар, которых нет в наборе."""
    league_clubs = []  # (league, season_year, club_id, club_name)
    for league, season in sorted(pairs):
        data = tm.competition_clubs(LEAGUE_CATALOG[league]["tm"], season)
        if not data:
            log.error("Нет клубов для %s %s", league, season)
            continue
        for c in data.get("clubs", []):
            league_clubs.append((league, season, c["id"], c["name"]))
    log.info("tm-api: клубов-сезонов %d", len(league_clubs))

    # составы (в них уже есть всё про игрока)
    squads_raw, blocked = _parallel(lambda x: tm.club_players(x[2], x[1]), league_clubs, "Составы")
    squad_rows, latest = [], {}   # latest: tm_player_id -> (сезон, клуб, данные из состава)
    for (league, season, club_id, _), data in squads_raw.items():
        for p in (data or {}).get("players", []):
            squad_rows.append({"tm_club_id": club_id, "tm_player_id": p["id"], "league": league,
                               "season": season_label(season),
                               "market_value": to_millions(p.get("marketValue"))})
            if p["id"] not in latest or season > latest[p["id"]][0]:
                latest[p["id"]] = (season, club_id, p)

    teams = {}
    for league, season, club_id, name in sorted(league_clubs, key=lambda x: x[1]):
        teams[club_id] = {"team_name": name, "league": league}   # последний сезон задаёт лигу

    players = []
    for pid, (_, club_id, p) in latest.items():   # из состава последнего сезона (+ профиль из кэша)
        prof = tm.cached_profile(pid) or {}
        nat = prof.get("citizenship") or p.get("nationality") or []
        if isinstance(nat, str):
            nat = [nat]
        players.append({
            "tm_player_id": pid,
            "player_name": prof.get("name") or p.get("name"),
            "birth_date": _date(prof.get("dateOfBirth") or p.get("dateOfBirth")),
            "position": (prof.get("position") or {}).get("main") or p.get("position"),
            "nationality": nat[0] if nat else None,
            "foot": prof.get("foot") or p.get("foot"),
            "height_cm": prof.get("height") or p.get("height"),
            "current_market_value": to_millions(prof.get("marketValue") or p.get("marketValue")),
            "tm_current_club_id": (prof.get("club") or {}).get("id") or club_id,
        })
    return teams, players, squad_rows, blocked


def run(seasons=SEASONS, leagues=None, with_stats: bool = False, refresh: bool = False, wait_api=None):
    leagues = leagues or list(LEAGUES)
    pairs = {(lg, y) for lg in leagues for y in seasons}

    # 1. Что есть в открытом наборе — берём оттуда (без запросов к сайту)
    in_dataset = set()
    if TM_SOURCE in ("auto", "dataset"):
        in_dataset = tm_dataset.coverage(leagues, seasons, refresh)
    missing = pairs - in_dataset
    log.info("Transfermarkt (TM_SOURCE=%s): из набора %d пар лига-сезон, нет в наборе %d%s", TM_SOURCE,
             len(in_dataset), len(missing),
             f": {', '.join(f'{lg} {season_label(y)}' for lg, y in sorted(missing))}" if missing else "")
    ds_teams, players, squad_rows = tm_dataset.load(in_dataset, refresh)
    api_teams = {}

    # 2. Остальное — через transfermarkt-api (TM_SOURCE=dataset: пропускаем, сайт не трогаем)
    tm, blocked = None, None
    if missing and TM_SOURCE == "dataset":
        log.warning("TM_SOURCE=dataset: этих пар в наборе пока нет, пропускаю — появятся после "
                    "обновления набора")
    elif missing:
        if wait_api:
            wait_api()
        tor.check()
        tm = TransfermarktClient()
        api_teams, api_players, api_squads, blocked = _from_api(tm, missing)
        squad_rows += api_squads
        have = {p["tm_player_id"] for p in api_players}
        players = [p for p in players if p["tm_player_id"] not in have] + api_players  # свежее — с сайта

    with SessionLocal() as s:
        repo.ensure_teams(s, ds_teams, rename=False)   # названия известных клубов не трогаем
        team_ids = repo.ensure_teams(s, api_teams)
        latest_club = {}
        for r in sorted(squad_rows, key=lambda r: r["season"]):
            latest_club[r["tm_player_id"]] = r["tm_club_id"]
        for p in players:
            club = p.pop("tm_current_club_id", None)
            p["team_id"] = team_ids.get(club) or team_ids.get(latest_club.get(p["tm_player_id"]))
        repo.upsert_players(s, players)
        pmap = repo.player_id_map(s)
        rows = [{
            "team_id": team_ids[r["tm_club_id"]], "player_id": pmap[r["tm_player_id"]],
            "league": r["league"], "season": r["season"], "market_value": r["market_value"],
        } for r in squad_rows if r["tm_club_id"] in team_ids and r["tm_player_id"] in pmap]
        repo.upsert_squads(s, list({(r["team_id"], r["player_id"], r["season"]): r for r in rows}.values()))
        log.info("Записано: игроков %d, строк составов %d", len(players), len(rows))

        # 3. Статистика с TM (по желанию; через transfermarkt-api)
        if with_stats and blocked is None:
            if tm is None and wait_api:
                wait_api()
            tm = tm or TransfermarktClient()
            birth = {p["tm_player_id"]: p["birth_date"] for p in players}
            stats, blocked = _parallel(tm.player_stats, sorted(birth), "Статистика TM")
            _write_stats(s, stats, pmap, team_ids, birth, seasons)

    if tm is not None:
        _report_failed(tm)
    if blocked is not None:
        raise SystemExit(str(blocked))


def _write_stats(s, stats, pmap, team_ids, birth, seasons):
    wanted = {season_label(y) for y in seasons}
    rows = []
    for pid, data in stats.items():
        for st in (data or {}).get("stats", []):
            league = TM_COMPETITION_TO_LEAGUE.get(st.get("competitionId"))
            season = season_from_tm(st.get("seasonId"))
            team_id = team_ids.get(st.get("clubId"))
            if not league or season not in wanted or not team_id:
                continue
            apps = st.get("appearances") or 0
            minutes = st.get("minutesPlayed")
            if minutes is not None and apps and minutes < apps:  # ошибка разбора "2.345'" -> 2
                minutes = None
            rows.append({
                "player_id": pmap[pid], "team_id": team_id, "league": league, "season": season,
                "age_at_season": age_at_season(birth.get(pid), season),
                "appearances": apps, "minutes_played": minutes,
                "goals": st.get("goals") or 0, "assists": st.get("assists") or 0,
                "yellow_cards": st.get("yellowCards") or 0, "red_cards": st.get("redCards") or 0,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return
    df = df.groupby(repo.KEY, as_index=False).agg({
        "age_at_season": "first", "appearances": "sum",
        "minutes_played": lambda x: x.sum(min_count=1),
        "goals": "sum", "assists": "sum", "yellow_cards": "sum", "red_cards": "sum",
    })
    df = df[df["appearances"] > 0]
    df["goals_per_match"] = safe_div(df["goals"], df["appearances"])
    df["assists_per_match"] = safe_div(df["assists"], df["appearances"])
    repo.upsert_stats(s, df, "transfermarkt")
    log.info("Строк статистики TM: %d", len(df))
