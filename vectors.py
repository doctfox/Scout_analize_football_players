"""Шаг 5. Вектор профиля игрока (config.VECTOR_FEATURES) для поиска похожих игроков в pgvector.

Признаки — удельные (на 90 минут) и процентные метрики, чтобы игроки с разным
игровым временем были сопоставимы: голы и ассисты (Transfermarkt), xG/xA (Understat),
действия с мячом и без (WhoScored). Пропуски заполняются медианой позиции,
затем StandardScaler (среднее 0, стандартное отклонение 1).
"""
import logging

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from config import DB_SCHEMA, MIN_MINUTES_FOR_VECTOR, VECTOR_FEATURES as FEATURES
from database import SessionLocal

log = logging.getLogger(__name__)

# считаются здесь, в таблице их нет
DERIVED = {
    "goals_p90": "ps.goals * 90.0 / NULLIF(ps.minutes_played, 0)",
    "assists_p90": "ps.assists * 90.0 / NULLIF(ps.minutes_played, 0)",
}
# голы/ассисты есть у всех, поэтому «есть ли продвинутые данные» проверяем по остальным
ADVANCED = [f for f in FEATURES if f not in DERIVED]


def run():
    cols = ", ".join(f"{DERIVED[c]} AS {c}" if c in DERIVED else f"ps.{c}" for c in FEATURES)
    with SessionLocal() as s:
        import repository as _repo
        n_age = _repo.fill_ages(s)
        if n_age:
            log.info("Возраст на сезон посчитан для %d строк", n_age)
        df = pd.DataFrame(s.execute(text(f"""
            SELECT ps.stat_id, p.position, {cols}
            FROM {DB_SCHEMA}.player_stats ps
            JOIN {DB_SCHEMA}.players p ON p.player_id = ps.player_id
            WHERE ps.minutes_played >= :m
        """), {"m": MIN_MINUTES_FOR_VECTOR}).mappings().all())
        if df.empty:
            log.warning("Нет строк с достаточным игровым временем")
            return
        # вектор строим только там, где есть данные Understat/WhoScored (>= 5 продвинутых признаков),
        # иначе он был бы "средним игроком" и искажал поиск похожих
        df = df[df[ADVANCED].notna().sum(axis=1) >= 5].reset_index(drop=True)
        if df.empty:
            log.warning("Нет строк с продвинутыми метриками — запустите understat/whoscored")
            return
        X = df[FEATURES].astype("float64")
        # признак, которого нет вообще ни у кого (например, не запускали WhoScored) -> 0
        X = X.fillna(X.groupby(df["position"].fillna("?")).transform("median")).fillna(X.median()).fillna(0)
        Z = StandardScaler().fit_transform(X)
        Z = np.nan_to_num(Z)

        s.execute(text(f"UPDATE {DB_SCHEMA}.player_stats SET feature_vector = NULL"))
        params = [{"id": int(i), "v": "[" + ",".join(f"{x:.5f}" for x in z) + "]"}
                  for i, z in zip(df["stat_id"], Z)]
        s.execute(text(f"UPDATE {DB_SCHEMA}.player_stats SET feature_vector = CAST(:v AS vector) "
                       f"WHERE stat_id = :id"), params)
        s.commit()
    log.info("Векторы посчитаны для %d строк (минимум %d минут)", len(params), MIN_MINUTES_FOR_VECTOR)
