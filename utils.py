"""Вспомогательные функции: сезоны, нормализация имён, нечёткое сопоставление."""
import re
from datetime import date

import pandas as pd
from rapidfuzz import fuzz, process
from unidecode import unidecode


# ---------------------------- Сезоны -----------------------------------------
def season_label(start_year: int) -> str:
    """2023 -> '2023/24'"""
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def season_from_tm(value: str) -> str | None:
    """Transfermarkt: '23/24' -> '2023/24'."""
    m = re.match(r"^\s*(\d{2})/(\d{2})\s*$", str(value or ""))
    if not m:
        return None
    return season_label(2000 + int(m.group(1)))


def sd_seasons(years) -> list[str]:
    """Годы начала сезона -> однозначные коды soccerdata: 2021 -> '2122'.
    Год числом передавать нельзя: soccerdata читает 2021 как '20-21' (сезон 2020/21)."""
    return [f"{int(y) % 100:02d}{(int(y) + 1) % 100:02d}" for y in years]


def season_from_soccerdata(code: str) -> str:
    """soccerdata: '2324' -> '2023/24'."""
    code = str(code)
    return season_label(2000 + int(code[:2]))


def season_start_year(label: str) -> int:
    return int(label[:4])


def age_at_season(birth: date | None, label: str) -> int | None:
    if birth is None or pd.isna(birth):
        return None
    ref = date(season_start_year(label), 9, 1)
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


def to_millions(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value) / 1_000_000, 3)


# ---------------------------- Имена ------------------------------------------
_TEAM_NOISE = re.compile(
    r"\b(fc|cf|afc|sc|ac|as|ssc|ss|us|ud|cd|rc|rcd|sv|vfb|vfl|tsg|fk|pfc|sd|ca|sl|"
    r"club|de|futbol|football|calcio|1\.|1)\b"
)


def norm_name(s: str) -> str:
    s = unidecode(str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_team(s: str) -> str:
    return re.sub(r"\s+", " ", _TEAM_NOISE.sub(" ", norm_name(s))).strip()


# Команды, которые сложно сопоставить автоматически: имя в источнике -> имя на Transfermarkt
TEAM_ALIASES = {
    "wolves": "wolverhampton wanderers",
    "psg": "paris saint germain",
    "atletico madrid": "atletico de madrid",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "borussia m gladbach": "borussia monchengladbach",
    "rasenballsport leipzig": "rb leipzig",
    "athletic club": "athletic bilbao",
    "fc bayern munchen": "bayern munich",
    "bayern munchen": "bayern munich",
}


def match_teams(source_names: list[str], tm_names: dict[str, int], threshold: int = 70) -> dict[str, int]:
    """Сопоставляет названия команд источника с командами TM той же лиги и сезона.

    tm_names: {название TM: team_id}. Возвращает {название в источнике: team_id}.
    """
    tm_norm = {norm_team(n): tid for n, tid in tm_names.items()}
    choices = list(tm_norm.keys())
    result, used = {}, set()
    scored = []
    for src in source_names:
        key = norm_team(TEAM_ALIASES.get(norm_name(src), src))
        for cand, score, _ in process.extract(key, choices, scorer=fuzz.WRatio, limit=3):
            scored.append((score, src, cand))
    # жадно: сначала самые уверенные пары, каждая команда TM используется один раз
    for score, src, cand in sorted(scored, reverse=True):
        if score < threshold or src in result or cand in used:
            continue
        result[src] = tm_norm[cand]
        used.add(cand)
    return result


def match_player(name: str, candidates: dict[str, int], threshold: int = 85) -> int | None:
    """Ищет игрока по имени среди кандидатов {имя: player_id}."""
    if not candidates:
        return None
    key = norm_name(name)
    norm = {}
    for n, pid in candidates.items():
        norm.setdefault(norm_name(n), pid)
    if key in norm:
        return norm[key]
    best = process.extractOne(key, list(norm.keys()), scorer=fuzz.token_sort_ratio)
    if best and best[1] >= threshold:
        return norm[best[0]]
    # "Vinicius Junior" vs "Vinicius Jr." / "Rodri" vs "Rodrigo Hernandez": пробуем token_set
    best = process.extractOne(key, list(norm.keys()), scorer=fuzz.token_set_ratio)
    if best and best[1] >= 95 and len(key) >= 4:
        return norm[best[0]]
    # совпадение фамилии, если она уникальна в составе
    last = key.split(" ")[-1] if key else ""
    same_last = [pid for n, pid in norm.items() if n.split(" ")[-1] == last]
    if len(last) >= 4 and len(set(same_last)) == 1:
        return same_last[0]
    return None


def safe_div(a, b, mult: float = 1.0):
    """Деление для pandas Series/скаляров с защитой от нуля."""
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        b = pd.Series(b, dtype="float64") if not isinstance(b, pd.Series) else b.astype("float64")
        return (a.astype("float64") / b.where(b > 0)) * mult
    return (a / b) * mult if b else None
