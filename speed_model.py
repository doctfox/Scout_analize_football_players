"""Калибровочная модель: рейтинги EA FC -> максимальная скорость, км/ч.

Идея: реальная максимальная скорость (трекинг) открыто публикуется только Бундеслигой,
а рейтинги EA FC (sprint_speed, acceleration) есть у игроков всех лиг. Обучаем линейную
регрессию на игроках Бундеслиги, у которых есть и замер, и рейтинг, и оцениваем скорость
остальных. Результат — player_stats.max_speed_est (оценка!), реальный замер остаётся в
max_speed. Качество — 5-кратная кросс-валидация (R², MAE, RMSE), пишется в speed_models
и в reports/speed_model_train.csv (факт и прогноз для каждого игрока обучающей выборки).

    max_speed ≈ b0 + b1·sprint_speed + b2·acceleration + b3·возраст + b4·ln(1+минуты) + позиция
"""
import json
import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold, cross_val_predict
from sqlalchemy import text

from config import DB_SCHEMA, MIN_MINUTES_FOR_SPEED_MODEL, REPORTS_DIR
from database import SessionLocal
from models import SpeedModel

log = logging.getLogger(__name__)

TRAIN_SOURCE = "bundesliga.com"
FEATURES = ["sprint_speed", "acceleration", "age", "log_minutes", "pos_DEF", "pos_MID", "pos_FWD"]
MIN_TRAIN = 50
CLIP = (24.0, 38.5)  # физически правдоподобный диапазон для профессионалов, км/ч


def position_group(pos) -> str:
    p = str(pos or "").lower()
    if "goalkeeper" in p or p == "gk":
        return "GK"
    if "midfield" in p:                      # до "defen": 'Defensive Midfield' — полузащитник
        return "MID"
    if "back" in p or "defen" in p:
        return "DEF"
    if any(k in p for k in ("wing", "forward", "striker", "attack")):
        return "FWD"
    return "MID"


def attach_ratings(stats: pd.DataFrame, ratings: pd.DataFrame, max_gap: int = 1) -> pd.DataFrame:
    """Рейтинг того же сезона, а если его нет — ближайшего (не дальше max_gap сезонов)."""
    st = stats.copy()
    st["_y"] = st["season"].str[:4].astype(int)
    rt = ratings.copy()
    rt["_ry"] = rt["season"].str[:4].astype(int)
    m = st.merge(rt.drop(columns="season"), on="player_id", how="left")
    m["_gap"] = (m["_y"] - m["_ry"]).abs()
    m = m[m["_gap"].isna() | (m["_gap"] <= max_gap)]
    m = m.sort_values(["stat_id", "_gap"]).drop_duplicates("stat_id")
    return st[["stat_id"]].merge(m, on="stat_id", how="left").drop(columns=["_y_x", "_y_y"], errors="ignore")


def build_features(df: pd.DataFrame, fill: dict | None = None) -> tuple[pd.DataFrame, dict]:
    X = pd.DataFrame(index=df.index)
    X["sprint_speed"] = df["sprint_speed"].astype(float)
    X["acceleration"] = df["acceleration"].astype(float)
    X["age"] = df["age_at_season"].astype(float)
    X["log_minutes"] = np.log1p(df["minutes_played"].astype(float))
    grp = df["position"].map(position_group)
    for g in ("DEF", "MID", "FWD"):
        X[f"pos_{g}"] = (grp == g).astype(float)
    if fill is None:
        # медиана выборки; если колонка пустая целиком (медиана = NaN) — типичное значение
        default = {"age": 26.0, "log_minutes": float(np.log1p(1500)),
                   "sprint_speed": 70.0, "acceleration": 70.0}
        fill = {}
        for c, d in default.items():
            med = X[c].median()
            fill[c] = float(med) if pd.notna(med) else d
            n_na = int(X[c].isna().sum())
            if n_na:
                log.warning("speed_model: %s пусто у %d из %d строк -> подставлено %.2f",
                            c, n_na, len(X), fill[c])
    return X.fillna(fill), fill


def fit(train: pd.DataFrame):
    X, fill = build_features(train)
    y = train["max_speed"].astype(float).values
    model = LinearRegression().fit(X[FEATURES], y)
    cv = cross_val_predict(LinearRegression(), X[FEATURES], y,
                           cv=KFold(5, shuffle=True, random_state=42))
    resid = y - cv
    metrics = {
        "r2_cv": float(1 - (resid ** 2).sum() / ((y - y.mean()) ** 2).sum()),
        "mae_cv": float(np.abs(resid).mean()),
        "rmse_cv": float(np.sqrt((resid ** 2).mean())),
        "mae_baseline": float(np.abs(y - y.mean()).mean()),  # "всем — среднюю скорость"
    }
    return model, fill, cv, metrics


def predict(model, fill, df: pd.DataFrame) -> np.ndarray:
    X, _ = build_features(df, fill)
    return np.clip(model.predict(X[FEATURES]), *CLIP)


def run():
    with SessionLocal() as s:
        import repository as _repo
        n_age = _repo.fill_ages(s)
        if n_age:
            log.info("Возраст на сезон посчитан для %d строк", n_age)
        stats = pd.DataFrame(s.execute(text(f"""
            SELECT ps.stat_id, ps.player_id, ps.league, ps.season, ps.minutes_played,
                   ps.age_at_season, ps.max_speed, ps.max_speed_source, p.player_name, p.position
            FROM {DB_SCHEMA}.player_stats ps
            JOIN {DB_SCHEMA}.players p ON p.player_id = ps.player_id
        """)).mappings().all())
        ratings = pd.DataFrame(s.execute(text(f"""
            SELECT player_id, season, sprint_speed, acceleration
            FROM {DB_SCHEMA}.player_ratings
            WHERE sprint_speed IS NOT NULL AND acceleration IS NOT NULL
        """)).mappings().all())
        if stats.empty or ratings.empty:
            log.error("Нет данных: нужны шаги transfermarkt, bundesliga_speed и sofifa")
            return

        df = attach_ratings(stats, ratings)
        df = df[df["sprint_speed"].notna()]
        train = df[(df["max_speed_source"] == TRAIN_SOURCE) & df["max_speed"].notna()
                   & (df["minutes_played"].fillna(0) >= MIN_MINUTES_FOR_SPEED_MODEL)]
        if len(train) < MIN_TRAIN:
            log.error("Для обучения мало игроков: %d (нужно ≥ %d). Запустите bundesliga_speed и "
                      "sofifa для GER-Bundesliga", len(train), MIN_TRAIN)
            return

        model, fill, cv, metrics = fit(train)
        coefs = dict(zip(FEATURES, map(float, model.coef_)))
        log.info("Модель скорости: n=%d, R²(CV)=%.3f, MAE(CV)=%.2f км/ч, RMSE(CV)=%.2f км/ч "
                 "(без модели MAE=%.2f)", len(train), metrics["r2_cv"], metrics["mae_cv"],
                 metrics["rmse_cv"], metrics["mae_baseline"])
        log.info("Коэффициенты: %s, свободный член %.2f",
                 {k: round(v, 3) for k, v in coefs.items()}, model.intercept_)

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        rep = train[["player_name", "season", "position", "minutes_played", "sprint_speed",
                     "acceleration", "max_speed"]].copy()
        rep["predicted_cv"] = np.round(cv, 2)
        rep["error"] = np.round(rep["predicted_cv"] - rep["max_speed"], 2)
        rep.sort_values("error").to_csv(REPORTS_DIR / "speed_model_train.csv", index=False)

        df["est"] = np.round(predict(model, fill, df), 2)
        s.execute(text(f"UPDATE {DB_SCHEMA}.player_stats SET max_speed_est = NULL"))
        s.execute(text(f"UPDATE {DB_SCHEMA}.player_stats SET max_speed_est = :v WHERE stat_id = :id"),
                  [{"id": int(i), "v": float(v)} for i, v in zip(df["stat_id"], df["est"])])
        s.add(SpeedModel(algorithm="LinearRegression", features=json.dumps(FEATURES),
                         coefficients=json.dumps(coefs), intercept=float(model.intercept_),
                         n_train=len(train), r2_cv=metrics["r2_cv"], mae_cv=metrics["mae_cv"],
                         rmse_cv=metrics["rmse_cv"], n_predicted=len(df)))
        s.commit()
    log.info("Оценка скорости записана для %d строк (player_stats.max_speed_est)", len(df))
