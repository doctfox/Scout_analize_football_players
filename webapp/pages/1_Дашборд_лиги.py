"""Дашборд: фильтры по лиге/сезону/позиции, лидеры по любой метрике, диаграмма рассеяния, команды."""
import plotly.express as px
import streamlit as st

from core import (GROUP_ORDER, METRICS, MIN_MINUTES, db_or_stop, label, league_name, leagues as all_leagues,
                  page_setup, player_chart, player_table,
                  seasons)

page_setup("Дашборд лиги")
df = db_or_stop()
st.title("Дашборд лиги")

# ---------------------------------------------------------------- фильтры
with st.sidebar:
    st.header("Фильтры")
    season = st.selectbox("Сезон", seasons(df))
    leagues = st.multiselect("Лиги", all_leagues(df), default=all_leagues(df), format_func=league_name)
    groups = st.multiselect("Позиции", GROUP_ORDER, default=[g for g in GROUP_ORDER if g != "Вратарь"])
    min_minutes = st.slider("Минимум минут", 0, 3000, MIN_MINUTES, step=90)
    age = st.slider("Возраст", 15, 42, (15, 42))

f = df[(df["season"] == season) & df["league"].isin(leagues) & df["group"].isin(groups)
       & (df["minutes_played"] >= min_minutes)
       & df["age_at_season"].between(*age)]
st.caption(f"В выборке: **{len(f)}** игроков")
if f.empty:
    st.warning("Под фильтры никто не подходит")
    st.stop()

metric_options = [m for m in METRICS if m in f.columns and f[m].notna().any()]


def _idx(name: str, fallback: int = 0) -> int:
    return metric_options.index(name) if name in metric_options else fallback


tab_lead, tab_scatter, tab_teams, tab_table = st.tabs(
    ["Лидеры", "Сравнение метрик", "Команды", "Таблица"])

with tab_lead:
    c1, c2 = st.columns([3, 1])
    metric = c1.selectbox("Метрика", metric_options, index=_idx("xg_p90"), format_func=label)
    n = c2.number_input("Сколько показать", 5, 50, 20)
    asc = not METRICS[metric][1]
    top = f.dropna(subset=[metric]).sort_values(metric, ascending=asc).head(n)
    top = top.assign(who=top["player_name"] + " · " + top["team_name"])
    fig = px.bar(top.iloc[::-1], x=metric, y="who", orientation="h", color="league_ru", text_auto=".2f",
                 custom_data=["player_id", "player_name"],
                 hover_data={"age_at_season": True, "minutes_played": True, "position": True, "who": False},
                 labels={metric: label(metric), "who": "", "league_ru": "Лига", "age_at_season": "Возраст",
                         "minutes_played": "Минуты", "position": "Позиция"})
    fig.update_layout(height=max(400, 26 * n), margin=dict(t=10))
    st.caption("Нажмите на столбик — появится ссылка на профиль игрока")
    player_chart(fig, "lead_chart")
    if not METRICS[metric][1]:
        st.caption("Для этой метрики меньше = лучше, поэтому наверху самые низкие значения")

with tab_scatter:
    c1, c2, c3 = st.columns(3)
    x = c1.selectbox("Ось X", metric_options, index=_idx("xg_p90"), format_func=label)
    y = c2.selectbox("Ось Y", metric_options, index=_idx("goals_p90", 1), format_func=label)
    color = c3.selectbox("Цвет", ["league_ru", "group", "age_at_season"],
                         format_func={"league_ru": "Лига", "group": "Позиция", "age_at_season": "Возраст"}.get)
    s = f.dropna(subset=[x, y])
    fig = px.scatter(s, x=x, y=y, color=color, hover_name="player_name", custom_data=["player_id", "player_name"],
                     hover_data={"team_name": True, "minutes_played": True, "age_at_season": True},
                     size=s["minutes_played"].clip(lower=1), size_max=14, opacity=0.75,
                     labels={x: label(x), y: label(y), "league_ru": "Лига", "group": "Позиция",
                             "age_at_season": "Возраст", "team_name": "Команда", "minutes_played": "Минуты"})
    fig.add_vline(x=s[x].median(), line_dash="dot", line_color="gray")
    fig.add_hline(y=s[y].median(), line_dash="dot", line_color="gray")
    fig.update_layout(height=600)
    player_chart(fig, "scatter_chart")
    st.caption("Пунктир — медианы выборки; размер точки — сыгранные минуты. "
               "Правый верхний угол — игроки, сильные по обеим метрикам. Нажмите на точку — появится ссылка на профиль.")

with tab_teams:
    t = df[(df["season"] == season) & df["league"].isin(leagues)]
    teams = (t.groupby(["team_name", "league_ru"])
             .agg(Голы=("goals", "sum"), xG=("xg", "sum"), xA=("xa", "sum"),
                  Стоимость=("market_value", "sum"), Возраст=("age_at_season", "mean"),
                  Игроков=("player_id", "nunique"))
             .reset_index().rename(columns={"team_name": "Команда", "league_ru": "Лига"}))
    teams["Голы − xG"] = teams["Голы"] - teams["xG"]
    plot = teams[teams["Стоимость"] > 0]
    fig = px.scatter(plot, x="Стоимость", y="Голы", color="Лига", hover_name="Команда", log_x=True,
                     hover_data={"xG": ":.1f", "Возраст": ":.1f"},
                     labels={"Стоимость": "Стоимость состава, млн € (лог. шкала)", "Голы": "Забито голов"})
    fig.update_layout(height=500)
    st.plotly_chart(fig, width="stretch")
    st.caption("xG есть только для лиг Understat — у Эредивизи и Лиги Португалии он пустой")
    teams = teams.sort_values("Голы", ascending=False).reset_index(drop=True)
    st.caption("Нажмите на команду, чтобы увидеть её состав")
    ev = st.dataframe(teams, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="teams_tbl", column_config={c: st.column_config.NumberColumn(format="%.1f")
                                                      for c in ["xG", "xA", "Стоимость", "Возраст", "Голы − xG"]})
    if ev.selection.rows:
        team = teams.iloc[ev.selection.rows[0]]
        squad = t[t["team_name"] == team["Команда"]].sort_values("minutes_played", ascending=False)
        st.subheader(f"Состав: {team['Команда']}, {season}")
        sq_cols = {"player_name": "Игрок", "position": "Позиция", "age_at_season": "Возраст",
                   "nationality": "Гражданство", "appearances": "Матчи", "minutes_played": "Минуты",
                   "goals": "Голы", "assists": "Ассисты", "xg": "xG", "market_value": "Стоимость, млн €"}
        st.caption("Нажмите на имя игрока — профиль откроется в новой вкладке")
        player_table(squad[list(sq_cols)].rename(columns=sq_cols), squad["player_id"], key="squad_tbl",
                     column_config={"xG": st.column_config.NumberColumn(format="%.1f"),
                                    "Стоимость, млн €": st.column_config.NumberColumn(format="%.1f")})

with tab_table:
    default_cols = ["goals", "assists", "xg_p90", "xa_p90", "key_passes_p90", "progressive_passes_p90",
                    "dribbles_p90", "tackles_p90", "interceptions_p90", "market_value"]
    chosen = st.multiselect("Колонки", metric_options, default=[c for c in default_cols if c in metric_options],
                            format_func=label)
    base = ["player_name", "team_name", "league_ru", "position", "age_at_season", "minutes_played"]
    table = f[base + [c for c in chosen if c not in base]]
    st.caption("Нажмите на имя игрока — профиль откроется в новой вкладке")
    player_table(table.rename(columns={**{c: label(c) for c in chosen}, "player_name": "Игрок",
                                       "team_name": "Команда", "league_ru": "Лига", "position": "Позиция",
                                       "age_at_season": "Возраст", "minutes_played": "Минуты"}),
                 f["player_id"], key="dash_tbl", height=600)
    st.download_button("Скачать CSV", table.to_csv(index=False).encode("utf-8-sig"),
                       f"players_{season.replace('/', '-')}.csv", "text/csv")
