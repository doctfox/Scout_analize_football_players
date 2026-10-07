"""Рейтинги EA FC (sofifa.com через soccerdata.SoFIFA): acceleration, sprint_speed и др.

Это экспертные оценки EA (0–99), а не замеры, поэтому они пишутся в отдельную таблицу
player_ratings, а не в max_speed. Используются как признаки и для калибровочной модели
скорости (шаг speed_model).

Сезон <-> издание: FIFA 22 -> 2021/22, FIFA 23 -> 2022/23, FC 24 -> 2023/24 ...
Из каждого издания берётся последнее обновление базы (состояние на конец сезона).
Рейтинги скачиваются только для игроков, найденных в наших составах (Transfermarkt).

Ограничения:
- sofifa.com за Cloudflare: нужен Chrome с окном (как для WhoScored); запуск долгий
  (~3–5 с на игрока), но всё кэшируется в SOCCERDATA_DIR/data/SoFIFA — можно прерывать.
- Российские клубы EA удалила из игры в 2022 г., поэтому для РПЛ данных почти не будет.
- sofifa показывает данные только после входа в аккаунт. Chrome запускается с постоянным
  профилем (data/chrome_sofifa): при первом запуске войдите в аккаунт в открывшемся окне
  и нажмите Enter в консоли — дальше вход сохраняется.
"""
import logging
import re

import pandas as pd
from tqdm import tqdm

import repository as repo
from config import BASE_DIR, LEAGUE_CATALOG, LEAGUES, SEASONS
from database import SessionLocal
from models import RATING_COLUMNS, PlayerRating
from sources.common import Matcher, import_soccerdata, load_squads
from utils import season_label

log = logging.getLogger(__name__)

# Названия лиг на sofifa меняются вместе со спонсорами ("LaLiga EA Sports", "Serie A Enilive"...),
# а в soccerdata зашиты старые. Поэтому название каждой лиги ищем в /api/league по стране
# и ключевому слову; id лиги у sofifa постоянный.
# (страна, ключевые слова, уровень) — из каталога лиг config.LEAGUE_CATALOG
CUSTOM = {k: (v["sofifa"][0], v["sofifa"][1], v["tier"]) for k, v in LEAGUE_CATALOG.items() if v["sofifa"]}


PROFILE_DIR = BASE_DIR / "data" / "chrome_sofifa"   # профиль Chrome: здесь хранится вход в sofifa
SOFIFA_URL = "https://sofifa.com"


def _logged_in(driver) -> bool:
    return 'href="/signIn"' not in driver.page_source and "/signIn" not in driver.current_url


CHECK_PLAYER_URL = SOFIFA_URL + "/player/158023/"   # любой игрок: проверяем язык страницы


def _english(driver) -> bool:
    return "Sprint speed" in driver.page_source


def _ensure_english(driver, headless: bool) -> None:
    """soccerdata разбирает страницы игроков по английским меткам ('Sprint speed'...),
    а язык sofifa хранится в настройках аккаунта — проверяем и просим переключить."""
    driver.get(CHECK_PLAYER_URL)
    driver.sleep(4)
    if _english(driver):
        return
    if headless:
        raise SystemExit("sofifa открывается не на английском: переключите язык сайта на English")
    print("\n" + "=" * 70 +
          "\n  sofifa открывается НЕ на английском — рейтинги не разберутся."
          "\n  В окне Chrome вверху справа выберите язык English (сейчас, видимо, Polski),"
          "\n  дождитесь перезагрузки страницы и нажмите Enter здесь.\n" + "=" * 70)
    while True:
        input()
        driver.get(CHECK_PLAYER_URL)
        driver.sleep(4)
        if _english(driver):
            log.info("SoFIFA: язык сайта — английский")
            return
        print("  Страница всё ещё не на английском. Переключите язык и снова нажмите Enter.")


def _ensure_login(driver, headless: bool) -> None:
    """Проверяет вход в sofifa; если не выполнен — ждёт, пока пользователь войдёт вручную."""
    _ensure_login_only(driver, headless)
    _ensure_english(driver, headless)


def _ensure_login_only(driver, headless: bool) -> None:
    driver.get(SOFIFA_URL)
    driver.sleep(5)
    if _logged_in(driver):
        log.info("SoFIFA: вход в аккаунт выполнен")
        return
    if headless:
        raise SystemExit("SoFIFA требует вход в аккаунт: запустите шаг без --headless и войдите в окне Chrome")
    driver.get(SOFIFA_URL + "/signIn")
    print("\n" + "=" * 70 +
          "\n  SoFIFA требует вход. В открывшемся окне Chrome войдите в свой аккаунт,"
          "\n  дождитесь главной страницы и нажмите Enter здесь.\n" + "=" * 70)
    while True:
        input()
        driver.get(SOFIFA_URL)
        driver.sleep(3)
        if _logged_in(driver):
            log.info("SoFIFA: вход выполнен, он сохранится в %s", PROFILE_DIR)
            return
        print("  Вход пока не виден. Войдите в окне Chrome и снова нажмите Enter.")


def _is_signin_stub(filepath) -> bool:
    """Негодная страница в кэше:
    - список игроков команды, сохранённый без входа (форма входа, ни одной ссылки на игрока);
    - страница игрока не на английском (метки вроде 'Sprint speed' не найдутся -> пустые рейтинги)."""
    name = filepath.name
    if not (name.startswith("players_") or name.startswith("player_")):
        return False
    try:
        if not filepath.exists():
            return False
        text = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if name.startswith("players_"):
        return 'type="password"' in text and 'href="/player/' not in text
    return "Sprint speed" not in text


def _reader_class(sd, headless: bool):
    """SoFIFA из soccerdata, но все читатели используют ОДИН Chrome с постоянным профилем
    (вход в аккаунт сохраняется) и английским интерфейсом (разбор страниц не ломается)."""
    import seleniumbase as sb

    class SoFIFA(sd.SoFIFA):
        _shared = None

        def get(self, url, filepath=None, *args, **kwargs):
            # страницы, скачанные без входа в аккаунт (форма входа вместо игроков), удаляем из кэша
            if filepath is not None and _is_signin_stub(filepath):
                log.info("SoFIFA: перекачиваю %s (сохранена без входа или не на английском)", filepath.name)
                filepath.unlink()
            return super().get(url, filepath, *args, **kwargs)

        def _init_webdriver(self):
            cls = SoFIFA
            if cls._shared is not None and not hasattr(self, "_driver"):
                return cls._shared                     # новый читатель — тот же браузер
            if cls._shared is not None:                # перезапуск браузера (после капчи и т.п.)
                try:
                    cls._shared.quit()
                except Exception:  # noqa: BLE001
                    pass
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            cls._shared = sb.Driver(uc=True, headless=self.headless, locale_code="en",
                                    user_data_dir=str(PROFILE_DIR))
            _ensure_login(cls._shared, self.headless)
            return cls._shared

    return SoFIFA


def edition_season(edition: str) -> int | None:
    """'FC 24' -> 2023, 'FIFA 22' -> 2021 (год начала сезона)."""
    m = re.search(r"(\d{2})\s*$", str(edition or ""))
    return 2000 + int(m.group(1)) - 1 if m else None


def pick_versions(versions: pd.DataFrame, seasons) -> dict[int, tuple[int, str]]:
    """{год начала сезона: (version_id последнего обновления издания, издание)}."""
    out = {}
    for vid, row in versions.sort_index().iterrows():
        y = edition_season(row["fifa_edition"])
        if y in seasons:
            out[y] = (int(vid), row["fifa_edition"])  # sort_index -> остаётся последнее
    return out


def to_int(v):
    """'85' / '85+2' / None -> 85 / None."""
    m = re.match(r"\s*(\d+)", str(v)) if v is not None else None
    return int(m.group(1)) if m else None


def pick_league_name(api_leagues: list[dict], country: str, keywords: list[str], tier: int = 1) -> str | None:
    """Выбирает название лиги страны из /api/league sofifa ('[Страна] Лига').
    Для высших лиг (tier=1) отбрасываются вторые дивизионы ("2", "B", "Hypermotion")."""
    cands = []
    for node in api_leagues:
        for ch in node.get("childs", []):
            if str(ch.get("nationName", "")).lower() != country.lower():
                continue
            val = str(ch.get("value", ""))
            low = val.lower()
            second = re.search(r"\b(2|ii|b)\b", low) or "hypermotion" in low
            if any(k in low for k in keywords) and (tier > 1 or not second):
                cands.append((int(ch.get("id", 0)), f"[{ch['nationName']}] {val}"))
    return sorted(cands)[0][1] if cands else None


def _register_custom_leagues(sd, probe) -> None:
    """Прописывает в LEAGUE_DICT soccerdata актуальные названия лиг на sofifa."""
    import json

    from soccerdata import sofifa as sf
    from soccerdata._config import LEAGUE_DICT
    try:
        reader = probe.get(sf.SO_FIFA_API + "/api/league", probe.data_dir / "leagues.json")
        data = json.load(reader)["data"]
    except Exception as e:  # noqa: BLE001
        log.warning("SoFIFA: не удалось получить список лиг (%s) — будут только топ-5", e)
        return
    for key, (country, words, tier) in CUSTOM.items():
        name = pick_league_name(data, country, words, tier)
        if name:
            LEAGUE_DICT.setdefault(key, {})["SoFIFA"] = name
            log.info("SoFIFA: %s -> %s", key, name)
        else:
            log.warning("SoFIFA: лига %s не найдена на sofifa", key)
    # soccerdata запоминает список лиг при создании первого читателя — сбрасываем
    for cls in type(probe).__mro__:
        if "_all_leagues_dict" in cls.__dict__:
            delattr(cls, "_all_leagues_dict")


def run(seasons=SEASONS, leagues=None, headless: bool = False):
    sd = import_soccerdata()
    leagues = leagues or list(LEAGUES)
    SoFIFA = _reader_class(sd, headless)
    probe = SoFIFA(leagues="ENG-Premier League", versions="latest", headless=headless)
    _register_custom_leagues(sd, probe)
    versions = pick_versions(probe.read_versions(), set(seasons))
    if not versions:
        log.error("SoFIFA: нет изданий для сезонов %s", seasons)
        return
    log.info("SoFIFA: издания %s", {season_label(y): v[1] for y, v in versions.items()})
    available = set(SoFIFA.available_leagues())

    with SessionLocal() as s:
        squads = load_squads(s)
        if squads.empty:
            log.error("Составов нет — сначала запустите шаг transfermarkt")
            return
        matcher = Matcher(squads, "sofifa_id")
        total = 0
        for league in leagues:
            if league not in available:
                log.warning("SoFIFA: %s недоступна — пропуск", league)
                continue
            for y, (vid, edition) in sorted(versions.items()):
                season = season_label(y)
                try:
                    reader = SoFIFA(leagues=[league], versions=[vid], headless=headless)
                    players = reader.read_players().reset_index()
                except Exception as e:  # noqa: BLE001
                    log.warning("SoFIFA %s %s: нет состава (%s)", league, edition, e)
                    continue
                if players.empty:
                    continue
                tmap = matcher.team_map(league, season, players["team"].dropna().unique().tolist())
                rows = []
                for p in tqdm(players.itertuples(), total=len(players), desc=f"SoFIFA {league} {edition}"):
                    pid = matcher.player(league, season, tmap.get(p.team), p.player, p.player_id)
                    if pid is None:
                        continue
                    try:
                        r = reader.read_player_ratings(player=int(p.player_id))
                    except Exception as e:  # noqa: BLE001
                        log.warning("SoFIFA: игрок %s (%s): %s", p.player, p.player_id, e)
                        continue
                    if r.empty:
                        continue
                    r = r.iloc[0]
                    row = {"player_id": int(pid), "season": season, "sofifa_id": int(p.player_id),
                           "fifa_edition": edition, "version_id": vid}
                    # soccerdata называет колонки 'sprintspeed', 'overallrating' (пробелы просто удаляет)
                    row.update({c: to_int(r.get(c, r.get(c.replace("_", "")))) for c in RATING_COLUMNS})
                    if all(row[c] is None for c in RATING_COLUMNS):
                        log.warning("SoFIFA: игрок %s (%s): рейтинги не разобрались", p.player, p.player_id)
                        continue
                    row["pace"] = None
                    if row["acceleration"] is not None and row["sprint_speed"] is not None:
                        row["pace"] = round(0.45 * row["acceleration"] + 0.55 * row["sprint_speed"])
                    rows.append(row)
                # игрок мог перейти внутри лиги и встретиться в двух составах — оставляем одну строку
                rows = list({(r["player_id"], r["season"]): r for r in rows}.values())
                repo.upsert(s, PlayerRating, rows, ["player_id", "season"])
                total += len(rows)
                log.info("SoFIFA %s %s: рейтинги для %d из %d игроков", league, edition, len(rows), len(players))
        repo.set_external_ids(s, "sofifa_id", matcher.new_ext_ids())
    matcher.save_report("sofifa")
    log.info("SoFIFA: записано %d строк рейтингов", total)
