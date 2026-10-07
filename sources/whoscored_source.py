"""Шаг 3. WhoScored (soccerdata): событийные данные Opta по каждому матчу всех 8 лиг.

Из событий считаются показатели каждого игрока в каждом матче (отборы, перехваты,
передачи вперёд, прогрессивные передачи, дриблинг, воздушные дуэли, потери и т.д.),
затем — средние за матч и на 90 минут за сезон.

Скачивание медленное (~5–10 с на матч, ~1500 матчей на сезон для 8 лиг), но всё
кэшируется в ~/soccerdata/data/WhoScored — прерванный запуск продолжится с того же места.
"""
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import repository as repo
from config import LEAGUES, SEASONS
from database import SessionLocal
from models import P90_METRICS, PER_MATCH_METRICS
from sources.common import Matcher, import_soccerdata, load_squads
from utils import safe_div, sd_seasons, season_from_soccerdata

log = logging.getLogger(__name__)

GOAL_X, GOAL_Y = 105.0, 34.0  # координаты WhoScored 0..100 -> метры поля 105x68


def _q(ev) -> set[str]:
    return {q.get("type", {}).get("displayName") for q in ev.get("qualifiers", []) or []}


def _name(obj):
    return (obj or {}).get("displayName") if isinstance(obj, dict) else obj


def _dist_to_goal(x, y):
    return math.hypot(GOAL_X - x * 1.05, GOAL_Y - y * 0.68)


def aggregate_match(data: dict) -> list[dict]:
    """Показатели каждого сыгравшего игрока в одном матче (matchCentreData WhoScored)."""
    max_min = data.get("expandedMaxMinute") or data.get("maxMinute") or 90
    events = data.get("events") or []

    # --- состав, позиции и минуты ---
    info = {}
    for side in ("home", "away"):
        team = data.get(side) or {}
        for p in team.get("players", []):
            starter = bool(p.get("isFirstEleven"))
            start = 0 if starter else p.get("subbedInExpandedMinute")
            if start is None:
                continue  # не выходил на поле
            end = p.get("subbedOutExpandedMinute") or max_min
            info[p["playerId"]] = {
                "team_id": team.get("teamId"), "team": team.get("name"),
                "player": p.get("name"), "is_gk": p.get("position") == "GK",
                "started": starter, "start": start, "end": end,
            }

    c = defaultdict(lambda: defaultdict(int))
    by_event_id = {(e.get("teamId"), e.get("eventId")): e for e in events}
    team_passes = defaultdict(int)

    for e in events:
        pid, typ, ok = e.get("playerId"), _name(e.get("type")), _name(e.get("outcomeType")) == "Successful"
        if pid is None:
            continue
        q = _q(e)
        s = c[pid]
        if e.get("isTouch"):
            s["touches"] += 1

        if typ == "Pass" and "ThrowIn" not in q:
            team_passes[e.get("teamId")] += 1
            s["passes"] += 1
            s["passes_completed"] += ok
            x, y, ex, ey = e.get("x"), e.get("y"), e.get("endX"), e.get("endY")
            if None not in (x, y, ex, ey):
                if ex > x:
                    s["forward_passes"] += 1
                    s["forward_passes_completed"] += ok
                    if ok and _dist_to_goal(ex, ey) <= 0.75 * _dist_to_goal(x, y):
                        s["progressive_passes"] += 1
            s["crosses"] += "Cross" in q
            s["long_balls"] += "Longball" in q
        elif typ == "TakeOn":
            s["dribbles"] += 1
            s["dribbles_won"] += ok
            s["duels"] += 1
            s["duels_won"] += ok
        elif typ == "Tackle":
            s["tackles"] += 1
            s["tackles_won"] += ok
            s["duels"] += 1
            s["duels_won"] += ok
        elif typ == "Challenge":           # игрока обвели
            s["duels"] += 1
        elif typ == "Interception":
            s["interceptions"] += 1
        elif typ == "Clearance":
            s["clearances"] += 1
        elif typ == "BlockedPass":
            s["blocks"] += 1
        elif typ == "Save" and not info.get(pid, {}).get("is_gk", False):
            s["blocks"] += 1                 # полевой игрок заблокировал удар
        elif typ == "Aerial":
            s["aerials"] += 1
            s["aerials_won"] += ok
            s["duels"] += 1
            s["duels_won"] += ok
        elif typ == "BallRecovery":
            s["ball_recoveries"] += 1
        elif typ == "Foul":
            s["fouled" if ok else "fouls"] += 1
        elif typ == "Dispossessed":
            s["dispossessed"] += 1
        elif typ == "BallTouch" and not ok:
            s["miscontrols"] += 1
        elif typ == "Card":
            card = _name(e.get("cardType"))
            if card == "Yellow":
                s["yellow_cards"] += 1
            elif card in ("Red", "SecondYellow"):
                s["red_cards"] += 1
                if pid in info:  # после удаления игрок не играет
                    info[pid]["end"] = min(info[pid]["end"], e.get("expandedMinute") or max_min)

        if e.get("isShot") and "OwnGoal" not in q:
            s["shots"] += 1
            penalty = "Penalty" in q
            s["penalties_attempted"] += penalty
            if e.get("isGoal"):
                s["goals"] += 1
                s["penalties_scored"] += penalty
                s["shots_on_target"] += 1
            elif typ == "SavedShot" and "Blocked" not in q:
                s["shots_on_target"] += 1
            # ключевая передача / голевая: удар ссылается на передачу своей команды
            rel = by_event_id.get((e.get("teamId"), e.get("relatedEventId")))
            if rel is not None and _name(rel.get("type")) == "Pass" and rel.get("playerId"):
                c[rel["playerId"]]["key_passes"] += 1
                if e.get("isGoal"):
                    c[rel["playerId"]]["assists"] += 1

    total_passes = sum(team_passes.values())
    rows = []
    for pid, meta in info.items():
        minutes = max(0, meta["end"] - meta["start"])
        row = {"ws_player_id": pid, "ws_team_id": meta["team_id"], "team": meta["team"],
               "player": meta["player"], "started": int(meta["started"]), "minutes": minutes,
               "possession": team_passes[meta["team_id"]] / total_passes if total_passes else None}
        row.update(c.get(pid, {}))
        rows.append(row)
    return rows


COUNT_COLS = PER_MATCH_METRICS + ["goals", "assists", "yellow_cards", "red_cards",
                                  "penalties_scored", "penalties_attempted", "duels", "duels_won"]


def aggregate_files(files: list[Path], league: str, season: str) -> pd.DataFrame:
    rows = []
    for f in tqdm(files, desc=f"WhoScored {league} {season}", leave=False):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not data or not data.get("events"):
            continue
        rows.extend(aggregate_match(data))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in COUNT_COLS:
        if col not in df:
            df[col] = 0
    df[COUNT_COLS] = df[COUNT_COLS].fillna(0)
    agg = {col: "sum" for col in COUNT_COLS}
    agg.update({"minutes": "sum", "started": "sum", "possession": "mean",
                "player": "last", "team": "last"})
    df = df.groupby(["ws_team_id", "ws_player_id"], as_index=False).agg(
        appearances=("minutes", "size"), **{k: (k, v) for k, v in agg.items()})
    df["league"], df["season"] = league, season
    return df


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Средние за матч, на 90 минут и проценты."""
    m, mins = df["appearances"], df["minutes"]
    out = df[["player_id", "team_id", "league", "season", "appearances",
              "goals", "assists", "yellow_cards", "red_cards",
              "penalties_scored", "penalties_attempted"]].copy()
    out["minutes_played"] = mins
    out["matches_started"] = df["started"]
    out["matches_subbed"] = m - df["started"]
    out["team_possession_share"] = df["possession"].round(4)
    out["goals_per_match"] = safe_div(df["goals"], m)
    out["assists_per_match"] = safe_div(df["assists"], m)
    for col in PER_MATCH_METRICS:
        out[f"{col}_per_match"] = safe_div(df[col], m)
    for col in P90_METRICS:
        out[f"{col}_p90"] = safe_div(df[col], mins, 90)
    out["pass_completion_pct"] = safe_div(df["passes_completed"], df["passes"], 100)
    out["forward_pass_completion_pct"] = safe_div(df["forward_passes_completed"], df["forward_passes"], 100)
    out["dribble_success_pct"] = safe_div(df["dribbles_won"], df["dribbles"], 100)
    out["tackles_won_pct"] = safe_div(df["tackles_won"], df["tackles"], 100)
    out["aerials_won_pct"] = safe_div(df["aerials_won"], df["aerials"], 100)
    out["duels_won_pct"] = safe_div(df["duels_won"], df["duels"], 100)
    return out


def run(seasons=SEASONS, leagues=None, download: bool = True, headless: bool = False):
    sd = import_soccerdata()
    leagues = leagues or list(LEAGUES)
    ws = sd.WhoScored(leagues=leagues, seasons=sd_seasons(seasons), headless=headless)
    if download:
        # output_fmt=None: только скачать и закэшировать, не держать все события в памяти
        ws.read_events(output_fmt=None, on_error="skip")

    events_dir = Path(ws.data_dir) / "events"
    with SessionLocal() as s:
        matcher = Matcher(load_squads(s), "whoscored_id")
        total = 0
        for league in leagues:
            for year in seasons:
                code = f"{str(year)[-2:]}{str(year + 1)[-2:]}"
                files = sorted((events_dir / f"{league}_{code}").glob("*.json"))
                if not files:
                    log.warning("WhoScored: нет файлов матчей для %s %s", league, code)
                    continue
                season = season_from_soccerdata(code)
                df = aggregate_files(files, league, season)
                if df.empty:
                    continue
                tmap = matcher.team_map(league, season, df["team"].unique().tolist())
                df["team_id"] = df["team"].map(tmap)
                df["player_id"] = [
                    matcher.player(league, season, t if pd.notna(t) else None, n, w)
                    for t, n, w in zip(df["team_id"], df["player"], df["ws_player_id"])
                ]
                df = df.dropna(subset=["team_id", "player_id"])
                df[["team_id", "player_id"]] = df[["team_id", "player_id"]].astype(int)
                out = compute_metrics(df)
                repo.upsert_stats(s, out, "whoscored")
                total += len(out)
                log.info("WhoScored %s %s: %d матчей, %d игроков", league, season, len(files), len(out))
        repo.set_external_ids(s, "whoscored_id", matcher.new_ext_ids())
    matcher.save_report("whoscored")
    log.info("WhoScored: записано %d строк", total)
