"""Шаг 2. Understat (soccerdata): xG, npxG, xA, xGChain, xGBuildup, удары, ключевые передачи.
Покрытие: топ-5 лиг + РПЛ (Эредивизи и Португалии на Understat нет).
"""
import logging

import pandas as pd

import repository as repo
from config import LEAGUE_CATALOG, LEAGUES, SEASONS
from database import SessionLocal
from sources.common import Matcher, import_soccerdata, load_squads
from utils import safe_div, sd_seasons, season_from_soccerdata

log = logging.getLogger(__name__)


def transform(raw: pd.DataFrame, matcher: Matcher) -> pd.DataFrame:
    df = raw.reset_index()
    df["season"] = df["season"].map(season_from_soccerdata)
    rows = []
    for (league, season), grp in df.groupby(["league", "season"]):
        tmap = matcher.team_map(league, season, grp["team"].unique().tolist())
        for r in grp.itertuples():
            team_id = tmap.get(r.team)
            pid = matcher.player(league, season, team_id, r.player, r.player_id)
            if pid is None or team_id is None:
                continue
            rows.append({"player_id": pid, "team_id": team_id, "league": league, "season": season,
                         "appearances": r.matches, "minutes_played": r.minutes, "goals": r.goals,
                         "assists": r.assists, "penalties_scored": r.goals - r.np_goals,
                         "yellow_cards": r.yellow_cards, "red_cards": r.red_cards,
                         "xg": r.xg, "npxg": r.np_xg, "xa": r.xa, "xg_chain": r.xg_chain,
                         "xg_buildup": r.xg_buildup, "shots": r.shots, "key_passes": r.key_passes})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    m, mins = out["appearances"], out["minutes_played"]
    for c in ["goals", "assists", "xg", "xa", "shots", "key_passes"]:
        out[f"{c}_per_match"] = safe_div(out[c], m)
    for c in ["xg", "npxg", "xa", "xg_chain", "xg_buildup", "shots", "key_passes"]:
        out[f"{c}_p90"] = safe_div(out[c], mins, 90)
    return out.drop(columns=["shots", "key_passes"])


def run(seasons=SEASONS, leagues=None):
    sd = import_soccerdata()
    leagues = [lg for lg in (leagues or LEAGUES) if LEAGUE_CATALOG[lg]["understat"]]
    if not leagues:
        return
    reader = sd.Understat(leagues=leagues, seasons=sd_seasons(seasons))
    raw = reader.read_player_season_stats()
    log.info("Understat: %d строк", len(raw))

    with SessionLocal() as s:
        matcher = Matcher(load_squads(s), "understat_id")
        df = transform(raw, matcher)
        repo.upsert_stats(s, df, "understat")
        repo.set_external_ids(s, "understat_id", matcher.new_ext_ids())
    matcher.save_report("understat")
    log.info("Understat: записано %d строк", len(df))
