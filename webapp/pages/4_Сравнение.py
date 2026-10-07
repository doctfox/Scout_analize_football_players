"""Сравнение 2–4 игроков (каждый — в своём сезоне): атрибуты 1–20, радар, статистика.

Открывается из меню или кнопкой «Сравнить» в профиле игрока (?ids=<stat_id>,<stat_id>...).
"""
import pandas as pd
import streamlit as st

from core import (METRICS, MIN_MINUTES, RADAR, db_or_stop, fm_attributes, fm_color, fm_stars, fmt_money, label,
                  page_setup, percentile_in, player_url, radar_figure)

page_setup("Сравнение")
df = db_or_stop()
st.title("Сравнение игроков")

MAX_PLAYERS = 4

# ---------------------------------------------------------------- выбор игроков
opts = df[df["minutes_played"].fillna(0) > 0].sort_values(["player_name", "season"])
text = dict(zip(opts["stat_id"], opts["player_name"] + " — " + opts["team_name"] + ", " + opts["season"]
                + " (" + opts["minutes_played"].fillna(0).astype(int).astype(str) + " мин)"))
param = st.query_params.get("ids", "")
from_url = [int(x) for x in param.split(",") if x.strip().isdigit() and int(x) in text]
if "cmp_ids" not in st.session_state or st.session_state.get("cmp_param") != param:
    st.session_state["cmp_ids"] = from_url[:MAX_PLAYERS]     # из ссылки — один раз, дальше выбор ручной
    st.session_state["cmp_param"] = param
chosen = st.multiselect(f"Игроки и сезоны (до {MAX_PLAYERS})", list(text), key="cmp_ids", format_func=text.get,
                        max_selections=MAX_PLAYERS, placeholder="Начните вводить имя")
if len(chosen) < 2:
    st.info("Добавьте хотя бы двух игроков. Из профиля игрока кнопка «Сравнить» сразу подставляет его сюда.")
    st.stop()

players = [df[df["stat_id"] == sid].iloc[0] for sid in chosen]
names = [f"{p['player_name']} ({p['season']})" for p in players]

# ---------------------------------------------------------------- карточки
cols = st.columns(len(players))
for col, p in zip(cols, players):
    age = f"{int(p['age_at_season'])} лет" if pd.notna(p["age_at_season"]) else "—"
    col.markdown(f"""
<div class="fm-header">
  <div class="fm-name" style="font-size:1.3rem"><a href="{player_url(p['player_id'], p['player_name'])}"
     target="_blank" style="color:#fff;text-decoration:none">{p['player_name']} ↗</a></div>
  <div class="fm-sub"><span class="fm-badge">{p['position'] or '—'}</span>{p['team_name']}</div>
  <div class="fm-sub">{p['league_ru']} · {p['season']} · {age}</div>
  <div class="fm-sub">{int(p['minutes_played'] or 0)} мин · {fmt_money(p['market_value'])}
    &nbsp;{fm_stars(p['overall_rating'], p['potential'])}</div>
</div>""", unsafe_allow_html=True)

# ---------------------------------------------------------------- атрибуты 1–20 бок о бок
st.subheader("Атрибуты 1–20")
attrs = [fm_attributes(df, p) for p in players]
cell = "padding:3px 10px;border-bottom:1px solid #23273a;"
html = ['<table style="width:100%;border-collapse:collapse;font-size:.9rem;background:#1d2030;border-radius:6px">',
        f'<tr><th style="{cell}text-align:left"></th>'
        + "".join(f'<th style="{cell}text-align:center">{n}</th>' for n in names) + "</tr>"]
for group in attrs[0]:
    html.append(f'<tr><td colspan="{len(players) + 1}" style="{cell}background:#262a3d;font-weight:700;'
                f'text-transform:uppercase;font-size:.75rem;letter-spacing:.6px">{group}</td></tr>')
    for i, (attr_name, _) in enumerate(attrs[0][group]):
        vals = [a[group][i][1] for a in attrs]
        known = [v for v in vals if v is not None]
        best = max(known) if len(set(known)) > 1 else None
        tds = "".join(
            f'<td style="{cell}text-align:center;font-weight:700;color:{fm_color(v)};'
            f'{"background:#24324a;" if best is not None and v == best else ""}">'
            f'{"—" if v is None else v}</td>' for v in vals)
        html.append(f'<tr><td style="{cell}">{attr_name}</td>{tds}</tr>')
html.append("</table>")
st.markdown("".join(html), unsafe_allow_html=True)
st.caption("Атрибут — перцентиль среди игроков той же позиции в своём сезоне (все лиги), 1–20; "
           "физика — рейтинги EA FC и скорость. Подсвечено лучшее значение в строке.")

# ---------------------------------------------------------------- радар
st.subheader("Радар перцентилей")
axes = RADAR.get(players[0]["group"], RADAR["Центральный полузащитник"])
series = []
for p, n in zip(players, names):
    cohort = df[(df["season"] == p["season"]) & (df["group"] == p["group"]) & (df["minutes_played"] >= MIN_MINUTES)]
    series.append((n, percentile_in(cohort, p, axes)))
st.plotly_chart(radar_figure(series, axes), width="stretch")
st.caption(f"Оси — по позиции первого игрока («{players[0]['group'].lower()}»); "
           "перцентиль каждого считается среди игроков его позиции в его сезоне.")

# ---------------------------------------------------------------- статистика
st.subheader("Статистика")
STAT_ROWS = ["minutes_played", "appearances", "goals", "assists", "xg", "xa", "goals_p90", "assists_p90", "xg_p90",
             "npxg_p90", "xa_p90", "shots_p90", "key_passes_p90", "progressive_passes_p90", "passes_p90",
             "pass_completion_pct", "dribbles_p90", "dribble_success_pct", "tackles_p90", "interceptions_p90",
             "clearances_p90", "aerials_won_p90", "duels_won_pct", "dispossessed_p90", "speed", "overall_rating",
             "potential", "market_value", "age_at_season", "height_cm"]
NO_BEST = {"height_cm"}   # «лучшего» роста не бывает — не подсвечиваем
table = pd.DataFrame({n: [p.get(m) for m in STAT_ROWS] for p, n in zip(players, names)},
                     index=[label(m) for m in STAT_ROWS]).apply(pd.to_numeric, errors="coerce")
direction = {label(m): (None if m in NO_BEST else METRICS.get(m, ("", True))[1]) for m in STAT_ROWS}
table = table.dropna(how="all")


def _best(row: pd.Series):
    vals = row.dropna()
    higher = direction.get(row.name)
    if higher is None or len(vals) < 2 or vals.nunique() == 1:
        return [""] * len(row)
    best = vals.max() if higher else vals.min()
    return ["background-color:#24324a;color:#2bc07a;font-weight:700" if v == best else "" for v in row]


st.dataframe(table.style.apply(_best, axis=1).format("{:.2f}", na_rep="—"), width="stretch",
             height=38 + 35 * len(table))
st.caption("Подсвечено лучшее значение (для потерь и возраста — меньшее).")
