"""Снимок базы для запуска веб-приложения без Docker и PostgreSQL.

Копирует таблицы, которые читает webapp, из PostgreSQL в один файл snapshot/scout.sqlite.
Если PostgreSQL недоступен (или пуст), webapp/core.py сам переключается на этот файл.
Запуск: make_snapshot.bat (или .venv\\Scripts\\python.exe export_snapshot.py).
"""
import datetime as dt
import decimal
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "webapp"))
from core import SCHEMA, SNAPSHOT, _database_url  # noqa: E402

TABLES = ["players", "teams", "player_stats", "squads", "player_ratings", "league_seasons", "transfers"]
INDEXES = {"player_stats": ["stat_id", "player_id"], "players": ["player_id"], "teams": ["team_id"],
           "squads": ["player_id"], "player_ratings": ["player_id"], "transfers": ["player_id"]}


def _plain(v):
    """Значения, которые sqlite3 не умеет хранить сам: Decimal, даты, массивы/JSON."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, (list, dict, tuple)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def main():
    eng = create_engine(_database_url())
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    with eng.connect() as c:
        for t in TABLES:
            sql = f"SELECT * FROM {SCHEMA}.{t}"
            if t == "player_stats":  # pgvector -> текст '[x,y,...]'
                cols = [r[0] for r in c.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"),
                    {"s": SCHEMA, "t": t})]
                sql = "SELECT " + ", ".join(f"{x}::text AS {x}" if x == "feature_vector" else x for x in cols) \
                      + f" FROM {SCHEMA}.{t}"
            df = pd.read_sql(text(sql), c)
            for col in df.columns[df.dtypes == object]:
                df[col] = df[col].map(_plain)
            df.to_sql(t, out, index=False)
            for col in INDEXES.get(t, []):
                out.execute(f"CREATE INDEX ix_{t}_{col} ON {t} ({col})")
            print(f"[ok] {t}: {len(df):,} rows")
    out.commit()
    out.execute("VACUUM")
    out.close()
    tmp.replace(SNAPSHOT)
    print(f"[ok] Snapshot saved: {SNAPSHOT} ({SNAPSHOT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
