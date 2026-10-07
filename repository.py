"""Запись в БД: идемпотентные upsert'ы (повторный запуск не плодит дубли)."""
import math

import pandas as pd
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert

from models import STAT_COLUMNS, Player, PlayerStat, SquadMember, Team, Transfer


def _clean(v):
    """NaN/NA/numpy -> обычные типы Python (psycopg2 не понимает numpy.float64('nan'))."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _rows(records: list[dict]) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in records]


def upsert(session, model, records: list[dict], conflict: list[str], update_cols=None, chunk=1000,
           keep_existing: bool = False):
    """INSERT ... ON CONFLICT DO UPDATE только переданных колонок.
    keep_existing=True: пустое (NULL) новое значение не затирает уже заполненное."""
    records = _rows(records)
    if not records:
        return
    if update_cols is None:
        update_cols = [c for c in records[0] if c not in conflict]
    table = model.__table__
    for i in range(0, len(records), chunk):
        stmt = insert(model).values(records[i:i + chunk])
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict,
                set_={c: func.coalesce(stmt.excluded[c], table.c[c]) if keep_existing else stmt.excluded[c]
                      for c in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
        session.execute(stmt)
    session.commit()


# ------------------------------ Команды ---------------------------------------
def ensure_teams(session, teams: dict[str, dict], rename: bool = True) -> dict[str, int]:
    """teams: {tm_club_id: {'team_name':..., 'league':...}} -> {tm_club_id: team_id}.

    Лигу обновляем только если она известна (клубы вне 8 лиг не затирают её на NULL).
    rename=False: у уже известных клубов название не меняем (в наборе transfermarkt-datasets
    часть названий официальные — "FK Dinamo Moskva" вместо "Dynamo Moscow", — а по названиям
    сопоставляются команды Understat/WhoScored).
    """
    with_league = [{"tm_club_id": k, **v} for k, v in teams.items() if v.get("league")]
    without = [{"tm_club_id": k, "team_name": v["team_name"]} for k, v in teams.items() if not v.get("league")]
    upsert(session, Team, with_league, ["tm_club_id"], ["team_name", "league"] if rename else ["league"])
    upsert(session, Team, without, ["tm_club_id"], [])  # только вставка новых
    return team_id_map(session)


def team_id_map(session) -> dict[str, int]:
    return dict(session.execute(select(Team.tm_club_id, Team.team_id)).all())


def player_id_map(session) -> dict[str, int]:
    return dict(session.execute(select(Player.tm_player_id, Player.player_id)).all())


# ------------------------------ Игроки ---------------------------------------
def upsert_players(session, players: list[dict]):
    # пустые поля (нет роста, ноги...) не затирают то, что уже известно из другого источника
    upsert(session, Player, players, ["tm_player_id"], keep_existing=True)


def upsert_squads(session, rows: list[dict]):
    upsert(session, SquadMember, rows, ["team_id", "player_id", "season"], ["league", "market_value"])


def upsert_transfers(session, rows: list[dict]):
    upsert(session, Transfer, rows, ["player_id", "tm_transfer_id"])


def set_external_ids(session, column: str, mapping: dict[int, int]):
    """mapping: {player_id: внешний id}. Пропускаем id, уже занятые другим игроком."""
    if not mapping:
        return
    col = getattr(Player, column)
    taken = dict(session.execute(select(col, Player.player_id).where(col.isnot(None))).all())
    for pid, ext in mapping.items():
        if taken.get(ext) not in (None, pid):
            continue
        session.query(Player).filter(Player.player_id == pid).update({column: int(ext)})
        taken[ext] = pid
    session.commit()


# --------------------------- Статистика --------------------------------------
KEY = ["player_id", "team_id", "league", "season"]


# Приоритет источников для пересекающихся колонок (минуты, матчи, голы, удары...):
# WhoScored считает по каждому матчу -> самый точный, затем Understat, затем Transfermarkt.
# Источник с меньшим приоритетом НЕ перезаписывает непустые значения более приоритетного,
# поэтому шаги можно запускать в любом порядке.
SOURCE_PRIORITY = ["whoscored", "understat", "transfermarkt"]


def upsert_stats(session, df: pd.DataFrame, source: str):
    """Пишет в player_stats только колонки из df; добавляет source в data_sources."""
    if df.empty:
        return
    cols = [c for c in df.columns if c in STAT_COLUMNS]
    missing = set(KEY) - set(cols)
    if missing:
        raise ValueError(f"В статистике нет ключевых колонок: {missing}")
    df = df[cols].drop_duplicates(subset=KEY, keep="first")

    records = df.to_dict("records")
    records = _rows(records)
    update_cols = [c for c in cols if c not in KEY]
    table = PlayerStat.__table__
    for i in range(0, len(records), 1000):
        chunk = [{**r, "data_sources": source} for r in records[i:i + 1000]]
        stmt = insert(PlayerStat).values(chunk)
        ds = table.c.data_sources
        higher = SOURCE_PRIORITY[:SOURCE_PRIORITY.index(source)] if source in SOURCE_PRIORITY else []
        set_ = {}
        for c in update_cols:
            if higher:
                higher_present = or_(*[func.coalesce(func.strpos(ds, h), 0) > 0 for h in higher])
                set_[c] = case((and_(higher_present, table.c[c].isnot(None)), table.c[c]),
                               else_=stmt.excluded[c])
            else:
                set_[c] = stmt.excluded[c]
        # data_sources: дописываем источник, если его там ещё нет
        set_["data_sources"] = case(
            (ds.is_(None), literal(source)),
            (func.strpos(ds, source) > 0, ds),
            else_=ds + literal("," + source),
        )
        set_["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(index_elements=KEY, set_=set_)
        session.execute(stmt)
    session.commit()


def fill_ages(session) -> int:
    """age_at_season для всех строк player_stats из players.birth_date (возраст на 1 сентября
    года начала сезона). Нужен потому, что Understat и bundesliga.com возраст не дают.
    Дата 2000-01-01 — заглушка из составов Transfermarkt, по ней возраст не считаем."""
    from sqlalchemy import text
    res = session.execute(text("""
        UPDATE football.player_stats ps
        SET age_at_season = date_part('year', age(make_date(left(ps.season, 4)::int, 9, 1),
                                                  p.birth_date))::int
        FROM football.players p
        WHERE p.player_id = ps.player_id
          AND p.birth_date IS NOT NULL
          AND p.birth_date <> DATE '2000-01-01'
          AND ps.age_at_season IS NULL
    """))
    session.commit()
    return res.rowcount
