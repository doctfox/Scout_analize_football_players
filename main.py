"""Точка входа ETL-парсера.

Примеры:
    python main.py init                    # создать таблицы
    python main.py transfermarkt           # команды, игроки, составы, трансферы, базовая стат.
    python main.py understat               # xG/xA и др. (топ-5 + РПЛ)
    python main.py whoscored               # детальные метрики по матчам (долго, нужен Chrome)
    python main.py strength                # коэффициент силы лиг (по стоимости составов)
    python main.py vectors                 # векторы профилей
    python main.py bundesliga_speed        # реальная макс. скорость, bundesliga.com (нужен Chrome)
    python main.py sofifa                  # рейтинги EA FC: sprint_speed, acceleration... (Chrome, долго)
    python main.py speed_model             # калибровка "рейтинг EA -> км/ч" -> max_speed_est
    python main.py speed                   # три шага выше подряд
    python main.py all                     # всё по порядку, кроме шагов с браузером
    python main.py all --with-whoscored --with-speed
    python main.py update                  # обновить текущий сезон (новые матчи, составы, стоимости)
    python main.py leagues                 # каталог лиг: какие включены, какие можно добавить
    python main.py all --leagues "TUR-Super Lig"   # догрузить новую лигу за все сезоны

    python main.py transfermarkt --seasons 2025 --leagues "RUS-Premier League"
"""
import argparse
import logging
import time

from config import CURRENT_SEASON, LEAGUE_CATALOG, LEAGUES, SEASONS, TM_API_URL


def wait_for(name: str, check, timeout: int = 300) -> None:
    """Ждёт, пока сервис (БД, transfermarkt-api) начнёт отвечать — нужно при старте в Docker."""
    deadline = time.time() + timeout
    while True:
        try:
            check()
            logging.info("%s доступен", name)
            return
        except Exception as e:  # noqa: BLE001
            if time.time() > deadline:
                raise SystemExit(f"{name} не отвечает {timeout} с: {e}")
            logging.info("Жду %s... (%s)", name, type(e).__name__)
            time.sleep(3)


def _check_db():
    from sqlalchemy import text
    from database import engine
    with engine.connect() as c:
        c.execute(text("SELECT 1"))


def _check_tm():
    import requests
    requests.get(f"{TM_API_URL.rstrip('/')}/docs", timeout=5).raise_for_status()


def main():
    ap = argparse.ArgumentParser(description="Сбор футбольной статистики в PostgreSQL")
    ap.add_argument("step", choices=["init", "transfermarkt", "transfers", "understat", "whoscored", "strength",
                                         "bundesliga_speed", "sofifa", "speed_model", "speed",
                                         "vectors", "all", "update", "leagues"])
    ap.add_argument("--seasons", type=lambda v: CURRENT_SEASON if v == "current" else int(v), nargs="+",
                    default=None,
                    help=f"год начала сезона: 2021 2022 ... или current (по умолчанию {SEASONS[0]}–{SEASONS[-1]}, "
                         f"для update — текущий {CURRENT_SEASON})")
    ap.add_argument("--leagues", nargs="+", choices=list(LEAGUE_CATALOG), default=None, metavar="ЛИГА",
                    help="лиги из каталога (python main.py leagues); по умолчанию — LEAGUES из settings.env")
    ap.add_argument("--tm-stats", action="store_true",
                    help="transfermarkt: дополнительно скачать статистику игроков с TM (+12 тыс. запросов)")
    ap.add_argument("--no-tm-stats", action="store_true", help=argparse.SUPPRESS)  # для совместимости
    ap.add_argument("--with-whoscored", action="store_true", help="для 'all': включить WhoScored")
    ap.add_argument("--ws-no-download", action="store_true", help="WhoScored: только обработать кэш")
    ap.add_argument("--with-speed", action="store_true", help="для 'all': bundesliga_speed + sofifa + speed_model")
    ap.add_argument("--refresh", action="store_true",
                    help="bundesliga_speed: скачать заново; transfermarkt/transfers: сразу проверить новую версию набора")
    ap.add_argument("--headless", action="store_true",
                    help="WhoScored/SoFIFA/bundesliga: Chrome без окна (чаще блокируют)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.step == "leagues":
        print(f"Сезоны по умолчанию: {SEASONS}, текущий: {CURRENT_SEASON}\n")
        for key, cfg in LEAGUE_CATALOG.items():
            src = ["Transfermarkt", "WhoScored"] + (["Understat (xG)"] if cfg["understat"] else []) \
                + (["EA FC"] if cfg["sofifa"] else [])
            print(f"{'[вкл]' if key in LEAGUES else '     '} {key:24} {cfg['name']:30} {', '.join(src)}")
        print("\nВключить: добавьте ключ в LEAGUES в settings.env; разово: start.bat all --leagues \"<ключ>\"")
        return
    if args.seasons is None:
        args.seasons = [CURRENT_SEASON] if args.step == "update" else SEASONS

    wait_for("PostgreSQL", _check_db)
    from database import init_db
    init_db()
    if args.step == "init":
        return

    speed = ["bundesliga_speed", "sofifa", "speed_model"]
    if args.step in ("all", "update"):
        # update = то же, что all, но по умолчанию только текущий сезон и свежая версия набора TM
        if args.step == "update":
            args.refresh = True
        steps = (["transfermarkt", "transfers", "understat"] + (["whoscored"] if args.with_whoscored else [])
                 + (speed if args.with_speed else []) + ["strength", "vectors"])
    elif args.step == "speed":
        steps = speed
    else:
        steps = [args.step]
    for step in steps:
        logging.info("===== %s =====", step)
        if step == "transfermarkt":
            from sources import transfermarkt
            # transfermarkt-api ждём, только если он понадобится (чего-то нет в наборе)
            transfermarkt.run(args.seasons, args.leagues, with_stats=args.tm_stats, refresh=args.refresh,
                              wait_api=lambda: wait_for("transfermarkt-api", _check_tm))
        elif step == "transfers":
            from sources import transfers_dataset
            transfers_dataset.run(refresh=args.refresh)
        elif step == "understat":
            from sources import understat_source
            understat_source.run(args.seasons, args.leagues)
        elif step == "whoscored":
            from sources import whoscored_source
            whoscored_source.run(args.seasons, args.leagues, download=not args.ws_no_download,
                                 headless=args.headless)
        elif step == "strength":
            from sources import league_strength
            league_strength.run(args.seasons)
        elif step == "bundesliga_speed":
            from sources import bundesliga_speed
            bundesliga_speed.run(args.seasons, refresh=args.refresh, headless=args.headless)
        elif step == "sofifa":
            from sources import sofifa_source
            sofifa_source.run(args.seasons, args.leagues, headless=args.headless)
        elif step == "speed_model":
            import speed_model
            speed_model.run()
        elif step == "vectors":
            import vectors
            vectors.run()


if __name__ == "__main__":
    main()
