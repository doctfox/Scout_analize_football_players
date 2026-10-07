"""Общее для всех страниц: подключение к БД, загрузка данных (с кэшем), справочники метрик."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import LEAGUE_CATALOG, VECTOR_FEATURES  # noqa: E402  общие с парсером

SCHEMA = "football"
MIN_MINUTES = 450  # как MIN_MINUTES_FOR_VECTOR в парсере: меньше — выборка слишком шумная


def _database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = {}
    settings = ROOT / "settings.env"
    if settings.exists():
        for line in settings.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return (f"postgresql+psycopg2://{env.get('DB_USER', 'postgres')}:{env.get('DB_PASSWORD', 'change_me')}"
            f"@localhost:{env.get('DB_PORT', '5433')}/{env.get('DB_NAME', 'project_futbol')}")


@st.cache_resource
def engine():
    return create_engine(_database_url(), pool_pre_ping=True)


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine().connect() as c:
        return pd.read_sql(text(sql), c, params=params or {})


# ----------------------------------------------------------------------------- справочники

# названия лиг — из каталога парсера (config.LEAGUE_CATALOG); новая лига появится сама
LEAGUE_NAMES = {k: v["name"] for k, v in LEAGUE_CATALOG.items()}

POSITION_GROUPS = {
    "Goalkeeper": "Вратарь",
    "Centre-Back": "Центральный защитник", "Defender": "Центральный защитник",
    "Right-Back": "Крайний защитник", "Left-Back": "Крайний защитник",
    "Defensive Midfield": "Опорный полузащитник",
    "Central Midfield": "Центральный полузащитник", "Midfielder": "Центральный полузащитник",
    "Right Midfield": "Центральный полузащитник", "Left Midfield": "Центральный полузащитник",
    "Attacking Midfield": "Атакующий полузащитник", "Second Striker": "Атакующий полузащитник",
    "Right Winger": "Вингер", "Left Winger": "Вингер",
    "Centre-Forward": "Центральный форвард",
}
GROUP_ORDER = ["Вратарь", "Центральный защитник", "Крайний защитник", "Опорный полузащитник",
               "Центральный полузащитник", "Атакующий полузащитник", "Вингер", "Центральный форвард"]

# колонка -> (подпись, больше = лучше)
METRICS = {
    "goals": ("Голы", True), "assists": ("Ассисты", True),
    "minutes_played": ("Минуты", True), "appearances": ("Матчи", True),
    "xg": ("xG", True), "xa": ("xA", True), "npxg": ("npxG", True),
    "xg_p90": ("xG /90", True), "npxg_p90": ("npxG /90", True), "xa_p90": ("xA /90", True),
    "xg_chain_p90": ("xGChain /90", True), "xg_buildup_p90": ("xGBuildup /90", True),
    "goals_p90": ("Голы /90", True), "assists_p90": ("Ассисты /90", True), "ga_p90": ("Г+А /90", True),
    "shots_p90": ("Удары /90", True), "shots_on_target_p90": ("Удары в створ /90", True),
    "key_passes_p90": ("Ключевые передачи /90", True), "passes_p90": ("Передачи /90", True),
    "forward_passes_p90": ("Передачи вперёд /90", True),
    "progressive_passes_p90": ("Прогрессивные передачи /90", True),
    "pass_completion_pct": ("Точность передач, %", True),
    "forward_pass_completion_pct": ("Точность передач вперёд, %", True),
    "dribbles_p90": ("Дриблинг /90", True), "dribble_success_pct": ("Успешный дриблинг, %", True),
    "tackles_p90": ("Отборы /90", True), "tackles_won_pct": ("Успешные отборы, %", True),
    "interceptions_p90": ("Перехваты /90", True), "blocks_p90": ("Блоки /90", True),
    "clearances_p90": ("Выносы /90", True), "aerials_won_p90": ("Выигранные верховые /90", True),
    "aerials_won_pct": ("Верховые, %", True), "duels_won_pct": ("Единоборства, %", True),
    "dispossessed_p90": ("Потери при ведении /90", False), "miscontrols_p90": ("Неудачные приёмы /90", False),
    "ball_recoveries_per_match": ("Подборы за матч", True), "touches_per_match": ("Касания за матч", True),
    "max_speed": ("Макс. скорость (замер), км/ч", True),
    "max_speed_est": ("Макс. скорость (оценка), км/ч", True),
    "speed": ("Макс. скорость, км/ч", True),  # замер bundesliga.com, иначе оценка по EA FC
    "overall_rating": ("Рейтинг EA FC", True), "potential": ("Потенциал EA FC", True),
    "height_cm": ("Рост, см", True),
    "market_value": ("Стоимость, млн €", True), "age_at_season": ("Возраст", False),
}

# набор осей радара для каждой группы позиций
RADAR = {
    "Вратарь": ["passes_p90", "pass_completion_pct", "forward_passes_p90", "progressive_passes_p90",
                "clearances_p90", "aerials_won_p90", "ball_recoveries_per_match", "touches_per_match"],
    "Центральный защитник": ["tackles_p90", "interceptions_p90", "blocks_p90", "clearances_p90",
                             "aerials_won_p90", "duels_won_pct", "progressive_passes_p90",
                             "pass_completion_pct", "xg_buildup_p90", "speed"],
    "Крайний защитник": ["tackles_p90", "interceptions_p90", "duels_won_pct", "progressive_passes_p90",
                         "key_passes_p90", "assists_p90", "xa_p90", "dribbles_p90", "pass_completion_pct", "speed"],
    "Опорный полузащитник": ["tackles_p90", "interceptions_p90", "ball_recoveries_per_match", "duels_won_pct",
                             "passes_p90", "pass_completion_pct", "progressive_passes_p90",
                             "xg_buildup_p90", "dispossessed_p90", "speed"],
    "Центральный полузащитник": ["goals_p90", "assists_p90", "passes_p90", "pass_completion_pct",
                                 "progressive_passes_p90", "key_passes_p90", "xa_p90", "tackles_p90",
                                 "interceptions_p90", "dribbles_p90", "speed"],
    "Атакующий полузащитник": ["goals_p90", "assists_p90", "xg_p90", "xa_p90", "key_passes_p90", "shots_p90",
                               "dribbles_p90", "dribble_success_pct", "progressive_passes_p90",
                               "dispossessed_p90", "speed"],
    "Вингер": ["goals_p90", "assists_p90", "xg_p90", "xa_p90", "shots_p90", "key_passes_p90", "dribbles_p90",
               "dribble_success_pct", "progressive_passes_p90", "dispossessed_p90", "speed"],
    "Центральный форвард": ["goals_p90", "assists_p90", "npxg_p90", "xa_p90", "shots_p90", "shots_on_target_p90",
                            "key_passes_p90", "aerials_won_p90", "dribbles_p90", "miscontrols_p90", "speed"],
}


def label(col: str) -> str:
    return METRICS.get(col, (col, True))[0]


def league_name(code: str) -> str:
    return LEAGUE_NAMES.get(code, code)


# ----------------------------------------------------------------------------- данные

# не из player_stats: считаются в load_stats или берутся из squads / player_ratings / players
DERIVED = ("goals_p90", "assists_p90", "ga_p90", "speed", "market_value", "overall_rating", "potential", "height_cm")
STAT_COLS = sorted(({c for c in METRICS} | set(VECTOR_FEATURES)) - set(DERIVED))


@st.cache_data(ttl=3600, show_spinner="Загружаю статистику...")
def load_stats() -> pd.DataFrame:
    """Все игроко-сезоны с данными игрока, команды, стоимости и силы лиги."""
    cols = ", ".join(f"ps.{c}" for c in STAT_COLS)
    df = query(f"""
        SELECT ps.stat_id, ps.player_id, ps.team_id, ps.league, ps.season, {cols},
               ps.matches_started, ps.yellow_cards, ps.red_cards, ps.penalties_scored,
               ps.league_strength_coefficient, ps.data_sources,
               ps.feature_vector IS NOT NULL AS has_vector,
               p.player_name, p.position, p.nationality, p.foot, p.height_cm, p.birth_date,
               p.current_market_value, t.team_name, sq.market_value,
               pr.overall_rating, pr.potential, pr.acceleration, pr.sprint_speed, pr.agility, pr.stamina,
               pr.strength, pr.jumping
        FROM {SCHEMA}.player_stats ps
        JOIN {SCHEMA}.players p ON p.player_id = ps.player_id
        JOIN {SCHEMA}.teams t ON t.team_id = ps.team_id
        LEFT JOIN {SCHEMA}.squads sq
               ON sq.player_id = ps.player_id AND sq.team_id = ps.team_id AND sq.season = ps.season
        LEFT JOIN {SCHEMA}.player_ratings pr ON pr.player_id = ps.player_id AND pr.season = ps.season
    """)
    df["group"] = df["position"].map(POSITION_GROUPS).fillna("Другое")
    m90 = df["minutes_played"].where(df["minutes_played"] > 0) / 90
    df["goals_p90"] = df["goals"] / m90
    df["assists_p90"] = df["assists"] / m90
    df["ga_p90"] = (df["goals"].fillna(0) + df["assists"].fillna(0)) / m90
    df["speed"] = df["max_speed"].fillna(df["max_speed_est"])
    df["league_ru"] = df["league"].map(league_name)
    return df


@st.cache_data(ttl=3600)
def load_league_strength() -> pd.DataFrame:
    return query(f"SELECT league, season, avg_team_value, strength_coefficient FROM {SCHEMA}.league_seasons")


@st.cache_data(ttl=3600, show_spinner="Загружаю векторы...")
def load_vectors() -> np.ndarray:
    """Матрица всех векторов (для поиска по профилю — перевод перцентилей в координаты)."""
    df = query(f"SELECT feature_vector::text AS v FROM {SCHEMA}.player_stats "
               "WHERE feature_vector IS NOT NULL")
    return np.array([np.array(v.strip("[]").split(","), dtype=float) for v in df["v"]])


def leagues(df: pd.DataFrame) -> list[str]:
    """Лиги, которые есть в данных, в порядке каталога (неизвестные — в конце)."""
    order = {k: i for i, k in enumerate(LEAGUE_CATALOG)}
    return sorted(df["league"].dropna().unique(), key=lambda k: (order.get(k, len(order)), k))


def seasons(df: pd.DataFrame) -> list[str]:
    return sorted(df["season"].dropna().unique(), reverse=True)


def percentile_in(cohort: pd.DataFrame, row: pd.Series, cols: list[str]) -> dict[str, float]:
    """Перцентиль значения игрока среди когорты (0–100). Для «меньше = лучше» шкала переворачивается."""
    out = {}
    for c in cols:
        vals = cohort[c].dropna()
        v = row.get(c)
        if vals.empty or pd.isna(v):
            out[c] = np.nan
            continue
        p = (vals < v).mean() * 100 + (vals == v).mean() * 50
        out[c] = p if METRICS.get(c, ("", True))[1] else 100 - p
    return out


def fmt_money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"€{v:.1f} млн" if v >= 1 else f"€{v * 1000:.0f} тыс."


# ----------------------------------------------------------------------------- стиль Football Manager

FM_CSS = """
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"] {display: none !important;}
h1 {font-weight: 700; letter-spacing: .3px;}
h2, h3 {background: linear-gradient(90deg, #262a3d, #1d2030 70%); border-left: 4px solid #2bc07a;
        padding: 6px 12px !important; border-radius: 4px; font-size: 1.05rem !important;
        text-transform: uppercase; letter-spacing: .6px;}
[data-testid="stMetric"] {background: #1d2030; border: 1px solid #2a2e42; border-radius: 6px; padding: 8px 12px;}
[data-testid="stMetricLabel"] {text-transform: uppercase; font-size: .72rem; color: #9aa0b4;}
[data-testid="stSidebar"] {background: #181a26; border-right: 1px solid #2a2e42;}
.stTabs [data-baseweb="tab"] {text-transform: uppercase; font-size: .8rem; letter-spacing: .5px;}
.fm-panel {background: #1d2030; border: 1px solid #2a2e42; border-radius: 6px; margin-bottom: 12px;}
.fm-panel-title {background: #262a3d; padding: 6px 12px; font-size: .75rem; font-weight: 700;
                 text-transform: uppercase; letter-spacing: .7px; color: #c9cde0; border-radius: 6px 6px 0 0;}
.fm-row {display: flex; justify-content: space-between; padding: 3px 12px; font-size: .9rem;
         border-bottom: 1px solid #23273a;}
.fm-row:last-child {border-bottom: none;}
.fm-val {font-weight: 700; min-width: 26px; text-align: right;}
.fm-header {background: linear-gradient(90deg, #1f6f4a, #1d2030 75%); border-radius: 8px;
            padding: 14px 20px; margin-bottom: 14px; border: 1px solid #2a2e42;}
.fm-name {font-size: 1.8rem; font-weight: 800; color: #fff; line-height: 1.1;}
.fm-sub {color: #c9cde0; font-size: .95rem; margin-top: 4px;}
.fm-badge {display: inline-block; background: #2bc07a; color: #0e1a14; font-weight: 800; border-radius: 4px;
           padding: 1px 8px; margin-right: 8px; font-size: .8rem;}
.fm-stars {color: #f5c542; font-size: 1.1rem; letter-spacing: 1px;}
.fm-stars .pot {color: #6b6f80;}
</style>
"""


def page_setup(title: str, icon: str | None = None):
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.markdown(FM_CSS, unsafe_allow_html=True)


PLAYER_PAGE = "pages/2_Игрок.py"


def player_url(player_id, name: str = "") -> str:
    """Ссылка на профиль игрока (страница «Игрок» читает player_id из адреса).
    Имя — в конце адреса, только чтобы показать его в таблице как текст ссылки."""
    clean = str(name or "").replace("#", "").replace("&", "and")
    return f"Игрок?player_id={int(player_id)}&n={clean}"


def compare_url(stat_ids) -> str:
    """Ссылка на страницу «Сравнение» с выбранными игроко-сезонами."""
    return "Сравнение?ids=" + ",".join(str(int(s)) for s in stat_ids)


def similar_url(stat_id) -> str:
    """Ссылка на «Поиск игрока» с открытой вкладкой «Похожие на игрока» для этого игроко-сезона."""
    return f"Поиск_игрока?ref={int(stat_id)}"


def player_table(view: pd.DataFrame, player_ids, key: str, name_col: str = "Игрок", **kwargs):
    """Таблица игроков: имя — ссылка, профиль открывается в новой вкладке (страница с таблицей остаётся).
    view — что показать, player_ids — player_id для каждой строки view (в том же порядке)."""
    view = view.copy()
    if name_col in view.columns:
        view[name_col] = [player_url(pid, name) for pid, name in zip(player_ids, view[name_col])]
        cfg = dict(kwargs.pop("column_config", None) or {})
        cfg[name_col] = st.column_config.LinkColumn(name_col, display_text=r"&n=(.*)$",
                                                    help="Открыть профиль в новой вкладке")
        kwargs["column_config"] = cfg
    st.dataframe(view, hide_index=True, width="stretch", key=key, **kwargs)


def player_chart(fig, key: str, **kwargs):
    """График Plotly: клик по точке/столбику — кнопка, открывающая профиль игрока в новой вкладке.
    В фигуре должен быть custom_data=["player_id", "player_name"]."""
    event = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key=key, **kwargs)
    points = event.selection.points if event else []
    if points and points[0].get("customdata"):
        pid, name = points[0]["customdata"][:2]
        st.link_button(f"Профиль: {name} ↗", player_url(pid, name), type="primary")


# атрибуты 1–20 в профиле и сравнении: (название, метрика); физика — рейтинги EA FC 0–99
ATTRS = {
    "Атака": [("Завершение", "goals_p90"), ("Выбор позиции", "npxg_p90"), ("Удары", "shots_p90"),
              ("Точность ударов", "shots_on_target_p90"), ("Дриблинг", "dribbles_p90"),
              ("Техника", "dribble_success_pct")],
    "Созидание": [("Голевые передачи", "assists_p90"), ("Видение поля", "xa_p90"), ("Ключевой пас", "key_passes_p90"),
                  ("Продвижение мяча", "progressive_passes_p90"), ("Участие в игре", "passes_p90"),
                  ("Точность паса", "pass_completion_pct")],
    "Оборона": [("Отбор", "tackles_p90"), ("Перехваты", "interceptions_p90"), ("Блоки", "blocks_p90"),
                ("Выносы", "clearances_p90"), ("Игра головой", "aerials_won_p90"), ("Единоборства", "duels_won_pct")],
}
EA_PHYS = [("Ускорение", "acceleration"), ("Скорость", "sprint_speed"), ("Ловкость", "agility"),
           ("Выносливость", "stamina"), ("Сила", "strength"), ("Прыжок", "jumping")]


def main_season(rows: pd.DataFrame) -> pd.Series:
    """Основной сезон игрока: последний с MIN_MINUTES+ минутами (иначе просто последний)."""
    full = rows[rows["minutes_played"] >= MIN_MINUTES]
    return (full if not full.empty else rows).sort_values(["season", "minutes_played"]).iloc[-1]


def fm_attributes(df: pd.DataFrame, row: pd.Series) -> dict[str, list[tuple[str, int | None]]]:
    """Атрибуты 1–20 игроко-сезона: перцентиль среди игроков той же позиции в этом сезоне (все лиги)."""
    cohort = df[(df["season"] == row["season"]) & (df["group"] == row["group"]) & (df["minutes_played"] >= MIN_MINUTES)]
    pct = percentile_in(cohort, row, [m for items in ATTRS.values() for _, m in items] + ["speed"])
    out = {title: [(name, fm_value(pct[m])) for name, m in items] for title, items in ATTRS.items()}
    phys = [(name, None if pd.isna(row.get(c)) else int(max(1, min(20, round(row.get(c) / 5))))) for name, c in EA_PHYS]
    out["Физика"] = phys + [("Макс. скорость", fm_value(pct["speed"]))]
    return out


def fm_value(pct) -> int | None:
    """Перцентиль 0–100 -> атрибут 1–20, как в Football Manager."""
    if pct is None or pd.isna(pct):
        return None
    return int(min(20, max(1, round(pct / 5))))


def fm_color(v) -> str:
    if v is None:
        return "#6b6f80"
    return "#e05252" if v <= 5 else "#e8913a" if v <= 10 else "#e8d13a" if v <= 15 else "#2bc07a"


def fm_panel(title: str, items: list[tuple[str, int | None]]) -> str:
    """HTML-панель атрибутов: [(название, значение 1–20)]."""
    rows = "".join(
        f'<div class="fm-row"><span>{name}</span>'
        f'<span class="fm-val" style="color:{fm_color(v)}">{"—" if v is None else v}</span></div>'
        for name, v in items)
    return f'<div class="fm-panel"><div class="fm-panel-title">{title}</div>{rows}</div>'


def fm_stars(current, potential) -> str:
    """Звёзды как в FM: золотые — текущий уровень, серые — запас до потенциала (рейтинг EA FC -> 1–5)."""
    def n(v):
        return 0 if v is None or pd.isna(v) else int(max(1, min(5, round((v - 45) / 10))))
    cur, pot = n(current), n(potential)
    return f'<span class="fm-stars">{"★" * cur}<span class="pot">{"★" * max(0, pot - cur)}</span></span>'


# координаты позиций на схеме поля (атака — вправо), x 0–105, y 0–68
PITCH_POS = {
    "Goalkeeper": [(6, 34)], "Centre-Back": [(22, 26), (22, 42)], "Defender": [(22, 34)],
    "Left-Back": [(27, 60)], "Right-Back": [(27, 8)], "Defensive Midfield": [(40, 34)],
    "Central Midfield": [(52, 34)], "Midfielder": [(52, 34)], "Left Midfield": [(55, 58)],
    "Right Midfield": [(55, 10)], "Attacking Midfield": [(68, 34)], "Left Winger": [(78, 58)],
    "Right Winger": [(78, 10)], "Second Striker": [(84, 34)], "Centre-Forward": [(93, 34)],
}


def pitch_figure(position: str | None):
    """Схема поля с отмеченной позицией игрока."""
    import plotly.graph_objects as go
    line = dict(color="rgba(255,255,255,.55)", width=1.5)
    fig = go.Figure()
    shapes = [dict(type="rect", x0=0, y0=0, x1=105, y1=68, line=line, fillcolor="#1f6f4a", layer="below"),
              dict(type="line", x0=52.5, y0=0, x1=52.5, y1=68, line=line),
              dict(type="circle", x0=43.35, y0=24.85, x1=61.65, y1=43.15, line=line),
              dict(type="rect", x0=0, y0=13.84, x1=16.5, y1=54.16, line=line),
              dict(type="rect", x0=88.5, y0=13.84, x1=105, y1=54.16, line=line),
              dict(type="rect", x0=0, y0=24.84, x1=5.5, y1=43.16, line=line),
              dict(type="rect", x0=99.5, y0=24.84, x1=105, y1=43.16, line=line)]
    pts = PITCH_POS.get(position or "", [])
    if pts:
        fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers",
                                 marker=dict(size=26, color="#2bc07a", line=dict(color="#fff", width=2)),
                                 hovertext=position, hoverinfo="text"))
    fig.update_layout(shapes=shapes, height=230, margin=dict(l=4, r=4, t=4, b=4), showlegend=False,
                      plot_bgcolor="#1f6f4a", paper_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False, range=[-1, 106]),
                      yaxis=dict(visible=False, range=[-1, 69], scaleanchor="x"))
    return fig


def db_or_stop() -> pd.DataFrame:
    try:
        return load_stats()
    except Exception as e:  # noqa: BLE001
        st.error(f"Нет подключения к базе: {e}\n\nЗапустите start.bat (нужен контейнер scout-db).")
        st.stop()


def radar_figure(series: list[tuple[str, dict[str, float]]], cols: list[str]):
    """Радар перцентилей: series = [(подпись, {колонка: перцентиль})]."""
    import plotly.graph_objects as go
    theta = [label(c) for c in cols] + [label(cols[0])]
    fig = go.Figure()
    for name, pct in series:
        r = [0 if pd.isna(pct.get(c)) else pct[c] for c in cols]
        fig.add_trace(go.Scatterpolar(r=r + r[:1], theta=theta, name=name, fill="toself", opacity=0.55,
                                      hovertemplate="%{theta}: %{r:.0f}-й перцентиль<extra></extra>"))
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75])),
                      margin=dict(l=40, r=40, t=30, b=30), height=430,
                      legend=dict(orientation="h", y=-0.1))
    return fig
