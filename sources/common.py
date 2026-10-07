"""Общее для шагов soccerdata: настройка кастомных лиг и сопоставление с Transfermarkt."""
import json
import logging
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from config import DB_SCHEMA, REPORTS_DIR, SOCCERDATA_CUSTOM_LEAGUES
from utils import match_player, match_teams

log = logging.getLogger(__name__)

# Кэш soccerdata — в data/soccerdata проекта при любом способе запуска (а не в ~/soccerdata).
# soccerdata читает переменную при импорте, поэтому задаём её здесь, до любого import soccerdata.
# Явно заданный SOCCERDATA_DIR (Docker, start.bat) имеет приоритет.
os.environ.setdefault("SOCCERDATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "soccerdata"))


def import_soccerdata():
    """Пишет league_dict.json (РПЛ, Эредивизи, Португалия) и только потом импортирует soccerdata."""
    base = Path(os.environ["SOCCERDATA_DIR"])
    cfg = base / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    f = cfg / "league_dict.json"
    current = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    current.update(SOCCERDATA_CUSTOM_LEAGUES)
    f.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    import soccerdata as sd
    return sd


def load_squads(session) -> pd.DataFrame:
    """Составы из Transfermarkt — справочник для сопоставления игроков других источников."""
    q = text(f"""
        SELECT s.league, s.season, s.team_id, t.team_name, s.player_id, p.player_name,
               p.tm_player_id, p.birth_date, p.understat_id, p.whoscored_id, p.sofifa_id
        FROM {DB_SCHEMA}.squads s
        JOIN {DB_SCHEMA}.teams t ON t.team_id = s.team_id
        JOIN {DB_SCHEMA}.players p ON p.player_id = s.player_id
    """)
    return pd.DataFrame(session.execute(q).mappings().all())


class Matcher:
    """Сопоставляет (лига, сезон, команда, игрок) источника с team_id/player_id из БД."""

    def __init__(self, squads: pd.DataFrame, ext_col: str | None):
        self.squads = squads
        self.ext_col = ext_col  # 'understat_id' | 'whoscored_id' | 'sofifa_id' | None (только по имени)
        self.ext_known = {}
        if ext_col and ext_col in squads:
            self.ext_known = {int(r[ext_col]): r["player_id"]
                              for _, r in squads.dropna(subset=[ext_col]).iterrows()}
            self._load_manual_aliases()
        self._team_cache = {}
        self.unmatched_teams, self.unmatched_players = [], []

    def _load_manual_aliases(self):
        """reports/player_aliases.csv: ручные соответствия для тех, кого не нашёл автоматический поиск.
        Колонки: source (understat|whoscored), ext_id, tm_player_id
        """
        f = REPORTS_DIR / "player_aliases.csv"
        if not f.exists() or self.squads.empty:
            return
        src = self.ext_col.replace("_id", "")
        tm_to_pid = dict(zip(self.squads.tm_player_id.astype(str), self.squads.player_id))
        aliases = pd.read_csv(f, dtype=str)
        for r in aliases[aliases["source"].str.strip() == src].itertuples():
            pid = tm_to_pid.get(str(r.tm_player_id).strip())
            if pid is not None:
                self.ext_known[int(r.ext_id)] = pid

    def team_map(self, league: str, season: str, source_teams: list[str]) -> dict[str, int]:
        key = (league, season)
        if key not in self._team_cache:
            sq = self.squads[(self.squads.league == league) & (self.squads.season == season)]
            tm = dict(zip(sq.team_name, sq.team_id))
            mapping = match_teams(sorted(set(source_teams)), tm)
            for t in set(source_teams) - set(mapping):
                self.unmatched_teams.append({"league": league, "season": season, "team": t})
            self._team_cache[key] = mapping
        return self._team_cache[key]

    def player(self, league, season, team_id, name, ext_id=None) -> int | None:
        if ext_id is not None and not pd.isna(ext_id) and int(ext_id) in self.ext_known:
            return self.ext_known[int(ext_id)]
        sq = self.squads[(self.squads.league == league) & (self.squads.season == season)]
        pid = None
        if team_id is not None:
            team_sq = sq[sq.team_id == team_id]
            pid = match_player(name, dict(zip(team_sq.player_name, team_sq.player_id)))
        if pid is None:  # игрок мог перейти внутри лиги: ищем по всей лиге строже
            pid = match_player(name, dict(zip(sq.player_name, sq.player_id)), threshold=92)
        if pid is None:
            self.unmatched_players.append({"league": league, "season": season,
                                           "team_id": team_id, "player": name, "ext_id": ext_id})
        elif ext_id is not None and not pd.isna(ext_id):
            self.ext_known[int(ext_id)] = pid
        return pid

    def new_ext_ids(self) -> dict[int, int]:
        """{player_id: ext_id} для записи в players.<ext_col>."""
        return {pid: ext for ext, pid in self.ext_known.items()}

    def save_report(self, name: str):
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.unmatched_teams).to_csv(REPORTS_DIR / f"{name}_unmatched_teams.csv", index=False)
        pd.DataFrame(self.unmatched_players).to_csv(REPORTS_DIR / f"{name}_unmatched_players.csv", index=False)
        log.info("%s: не сопоставлено команд %d, игроков %d (см. папку reports)",
                 name, len(self.unmatched_teams), len(self.unmatched_players))
