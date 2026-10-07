"""Коэффициент силы лиги по сезонам — по стоимости составов (Transfermarkt).

Для каждой команды складывается стоимость игроков её состава в сезоне, по лиге берётся средняя
стоимость команды; коэффициент = средняя стоимость команды лиги / максимум среди лиг сезона
(самая дорогая лига = 1.0). Пишется в league_seasons и в player_stats.league_strength_coefficient.
"""
import logging

import pandas as pd
from sqlalchemy import text

import repository as repo
from config import DB_SCHEMA, SEASONS
from database import SessionLocal
from models import LeagueSeason
from utils import season_label

log = logging.getLogger(__name__)


def run(seasons=SEASONS):
    wanted = [season_label(y) for y in seasons]
    with SessionLocal() as s:
        df = pd.DataFrame(s.execute(text(f"""
            SELECT league, season, avg(team_value) AS avg_team_value
            FROM (
                SELECT league, season, team_id, sum(market_value) AS team_value
                FROM {DB_SCHEMA}.squads
                WHERE market_value IS NOT NULL
                GROUP BY league, season, team_id
            ) t
            GROUP BY league, season
        """)).mappings().all())
        if df.empty:
            log.error("Нет составов со стоимостью — сначала запустите шаг transfermarkt")
            return
        # коэффициент нормируется по всем лигам сезона в базе, поэтому считаем по всем, пишем нужные сезоны
        df["avg_team_value"] = df["avg_team_value"].astype(float).round(3)
        df["strength_coefficient"] = (
            df["avg_team_value"] / df.groupby("season")["avg_team_value"].transform("max")).round(4)
        df = df[df["season"].isin(wanted)]
        repo.upsert(s, LeagueSeason, df.to_dict("records"), ["league", "season"])
        s.execute(text(f"""
            UPDATE {DB_SCHEMA}.player_stats ps
            SET league_strength_coefficient = ls.strength_coefficient
            FROM {DB_SCHEMA}.league_seasons ls
            WHERE ls.league = ps.league AND ls.season = ps.season
        """))
        s.commit()
    log.info("Сила лиг (средняя стоимость команды, млн €):\n%s",
             df.sort_values(["season", "strength_coefficient"], ascending=[True, False]).to_string(index=False))
