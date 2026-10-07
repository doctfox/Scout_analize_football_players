"""Поиск игрока: по фильтрам, похожие на игрока и по профилю (векторный поиск pgvector).

Фильтры в боковой панели общие для всех режимов. Вектор игрока — стандартизованные метрики из
config.VECTOR_FEATURES (шаг vectors), близость — косинусная (оператор <=>, индекс HNSW);
hnsw.iterative_scan не даёт индексу «потерять» результаты, когда фильтр отсекает большую часть строк.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

from core import (GROUP_ORDER, METRICS, MIN_MINUTES, RADAR, SCHEMA, VECTOR_FEATURES, db_or_stop, engine,
                  fmt_money, label, league_name, leagues, load_vectors, page_setup, percentile_in, player_table,
                  radar_figure, seasons)

page_setup("Поиск игрока")
df = db_or_stop()
st.title("Поиск игрока")


# ============================================================== фильтры (боковая панель)
def _range(col: str, title: str, step: float = 1.0):
    """Слайдер диапазона по фактическим значениям колонки; None — фильтр не трогали."""
    vals = df[col].dropna()
    if vals.empty:
        return None
    lo, hi = float(np.floor(vals.min())), float(np.ceil(vals.max()))
    if lo >= hi:
        return None
    sel = st.slider(title, lo, hi, (lo, hi), step=step, key=f"f_{col}")
    return None if sel == (lo, hi) else sel


with st.sidebar:
    st.header("Фильтры")
    f_seasons = st.multiselect("Сезоны", seasons(df), default=seasons(df)[:1], placeholder="все", key="f_seasons")
    f_leagues = st.multiselect("Лиги", leagues(df), format_func=league_name, placeholder="все", key="f_leagues")
    scope = df[df["season"].isin(f_seasons or seasons(df)) & df["league"].isin(f_leagues or leagues(df))]
    f_teams = st.multiselect("Команды", sorted(scope["team_name"].dropna().unique()), placeholder="все",
                             key="f_teams")
    f_groups = st.multiselect("Группы позиций", GROUP_ORDER, placeholder="все", key="f_groups")
    pos_opts = sorted(df.loc[df["group"].isin(f_groups or GROUP_ORDER), "position"].dropna().unique())
    f_pos = st.multiselect("Позиции", pos_opts, placeholder="все", key="f_pos")

    st.subheader("Игрок")
    f_age = _range("age_at_season", "Возраст")
    f_nat = st.multiselect("Гражданство", sorted(df["nationality"].dropna().unique()), placeholder="любое",
                           key="f_nat")
    feet = sorted(f for f in df["foot"].dropna().unique() if f)
    f_foot = st.multiselect("Рабочая нога", feet, placeholder="любая", key="f_foot")
    f_height = _range("height_cm", "Рост, см")
    f_value = _range("market_value", "Стоимость, млн €", step=0.5)

    st.subheader("Игровое время")
    f_minutes = st.slider("Минимум минут", 0, int(df["minutes_played"].max() or 0), MIN_MINUTES, step=90,
                          key="f_minutes")
    f_apps = st.slider("Минимум матчей", 0, int(df["appearances"].max() or 0), 0, key="f_apps")

    st.subheader("Физика и EA FC")
    f_speed = _range("speed", "Макс. скорость, км/ч", step=0.5)
    f_overall = _range("overall_rating", "Рейтинг EA FC")
    f_potential = _range("potential", "Потенциал EA FC")

    st.subheader("Пороги по метрикам")
    thr_opts = [m for m in METRICS if m in df.columns and df[m].notna().any()
                and m not in ("age_at_season", "market_value", "speed", "overall_rating", "potential", "height_cm")]
    f_thr = {}
    for m in st.multiselect("Метрики", thr_opts, format_func=label, placeholder="например, Голы /90",
                            key="f_thr"):
        vals = df.loc[df["minutes_played"] >= MIN_MINUTES, m].dropna()
        lo, hi = float(vals.min()), float(vals.quantile(0.99))
        better_high = METRICS[m][1]
        v = st.slider(f"{label(m)} {'не меньше' if better_high else 'не больше'}", lo, hi,
                      lo if better_high else hi, key=f"thr_{m}")
        f_thr[m] = (v, better_high)

    if st.button("Сбросить фильтры"):
        for k in list(st.session_state):
            if k.startswith(("f_", "thr_")):
                del st.session_state[k]
        st.rerun()


def _apply(d: pd.DataFrame) -> pd.DataFrame:
    m = d["minutes_played"].fillna(0).ge(f_minutes) & d["appearances"].fillna(0).ge(f_apps)
    for col, sel in [("season", f_seasons), ("league", f_leagues), ("team_name", f_teams), ("group", f_groups),
                     ("position", f_pos), ("nationality", f_nat), ("foot", f_foot)]:
        if sel:
            m &= d[col].isin(sel)
    for col, rng in [("age_at_season", f_age), ("height_cm", f_height), ("market_value", f_value),
                     ("speed", f_speed), ("overall_rating", f_overall), ("potential", f_potential)]:
        if rng:
            m &= d[col].between(*rng)
    for col, (v, better_high) in f_thr.items():
        m &= d[col].ge(v) if better_high else d[col].le(v)
    return d[m]


found = _apply(df)
st.caption(f"Под фильтры подходит игроко-сезонов: **{len(found):,}**".replace(",", " "))


# ============================================================== общее
ALL = "Все"


def count_input(key: str, default: int = 20) -> int | None:
    """Сколько показать: число или «Все» (None)."""
    v = st.selectbox("Сколько найти", [10, 20, 50, 100, 500, ALL], index=[10, 20, 50, 100, 500].index(default),
                     key=key)
    return None if v == ALL else int(v)


def vec_literal(v) -> str:
    return "[" + ",".join(f"{x:.5f}" for x in v) + "]"


def parse_vec(s: str) -> np.ndarray:
    return np.array(s.strip("[]").split(","), dtype=float)


def vector_search(vec: str, allowed: list[int], limit: int | None) -> pd.DataFrame:
    """Ближайшие по косинусу среди allowed; limit=None — все allowed по убыванию сходства."""
    if not allowed:
        return pd.DataFrame(columns=["stat_id", "similarity", "fv"])
    with engine().connect() as c:
        c.execute(text("SET LOCAL hnsw.ef_search = 200"))
        c.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        res = c.execute(text(f"""
            SELECT stat_id, 1 - (feature_vector <=> CAST(:v AS vector)) AS similarity,
                   feature_vector::text AS fv
            FROM {SCHEMA}.player_stats
            WHERE feature_vector IS NOT NULL AND stat_id = ANY(:ids)
            ORDER BY feature_vector <=> CAST(:v AS vector)
            LIMIT :n"""), {"v": vec, "ids": allowed, "n": limit or len(allowed)}).mappings().all()
    return pd.DataFrame(res, columns=["stat_id", "similarity", "fv"])


TABLE_COLS = {
    "player_name": "Игрок", "team_name": "Команда", "league_ru": "Лига", "season": "Сезон",
    "position": "Позиция", "age_at_season": "Возраст", "nationality": "Гражданство",
    "minutes_played": "Минуты", "market_value": "Стоимость, млн €", "goals": "Голы", "assists": "Ассисты",
    "goals_p90": "Голы /90", "assists_p90": "Ассисты /90", "xg_p90": "xG /90", "xa_p90": "xA /90",
    "progressive_passes_p90": "Прогр. пер. /90", "tackles_p90": "Отборы /90", "speed": "Скорость, км/ч",
    "overall_rating": "EA FC",
}


def show_table(out: pd.DataFrame, extra: dict[str, str] | None = None, similarity: bool = False,
               csv_name: str = "players.csv"):
    cols = dict(TABLE_COLS, **(extra or {}))
    view = out[[c for c in cols if c in out.columns]].rename(columns=cols)
    if similarity:
        view.insert(0, "Сходство", out["similarity"].to_numpy() * 100)
    cfg = {c: st.column_config.NumberColumn(format="%.2f") for c in view.columns if c.endswith("/90")}
    cfg.update({"Скорость, км/ч": st.column_config.NumberColumn(format="%.1f"),
                "Стоимость, млн €": st.column_config.NumberColumn(format="%.1f"),
                "Сходство": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%")})
    st.caption(f"Показано: {len(view)} · нажмите на имя игрока — профиль откроется в новой вкладке")
    player_table(view, out["player_id"], key=f"tbl_{csv_name}", height=min(740, 38 + 35 * len(view)),
                 column_config=cfg)
    st.download_button("Скачать CSV", view.to_csv(index=False).encode("utf-8-sig"), csv_name, "text/csv",
                       key=f"csv_{csv_name}")


def coord_chart(series: list[tuple[str, np.ndarray]]):
    """Координаты вектора: насколько метрика выше/ниже средней (в стандартных отклонениях)."""
    fig = go.Figure()
    for name, v in series:
        fig.add_trace(go.Bar(x=[label(f) for f in VECTOR_FEATURES], y=v, name=name))
    fig.update_layout(barmode="group", height=420, margin=dict(t=10), yaxis_title="σ от среднего",
                      legend=dict(orientation="h", y=1.1))
    return fig


# из профиля игрока («Поиск похожих») приходит ?ref=<stat_id> — тогда первой открыта вкладка похожих
ref_param = st.query_params.get("ref")
if ref_param and ref_param.isdigit():
    if st.session_state.get("ref_applied") != ref_param:   # один раз, дальше эталон можно менять вручную
        st.session_state["ref_stat_id"] = int(ref_param)
        st.session_state["ref_applied"] = ref_param
    tab_player, tab_filter, tab_profile = st.tabs(["Похожие на игрока", "По фильтрам", "По профилю"])
else:
    tab_filter, tab_player, tab_profile = st.tabs(["По фильтрам", "Похожие на игрока", "По профилю"])

# ============================================================== режим 1: по фильтрам
with tab_filter:
    sort_opts = [m for m in METRICS if m in found.columns and found[m].notna().any()] or ["minutes_played"]
    c1, c2, c3 = st.columns([3, 2, 1])
    sort_by = c1.selectbox("Сортировать по", sort_opts,
                           index=sort_opts.index("goals") if "goals" in sort_opts else 0, format_func=label)
    desc = c2.radio("Порядок", ["по убыванию", "по возрастанию"], horizontal=True) == "по убыванию"
    with c3:
        n = count_input("n_filter", 100)
    if found.empty:
        st.warning("Никого не нашлось — ослабьте фильтры")
    else:
        out = found.sort_values(sort_by, ascending=not desc, na_position="last")
        out = out if n is None else out.head(n)
        show_table(out, {sort_by: label(sort_by)}, csv_name="players_filter.csv")

# ============================================================== режим 2: похожие на игрока
with tab_player:
    st.caption(f"Сходство стиля игры по {len(VECTOR_FEATURES)} метрикам (голы, ассисты, xG/xA, передачи, дриблинг, "
               f"отборы…). Кандидаты — из отфильтрованных слева, с {MIN_MINUTES}+ минутами.")
    vec_rows = df[df["has_vector"]]
    ref_opts = vec_rows.sort_values(["player_name", "season"])
    ref_text = dict(zip(ref_opts["stat_id"], ref_opts["player_name"] + " — " + ref_opts["team_name"]
                        + ", " + ref_opts["season"] + " (" + ref_opts["minutes_played"].astype(str) + " мин)"))
    ids = list(ref_text)
    pre = st.session_state.get("ref_stat_id")
    c1, c2, c3 = st.columns([4, 2, 1])
    ref_id = c1.selectbox("Эталонный игрок и сезон", ids, index=ids.index(pre) if pre in ids else None,
                          format_func=ref_text.get, placeholder="Начните вводить имя")
    c2.write("")
    same_group = c2.checkbox("Только позиция эталона", value=True)
    with c3:
        n_sim = count_input("n_sim")
    if ref_id is None:
        st.info("Выберите игрока, на которого ищем похожих")
    else:
        st.session_state["ref_stat_id"] = ref_id
        ref = vec_rows[vec_rows["stat_id"] == ref_id].iloc[0]
        age_txt = f"{int(ref['age_at_season'])} лет" if pd.notna(ref["age_at_season"]) else "возраст ?"
        st.markdown(f"**{ref['player_name']}** · {ref['position']} · {ref['team_name']} ({ref['league_ru']}) · "
                    f"{ref['season']} · {age_txt} · {fmt_money(ref['market_value'])}")
        cand = found[found["has_vector"] & found["player_id"].ne(ref["player_id"])]
        if same_group:
            cand = cand[cand["group"] == ref["group"]]
        with engine().connect() as c:
            ref_vec = c.execute(text(f"SELECT feature_vector::text FROM {SCHEMA}.player_stats WHERE stat_id = :i"),
                                {"i": int(ref_id)}).scalar()
        res = vector_search(ref_vec, cand["stat_id"].astype(int).tolist(), n_sim)
        if res.empty:
            st.warning("Никого не нашлось — ослабьте фильтры")
        else:
            out = res.merge(df, on="stat_id")
            show_table(out, similarity=True, csv_name="players_similar.csv")
            st.subheader("Сравнение с кандидатом")
            cand_id = st.selectbox("Кандидат", out["stat_id"].tolist(),
                                   format_func=dict(zip(out["stat_id"], out["player_name"] + " — "
                                                        + out["team_name"] + ", " + out["season"])).get)
            c = out[out["stat_id"] == cand_id].iloc[0]
            axes = RADAR.get(ref["group"], RADAR["Центральный полузащитник"])
            base = df[(df["group"] == ref["group"]) & (df["minutes_played"] >= MIN_MINUTES)]
            p_ref = percentile_in(base[base["season"] == ref["season"]], ref, axes)
            p_cand = percentile_in(base[base["season"] == c["season"]], c, axes)
            left, right = st.columns(2)
            left.plotly_chart(radar_figure([(f"{ref['player_name']} {ref['season']}", p_ref),
                                            (f"{c['player_name']} {c['season']}", p_cand)], axes), width="stretch")
            left.caption(f"Радар — перцентили среди позиции «{ref['group'].lower()}» в своём сезоне")
            right.plotly_chart(coord_chart([(ref["player_name"], parse_vec(ref_vec)),
                                            (c["player_name"], parse_vec(c["fv"]))]), width="stretch")
            right.caption("Координаты вектора, по которым считается сходство")

# ============================================================== режим 3: по профилю
PRESETS = {
    "—": {},
    "Плеймейкер": {"assists_p90": 90, "key_passes_p90": 95, "xa_p90": 90, "progressive_passes_p90": 90,
                   "passes_p90": 80, "pass_completion_pct": 75},
    "Опорник-разрушитель": {"tackles_p90": 95, "interceptions_p90": 90, "duels_won_pct": 80, "xg_p90": 20,
                            "pass_completion_pct": 70},
    "Таргетмен": {"aerials_won_p90": 95, "goals_p90": 80, "xg_p90": 85, "shots_p90": 80, "dribbles_p90": 25,
                  "passes_p90": 25},
    "Вингер-дриблёр": {"dribbles_p90": 95, "goals_p90": 75, "assists_p90": 80, "dribble_success_pct": 75,
                       "xa_p90": 80, "key_passes_p90": 80, "dispossessed_p90": 80},
    "Защитник с пасом": {"progressive_passes_p90": 90, "pass_completion_pct": 85, "xg_buildup_p90": 85,
                         "clearances_p90": 60, "interceptions_p90": 70},
    "Центральный форвард": {"goals_p90": 97, "npxg_p90": 95, "shots_p90": 90,
                            "assists_p90": 60, "aerials_won_p90": 70, "passes_p90": 25},
}

with tab_profile:
    st.caption("Опишите игрока перцентилями (50 = средний, 90 = больше, чем у 90% игроков). "
               "Метрики, которые не выбраны, на поиск не влияют. Кандидаты — из отфильтрованных слева.")
    c1, c2 = st.columns([4, 1])
    preset = c1.selectbox("Шаблон", list(PRESETS))
    with c2:
        n_prof = count_input("n_prof")
    targets = PRESETS[preset]
    chosen = st.multiselect("Метрики профиля", VECTOR_FEATURES, default=list(targets), format_func=label,
                            key=f"feat_{preset}")
    mat = load_vectors()
    vec = np.zeros(len(VECTOR_FEATURES))
    if chosen:
        cols = st.columns(min(4, len(chosen)))
        for i, f in enumerate(chosen):
            p = cols[i % len(cols)].slider(label(f), 1, 99, targets.get(f, 50), key=f"pct_{preset}_{f}")
            j = VECTOR_FEATURES.index(f)
            vec[j] = np.quantile(mat[:, j], p / 100)  # перцентиль -> координата вектора
    if not chosen or not np.any(vec):
        st.info("Выберите шаблон или хотя бы одну метрику")
    else:
        cand = found[found["has_vector"]]
        res = vector_search(vec_literal(vec), cand["stat_id"].astype(int).tolist(), n_prof)
        if res.empty:
            st.warning("Никого не нашлось — ослабьте фильтры")
        else:
            out = res.merge(df, on="stat_id")
            show_table(out, similarity=True, csv_name="players_profile.csv")
            best = out.iloc[0]
            st.plotly_chart(coord_chart([("Заданный профиль", vec), (best["player_name"], parse_vec(best["fv"]))]),
                            width="stretch")
            st.caption("Сравнение профиля с лучшим совпадением по координатам вектора")
