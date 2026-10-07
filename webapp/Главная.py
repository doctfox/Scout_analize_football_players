"""Скаутская панель: обзор базы. Запуск: run_app.bat (или streamlit run webapp/Главная.py)."""
import plotly.express as px
import streamlit as st

from core import (MIN_MINUTES, db_or_stop, fmt_money, league_name, load_league_strength, page_setup,
                  player_table, seasons)

page_setup("Скаут: обзор")
df = db_or_stop()

st.title("Скаутская панель")
st.caption(f"Лиг в базе: {df['league'].nunique()} · Transfermarkt, Understat, WhoScored, EA FC, bundesliga.com · "
           "слева в меню: дашборд лиги, карточка игрока, поиск игрока")

season = st.selectbox("Сезон", seasons(df))
cur = df[df["season"] == season]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Игроков в сезоне", f"{cur['player_id'].nunique():,}".replace(",", " "))
c2.metric("Команд", cur["team_id"].nunique())
c3.metric("Голов", f"{int(cur['goals'].sum()):,}".replace(",", " "))
c4.metric("С профилем для поиска", f"{int(cur['has_vector'].sum()):,}".replace(",", " "),
          help=f"Игроки с {MIN_MINUTES}+ минутами: только для них считается вектор похожести")
c5.metric("Суммарная стоимость", fmt_money(cur["market_value"].sum()))

left, right = st.columns(2)

with left:
    st.subheader("Сила лиг")
    ls = load_league_strength()
    ls = ls[ls["season"] == season].assign(name=lambda d: d["league"].map(league_name))
    st.caption("Средняя стоимость состава команды лиги относительно самой дорогой лиги сезона (= 1.0)")
    fig = px.bar(ls.sort_values("strength_coefficient"), x="strength_coefficient", y="name",
                 orientation="h", text_auto=".2f", hover_data={"avg_team_value": ":.0f"},
                 labels={"strength_coefficient": "Коэффициент", "name": "",
                         "avg_team_value": "Средняя стоимость команды, млн €"})
    fig.update_layout(height=360, margin=dict(t=10, b=10))
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Стоимость составов по лигам")
    by_league = (cur.groupby("league_ru")
                 .agg(value=("market_value", "sum"), players=("player_id", "nunique"),
                      age=("age_at_season", "mean"))
                 .reset_index().sort_values("value"))
    fig = px.bar(by_league, x="value", y="league_ru", orientation="h", text_auto=".0f",
                 hover_data={"players": True, "age": ":.1f"},
                 labels={"value": "млн €", "league_ru": "", "players": "игроков", "age": "ср. возраст"})
    fig.update_layout(height=360, margin=dict(t=10, b=10))
    st.plotly_chart(fig, width="stretch")

st.subheader("Лидеры сезона")
qual = cur[cur["minutes_played"] >= MIN_MINUTES]
cols = st.columns(4)
leaders = [("goals", "Голы", "%d"), ("assists", "Ассисты", "%d"), ("xg", "xG", "%.1f"),
           ("progressive_passes_p90", "Прогр. передачи /90", "%.2f")]
st.caption("Нажмите на имя игрока — профиль откроется в новой вкладке")
for col, (metric, title, fmt) in zip(cols, leaders):
    top = qual.nlargest(10, metric)
    with col:
        st.markdown(f"**{title}**")
        player_table(top[["player_name", "team_name", metric]]
                     .rename(columns={"player_name": "Игрок", "team_name": "Команда", metric: title}),
                     top["player_id"], key=f"lead_{metric}",
                     column_config={title: st.column_config.NumberColumn(format=fmt)})

st.subheader("Возраст и стоимость")
a, b = st.columns(2)
with a:
    fig = px.histogram(qual, x="age_at_season", color="group", nbins=25, barmode="stack",
                       labels={"age_at_season": "Возраст", "group": "Позиция"})
    fig.update_layout(height=360, margin=dict(t=10), yaxis_title="Игроков")
    st.plotly_chart(fig, width="stretch")
with b:
    v = qual.dropna(subset=["market_value", "age_at_season"])
    v = v[v["market_value"] > 0]
    fig = px.box(v, x="age_at_season", y="market_value", points=False, log_y=True,
                 labels={"age_at_season": "Возраст", "market_value": "Стоимость, млн € (лог. шкала)"})
    fig.update_layout(height=360, margin=dict(t=10))
    st.plotly_chart(fig, width="stretch")
