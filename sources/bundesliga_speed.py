"""Реальная максимальная скорость игроков Бундеслиги (официальный трекинг лиги).

bundesliga.com публикует рейтинг "Top speed (km/h)" за каждый сезон:
https://www.bundesliga.com/en/bundesliga/stats/players/top-speed/2024-2025
Список догружается кнопкой "Load more", поэтому страница открывается в Chrome
(seleniumbase ставится вместе с soccerdata) и кнопка нажимается, пока не исчезнет.

Результат сезона кэшируется в cache/bundesliga/top_speed_<сезон>.csv — повторный
запуск берёт кэш (флаг --refresh скачивает заново).
Пишет в player_stats: max_speed (км/ч) и max_speed_source = 'bundesliga.com'.
"""
import logging
import re
import time

import pandas as pd

import repository as repo
from config import BUNDESLIGA_CACHE_DIR, BUNDESLIGA_SPEED_URL, SEASONS
from database import SessionLocal
from sources.common import Matcher, load_squads
from utils import season_label

log = logging.getLogger(__name__)

LEAGUE = "GER-Bundesliga"
SOURCE = "bundesliga.com"
_NUM = re.compile(r"^\d{2}(?:[.,]\d{1,2})?$")

# все ссылки на игроков в списке: [href, видимый текст]
_JS_ENTRIES = """
return Array.from(document.querySelectorAll('a[href*="/player/"]'))
            .map(a => [a.getAttribute('href') || '', a.innerText || '']);
"""


def season_slug(start_year: int) -> str:
    """2024 -> '2024-2025' (формат адреса bundesliga.com)."""
    return f"{start_year}-{start_year + 1}"


_HEADER = re.compile(r"top\s*speed|km/h", re.I)
_RANK = re.compile(r"^\d+\s*")


def _clean_name(v) -> str | None:
    """'2UCHENNA OGUNDU' -> 'Uchenna Ogundu' (номер места и заголовок колонки - мусор вёрстки)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    v = _RANK.sub("", str(v)).strip()
    if not v or _HEADER.search(v):
        return None
    return v.title() if v.isupper() else v


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """Чинит таблицу из новой вёрстки bundesliga.com (2026): клуба в списке больше нет,
    а к имени приклеен номер места; у первой строки имя попадает в колонку club,
    а в player - заголовок 'TOP SPEED (KM/H)'. Работает и для уже сохранённого кэша."""
    df = df.copy()
    player = df["player"].map(_clean_name)
    club = df["club"].map(_clean_name) if "club" in df else pd.Series(None, index=df.index)
    looks_like_name = player.isna() & club.notna()          # первая строка: имя уехало в club
    player = player.where(~looks_like_name, club)
    club = club.where(~looks_like_name, None)
    # клубом считаем только то, что не совпадает с именем игрока
    df["player"] = player.fillna(df["slug"].str.replace("-", " ").str.title())
    df["club"] = club.where(club != df["player"], None)
    return df


def parse_entries(entries: list[list[str]]) -> pd.DataFrame:
    """[[href, текст], ...] -> DataFrame(player, club, top_speed, slug).

    Текст пункта списка: имя (часто дважды — для мобильной и обычной вёрстки),
    клуб и значение, например 'Loïs Openda\\nLoïs Openda\\nRB Leipzig\\n36.86'.
    Ссылки меню/новостей отсеиваются: у них нет числа 20–45 км/ч в конце.
    """
    rows, seen = [], set()
    for href, txt in entries:
        lines = [ln.strip() for ln in str(txt).replace("\r", "").split("\n") if ln.strip()]
        if len(lines) < 2 or not _NUM.match(lines[-1]):
            continue
        speed = float(lines[-1].replace(",", "."))
        if not 20 <= speed <= 45:
            continue
        slug = href.rstrip("/").split("/")[-1].split("#")[0]
        if slug in seen:
            continue
        seen.add(slug)
        body = lines[:-1]
        club = body[-1] if len(body) >= 2 else None
        names = body[:-1] if club else body
        with_space = [n for n in names if " " in n]
        if with_space:
            name = max(with_space, key=len)
        elif len(names) >= 2 and names[0] != names[1]:
            name = " ".join(names[:2])          # имя и фамилия отдельными строками
        elif names:
            name = names[0]
        else:
            name = slug.replace("-", " ")
        rows.append({"player": name, "club": club, "top_speed": speed, "slug": slug})
    return clean_table(pd.DataFrame(rows, columns=["player", "club", "top_speed", "slug"]))


def _accept_cookies(driver):
    for sel in ("#onetrust-accept-btn-handler", "button[id*='accept']"):
        try:
            btns = driver.find_elements("css selector", sel)
            if btns:
                driver.execute_script("arguments[0].click();", btns[0])
                time.sleep(1)
                return
        except Exception:  # noqa: BLE001
            pass


def scrape_season(driver, start_year: int, max_clicks: int = 150) -> pd.DataFrame:
    url = BUNDESLIGA_SPEED_URL.format(season=season_slug(start_year))
    log.info("Открываю %s", url)
    driver.get(url)
    time.sleep(4)
    _accept_cookies(driver)

    xpath = ("//button[contains(translate(normalize-space(.), 'LOADMRE', 'loadmre'), 'load more')"
             " or contains(normalize-space(.), 'Mehr laden')]")
    prev, stalls = 0, 0
    for _ in range(max_clicks):
        n = len(driver.execute_script(_JS_ENTRIES))
        stalls = stalls + 1 if n == prev else 0
        prev = n
        if stalls >= 3:
            break
        btns = [b for b in driver.find_elements("xpath", xpath) if b.is_displayed()]
        if not btns:
            break
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btns[0])
        driver.execute_script("arguments[0].click();", btns[0])
        time.sleep(1.5)
    df = parse_entries(driver.execute_script(_JS_ENTRIES))
    log.info("Бундеслига %s: %d игроков", season_label(start_year), len(df))
    return df


def load_speeds(seasons, refresh: bool = False, headless: bool = False) -> dict[int, pd.DataFrame]:
    BUNDESLIGA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for y in seasons:
        f = BUNDESLIGA_CACHE_DIR / f"top_speed_{season_slug(y)}.csv"
        if f.exists() and not refresh:
            out[y] = clean_table(pd.read_csv(f))
        else:
            todo.append(y)
    if todo:
        from seleniumbase import Driver
        driver = Driver(uc=True, headless=headless)
        try:
            for y in todo:
                try:
                    df = scrape_season(driver, y)
                except Exception as e:  # noqa: BLE001
                    log.error("Бундеслига %s: не удалось скачать (%s)", season_slug(y), e)
                    continue
                if df.empty:
                    log.warning("Бундеслига %s: список пуст — возможно, изменилась вёрстка", season_slug(y))
                    continue
                df.to_csv(BUNDESLIGA_CACHE_DIR / f"top_speed_{season_slug(y)}.csv", index=False)
                out[y] = df
        finally:
            driver.quit()
    return out


def run(seasons=SEASONS, refresh: bool = False, headless: bool = False):
    speeds = load_speeds(seasons, refresh=refresh, headless=headless)
    if not speeds:
        log.warning("Скорости Бундеслиги не получены")
        return
    with SessionLocal() as s:
        squads = load_squads(s)
        if squads.empty:
            log.error("Составов нет — сначала запустите шаг transfermarkt")
            return
        matcher = Matcher(squads, None)  # у bundesliga.com нет своих id — поиск по имени и клубу
        total = 0
        for y, df in speeds.items():
            season = season_label(y)
            tmap = matcher.team_map(LEAGUE, season, df["club"].dropna().unique().tolist())
            sq = squads[(squads.league == LEAGUE) & (squads.season == season)]
            team_of = dict(zip(sq.player_id, sq.team_id))
            rows = []
            for r in df.itertuples():
                team_id = tmap.get(r.club)
                pid = matcher.player(LEAGUE, season, team_id, r.player)
                if pid is None:
                    continue
                team_id = team_id if team_id in set(sq[sq.player_id == pid].team_id) else team_of.get(pid)
                if team_id is None:
                    continue
                rows.append({"player_id": int(pid), "team_id": int(team_id), "league": LEAGUE,
                             "season": season, "max_speed": float(r.top_speed),
                             "max_speed_source": SOURCE})
            out = pd.DataFrame(rows)
            if out.empty:
                continue
            # если игрок попал в список дважды (сопоставился неоднозначно) — берём максимум
            out = out.sort_values("max_speed", ascending=False).drop_duplicates(repo.KEY)
            repo.upsert_stats(s, out, "bundesliga")
            total += len(out)
            log.info("Бундеслига %s: скорость записана для %d из %d игроков", season, len(out), len(df))
    matcher.save_report("bundesliga_speed")
    log.info("Бундеслига: всего %d строк со скоростью", total)
