"""Шаг 1б. История трансферов из открытого набора данных transfermarkt-datasets.

Источник: https://github.com/dcaribou/transfermarkt-datasets (лицензия CC0, обновляется
еженедельно). Это те же данные Transfermarkt, но собранные автором набора и выложенные
одним файлом — поэтому сайт Transfermarkt вообще не запрашивается и блокировок нет.
Покрывает все наши 8 лиг; id игроков и клубов — родные id Transfermarkt, поэтому они
напрямую совпадают с tm_player_id / tm_club_id в нашей БД.

Файл (~6 МБ) перекачивается, только когда на сервере вышла новая версия (sources/tm_dataset.py).
"""
import hashlib
import logging

import pandas as pd

import repository as repo
from database import SessionLocal
from sources import tm_dataset
from utils import season_from_tm, to_millions

log = logging.getLogger(__name__)


def _club_id(v):
    if v is None or pd.isna(v):
        return None
    return str(int(v))


def run(refresh: bool = False):
    df = tm_dataset.read("transfers", refresh)
    log.info("В наборе трансферов: %d", len(df))

    with SessionLocal() as s:
        pmap = repo.player_id_map(s)                         # tm_player_id -> player_id
        df["tm_player_id"] = df["player_id"].astype("Int64").astype(str)
        df = df[df["tm_player_id"].isin(pmap.keys())].copy()
        log.info("Из них по игрокам нашей БД: %d (игроков: %d из %d)",
                 len(df), df["tm_player_id"].nunique(), len(pmap))
        if df.empty:
            log.warning("Нет совпадений — сначала запустите шаг transfermarkt (составы и игроки)")
            return

        # клубы, которых ещё нет в БД (не из 8 лиг) — добавляем без лиги
        team_ids = repo.team_id_map(s)
        new_teams = {}
        for side in ("from", "to"):
            for cid, name in zip(df[f"{side}_club_id"], df[f"{side}_club_name"]):
                cid = _club_id(cid)
                if cid and cid not in team_ids:
                    new_teams[cid] = {"team_name": (name if isinstance(name, str) and name else "Unknown"),
                                      "league": None}
        if new_teams:
            team_ids = repo.ensure_teams(s, new_teams)

        rows = []
        for r in df.itertuples(index=False):
            d = pd.to_datetime(r.transfer_date, errors="coerce")
            d = None if pd.isna(d) else d.date()
            frm, to = _club_id(r.from_club_id), _club_id(r.to_club_id)
            # в наборе нет id трансфера — строим устойчивый ключ, чтобы повторный запуск не плодил дубли
            key = hashlib.md5(f"{r.tm_player_id}|{d}|{frm}|{to}".encode()).hexdigest()[:20]
            rows.append({
                "tm_transfer_id": f"ds_{key}",
                "player_id": pmap[r.tm_player_id],
                "team_out_id": team_ids.get(frm),
                "team_in_id": team_ids.get(to),
                "transfer_date": d,
                "year": d.year if d else None,
                "season": season_from_tm(r.transfer_season),
                # в наборе: 0 = свободный трансфер ИЛИ аренда, пусто = неизвестно
                "transfer_fee": to_millions(r.transfer_fee),
                "market_value_at_transfer": to_millions(r.market_value_in_eur),
                "is_upcoming": bool(d and d > pd.Timestamp.today().date()),
            })
        rows = list({(r["player_id"], r["tm_transfer_id"]): r for r in rows}.values())
        repo.upsert_transfers(s, rows)
    log.info("Записано трансферов: %d", len(rows))
