"""Детальная карточка игрока: профиль, сезоны, радар перцентилей, динамика, рейтинги EA FC, трансферы."""
import pandas as pd
import plotly.express as px
import streamlit as st

from core import (METRICS, MIN_MINUTES, RADAR, SCHEMA, compare_url, db_or_stop, fm_attributes, fm_panel, fm_stars,
                  fmt_money, label, main_season, page_setup, percentile_in, pitch_figure, query, radar_figure,
                  similar_url)

page_setup("Игрок")
df = db_or_stop()
st.title("Профиль игрока")


@st.cache_data(ttl=3600)
def player_index(_df: pd.DataFrame) -> pd.DataFrame:
    last = _df.sort_values("season").groupby("player_id").tail(1)
    idx = last[["player_id", "player_name", "team_name", "position"]].copy()
    idx["text"] = idx["player_name"] + " — " + idx["team_name"] + " (" + idx["position"].fillna("?") + ")"
    return idx.sort_values("player_name")


idx = player_index(df)
options = idx["player_id"].tolist()
pre = st.session_state.get("player_id") or st.query_params.get("player_id")
try:
    default = options.index(int(pre)) if pre else None
except ValueError:
    default = None
pid = st.selectbox("Игрок (начните вводить имя)", options, index=default,
                   format_func=dict(zip(idx["player_id"], idx["text"])).get, placeholder="Например, Haaland")
if pid is None:
    st.info("Выберите игрока")
    st.stop()
st.session_state["player_id"] = pid
st.query_params["player_id"] = str(pid)

rows = df[df["player_id"] == pid].sort_values("season")
info = rows.iloc[-1]

# ---------------------------------------------------------------- профиль (как в Football Manager)
FOOT = {"right": "правая", "left": "левая", "both": "обе"}
age_now = (pd.Timestamp.today() - pd.Timestamp(info["birth_date"])).days // 365 if pd.notna(info["birth_date"]) else "—"
height = f"{int(info['height_cm'])} см" if pd.notna(info["height_cm"]) else "—"
base_row = main_season(rows)   # по нему считаются атрибуты, звёзды и поиск похожих
st.markdown(f"""
<div class="fm-header">
  <div class="fm-name">{info['player_name']}</div>
  <div class="fm-sub"><span class="fm-badge">{info['position'] or '—'}</span>
    {info['team_name']} · {info['league_ru']}</div>
  <div class="fm-sub">{age_now} лет · {info['nationality'] or '—'} · рост {height} ·
    нога: {FOOT.get(info['foot'], info['foot'] or '—')} · стоимость {fmt_money(info['current_market_value'])}
    &nbsp; {fm_stars(base_row['overall_rating'], base_row['potential'])}</div>
</div>""", unsafe_allow_html=True)

c1, c2, _ = st.columns([1.3, 1, 3])
if base_row["has_vector"]:
    c1.link_button(f"Поиск похожих ↗  ({base_row['season']})", similar_url(base_row["stat_id"]), type="primary",
                   help="Откроется в новой вкладке: игроки с похожим стилем игры", width="stretch")
else:
    c1.caption(f"Поиск похожих недоступен: нет сезона с {MIN_MINUTES}+ минутами")
c2.link_button("Сравнить ↗", compare_url([base_row["stat_id"]]), width="stretch",
               help="Откроется в новой вкладке: сравнение с другими игроками")
cols = st.columns([1, 1, 1, 1, 1.3])
for col, (title, items) in zip(cols, fm_attributes(df, base_row).items()):
    col.markdown(fm_panel(title, items), unsafe_allow_html=True)
with cols[4]:
    st.plotly_chart(pitch_figure(info["position"]), width="stretch", config={"displayModeBar": False})
    st.caption(f"Атрибуты 1–20: сравнение с позицией «{base_row['group'].lower()}» во всех лигах, сезон "
               f"{base_row['season']} ({int(base_row['minutes_played'] or 0)} мин). Физика — EA FC и скорость.")

# ---------------------------------------------------------------- таблица сезонов
st.subheader("Статистика по сезонам")
season_cols = ["season", "team_name", "league_ru", "appearances", "matches_started", "minutes_played", "goals",
               "assists", "xg", "xa", "xg_p90", "xa_p90", "key_passes_p90", "progressive_passes_p90",
               "dribbles_p90", "tackles_p90", "interceptions_p90", "pass_completion_pct", "yellow_cards",
               "red_cards", "market_value"]
names = {"season": "Сезон", "team_name": "Команда", "league_ru": "Лига", "matches_started": "В старте",
         "yellow_cards": "ЖК", "red_cards": "КК"}
st.dataframe(rows[season_cols].rename(columns={c: names.get(c, label(c)) for c in season_cols}),
             hide_index=True, width="stretch",
             column_config={label(c): st.column_config.NumberColumn(format="%.2f")
                            for c in season_cols if c.endswith(("p90", "pct")) or c in ("xg", "xa", "market_value")})

# ---------------------------------------------------------------- радар
st.subheader("Профиль относительно игроков той же позиции")
played = rows[rows["minutes_played"] >= MIN_MINUTES]
if played.empty:
    st.info(f"Ни в одном сезоне нет {MIN_MINUTES}+ минут — перцентили были бы шумными")
else:
    c1, c2 = st.columns(2)
    season = c1.selectbox("Сезон", played["season"].unique().tolist()[::-1])
    scope = c2.radio("Сравнивать с", ["всеми лигами", "своей лигой"], horizontal=True)
    row = played[played["season"] == season].sort_values("minutes_played").iloc[-1]
    group = row["group"]
    axes = RADAR.get(group, RADAR["Центральный полузащитник"])
    cohort = df[(df["season"] == season) & (df["group"] == group) & (df["minutes_played"] >= MIN_MINUTES)]
    if scope == "своей лигой":
        cohort = cohort[cohort["league"] == row["league"]]
    pct = percentile_in(cohort, row, axes)
    left, right = st.columns([3, 2])
    left.plotly_chart(radar_figure([(f"{row['player_name']} {season}", pct)], axes), width="stretch")
    with right:
        st.caption(f"Когорта: {group.lower()}, {season}, {MIN_MINUTES}+ минут — {len(cohort)} игроков. "
                   "100 = лучший в когорте (для потерь шкала перевёрнута).")
        tbl = pd.DataFrame({"Метрика": [label(a) for a in axes],
                            "Значение": [row[a] for a in axes],
                            "Перцентиль": [pct[a] for a in axes]})
        st.dataframe(tbl, hide_index=True, width="stretch",
                     column_config={"Значение": st.column_config.NumberColumn(format="%.2f"),
                                    "Перцентиль": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                                   format="%.0f")})

# ---------------------------------------------------------------- динамика
if rows["season"].nunique() > 1:
    st.subheader("Динамика по сезонам")
    opts = [m for m in METRICS if rows[m].notna().sum() > 1]
    chosen = st.multiselect("Метрики", opts, default=[m for m in ["xg_p90", "xa_p90", "ga_p90"] if m in opts],
                            format_func=label)
    if chosen:
        long = rows.melt(id_vars=["season", "team_name"], value_vars=chosen, var_name="m", value_name="v")
        long["m"] = long["m"].map(label)
        fig = px.line(long, x="season", y="v", color="m", markers=True, hover_data=["team_name"],
                      labels={"season": "Сезон", "v": "", "m": "", "team_name": "Команда"})
        fig.update_layout(height=380)
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- EA FC + скорость + трансферы
a, b = st.columns(2)
with a:
    st.subheader("Рейтинги EA FC")
    r = query(f"SELECT season, overall_rating, potential, pace, acceleration, sprint_speed, agility, stamina, "
              f"strength, dribbling, short_passing, vision, finishing, defensive_awareness, standing_tackle, "
              f"composure FROM {SCHEMA}.player_ratings WHERE player_id = :p ORDER BY season", {"p": int(pid)})
    if r.empty:
        st.caption("Нет данных SoFIFA")
    else:
        last = r.iloc[-1]
        k = st.columns(3)
        delta = int(last["overall_rating"] - r.iloc[-2]["overall_rating"]) if len(r) > 1 else None
        k[0].metric("Общий", int(last["overall_rating"]), delta=delta)
        k[1].metric("Потенциал", int(last["potential"]))
        k[2].metric("Скорость (pace)", int(last["pace"]) if pd.notna(last["pace"]) else "—")
        attrs = last.drop(["season", "overall_rating", "potential"]).dropna().astype(int)
        fig = px.bar(x=attrs.values, y=attrs.index.str.replace("_", " "), orientation="h", range_x=[0, 99],
                     text=attrs.values, labels={"x": f"EA FC, сезон {last['season']}", "y": ""})
        fig.update_layout(height=430, margin=dict(t=10))
        st.plotly_chart(fig, width="stretch")
    spd = rows.dropna(subset=["max_speed", "max_speed_est"], how="all")
    if not spd.empty:
        s = spd.iloc[-1]
        txt = f"{s['max_speed']:.1f} км/ч (замер)" if pd.notna(s["max_speed"]) \
            else f"≈ {s['max_speed_est']:.1f} км/ч (оценка)"
        st.metric(f"Максимальная скорость, {s['season']}", txt,
                  help="Замер — bundesliga.com; оценка — модель по рейтингам EA FC")

with b:
    st.subheader("Стоимость и трансферы")
    mv = rows.dropna(subset=["market_value"]).groupby("season", as_index=False)["market_value"].max()
    if not mv.empty:
        fig = px.area(mv, x="season", y="market_value", markers=True,
                      labels={"season": "Сезон", "market_value": "млн €"})
        fig.update_layout(height=250, margin=dict(t=10))
        st.plotly_chart(fig, width="stretch")
    tr = query(f"""
        SELECT t.transfer_date AS "Дата", a.team_name AS "Откуда", b.team_name AS "Куда",
               t.transfer_fee AS "Сумма, млн €", t.market_value_at_transfer AS "Стоимость, млн €"
        FROM {SCHEMA}.transfers t
        LEFT JOIN {SCHEMA}.teams a ON a.team_id = t.team_out_id
        LEFT JOIN {SCHEMA}.teams b ON b.team_id = t.team_in_id
        WHERE t.player_id = :p AND NOT coalesce(t.is_upcoming, false)
        ORDER BY t.transfer_date DESC NULLS LAST""", {"p": int(pid)})
    if tr.empty:
        st.caption("Трансферов нет")
    else:
        st.dataframe(tr, hide_index=True, width="stretch")
        st.caption("Сумма 0 — свободный трансфер или возврат из аренды, пусто — неизвестно")
