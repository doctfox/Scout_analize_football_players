"""ORM-модели. Схема football, все внешние ключи — на внутренние SERIAL id."""
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)

from config import DB_SCHEMA, VECTOR_DIM
from database import Base

S = DB_SCHEMA


class Team(Base):
    """Клубы. Сюда же попадают клубы вне 8 лиг (откуда/куда переходили игроки)."""
    __tablename__ = "teams"
    __table_args__ = {"schema": S}

    team_id = Column(Integer, primary_key=True)
    tm_club_id = Column(String(20), unique=True, nullable=False)   # id на Transfermarkt
    team_name = Column(String(150), nullable=False)
    league = Column(String(100))                                   # последняя известная лига из 8


class Player(Base):
    __tablename__ = "players"
    __table_args__ = {"schema": S}

    player_id = Column(Integer, primary_key=True)
    tm_player_id = Column(String(20), unique=True, nullable=False)
    understat_id = Column(Integer, unique=True)
    whoscored_id = Column(Integer, unique=True)
    sofifa_id = Column(Integer, unique=True)
    player_name = Column(String(150), nullable=False)
    birth_date = Column(Date)
    position = Column(String(50))
    nationality = Column(String(100))
    foot = Column(String(20))
    height_cm = Column(Integer)
    current_market_value = Column(Float)          # млн €
    team_id = Column(Integer, ForeignKey(f"{S}.teams.team_id"))  # текущий клуб
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SquadMember(Base):
    """Состав команды по сезонам (команда — игрок — лига — сезон)."""
    __tablename__ = "squads"
    __table_args__ = (UniqueConstraint("team_id", "player_id", "season"), {"schema": S})

    squad_id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey(f"{S}.teams.team_id"), nullable=False)
    player_id = Column(Integer, ForeignKey(f"{S}.players.player_id"), nullable=False)
    league = Column(String(100), nullable=False)
    season = Column(String(10), nullable=False)     # '2023/24'
    market_value = Column(Float)                    # млн € на странице состава того сезона


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (UniqueConstraint("player_id", "tm_transfer_id"), {"schema": S})

    transfer_id = Column(Integer, primary_key=True)
    tm_transfer_id = Column(String(30), nullable=False)
    player_id = Column(Integer, ForeignKey(f"{S}.players.player_id"), nullable=False)
    team_out_id = Column(Integer, ForeignKey(f"{S}.teams.team_id"))
    team_in_id = Column(Integer, ForeignKey(f"{S}.teams.team_id"))
    transfer_date = Column(Date)
    year = Column(Integer)
    season = Column(String(10))
    transfer_fee = Column(Float)                    # млн €, NULL = свободный/аренда/неизвестно
    market_value_at_transfer = Column(Float)        # млн €
    is_upcoming = Column(Boolean, default=False)


class LeagueSeason(Base):
    """Сила лиги в сезоне по стоимости составов (шаг strength)."""
    __tablename__ = "league_seasons"
    __table_args__ = (UniqueConstraint("league", "season"), {"schema": S})

    id = Column(Integer, primary_key=True)
    league = Column(String(100), nullable=False)
    season = Column(String(10), nullable=False)
    avg_team_value = Column(Float)                  # средняя стоимость состава команды, млн €
    strength_coefficient = Column(Float)            # avg_team_value / максимум среди лиг сезона


# ---------------------------------------------------------------------------
# Статистика за сезон. Колонки сгруппированы по источникам — каждый шаг парсера
# обновляет только "свои" колонки, поэтому шаги можно перезапускать независимо.
# ---------------------------------------------------------------------------
PER_MATCH_METRICS = [
    "shots", "shots_on_target", "key_passes", "passes", "passes_completed",
    "forward_passes", "forward_passes_completed", "progressive_passes", "crosses",
    "long_balls", "dribbles", "dribbles_won", "tackles", "tackles_won",
    "interceptions", "clearances", "blocks", "aerials", "aerials_won",
    "ball_recoveries", "fouls", "fouled", "dispossessed", "miscontrols", "touches",
]
P90_METRICS = [
    "shots", "shots_on_target", "key_passes", "passes", "forward_passes",
    "progressive_passes", "dribbles", "tackles", "interceptions", "blocks",
    "clearances", "aerials_won", "dispossessed", "miscontrols",
]
UNDERSTAT_P90 = ["xg", "npxg", "xa", "xg_chain", "xg_buildup"]
PCT_METRICS = [
    "pass_completion_pct", "forward_pass_completion_pct", "dribble_success_pct",
    "tackles_won_pct", "aerials_won_pct", "duels_won_pct",
]


class PlayerStat(Base):
    __tablename__ = "player_stats"
    __table_args__ = (UniqueConstraint("player_id", "team_id", "league", "season"), {"schema": S})

    stat_id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey(f"{S}.players.player_id"), nullable=False)
    team_id = Column(Integer, ForeignKey(f"{S}.teams.team_id"), nullable=False)
    league = Column(String(100), nullable=False)
    season = Column(String(10), nullable=False)
    age_at_season = Column(Integer)

    # Игровое время
    appearances = Column(Integer)
    matches_started = Column(Integer)
    matches_subbed = Column(Integer)
    minutes_played = Column(Integer)

    # Базовые показатели (итого за сезон)
    goals = Column(Integer)
    assists = Column(Integer)
    yellow_cards = Column(Integer)
    red_cards = Column(Integer)
    penalties_scored = Column(Integer)
    penalties_attempted = Column(Integer)

    # Максимальная скорость, км/ч.
    # max_speed — только реальный замер (трекинг лиги, сейчас bundesliga.com), иначе NULL;
    # max_speed_est — оценка по рейтингам EA FC через калибровочную модель (шаг speed_model)
    max_speed = Column(Float)
    max_speed_source = Column(String(50))
    max_speed_est = Column(Float)

    # Understat (итого за сезон)
    xg = Column(Float)
    npxg = Column(Float)
    xa = Column(Float)
    xg_chain = Column(Float)
    xg_buildup = Column(Float)

    # Командный контекст
    team_possession_share = Column(Float)
    league_strength_coefficient = Column(Float)

    data_sources = Column(String(100))   # напр. 'transfermarkt,understat,whoscored'
    feature_vector = Column(Vector(VECTOR_DIM))   # признаки — config.VECTOR_FEATURES
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# Динамически добавляем однотипные колонки (чтобы не писать 60 строк вручную)
for _m in ["goals", "assists", "xg", "xa"] + PER_MATCH_METRICS:
    setattr(PlayerStat, f"{_m}_per_match", Column(Float))
for _m in P90_METRICS + UNDERSTAT_P90:
    setattr(PlayerStat, f"{_m}_p90", Column(Float))
for _m in PCT_METRICS:
    setattr(PlayerStat, _m, Column(Float))

STAT_COLUMNS = {c.name for c in PlayerStat.__table__.columns}


# ---------------------------------------------------------------------------
# Рейтинги EA FC (sofifa.com): экспертные оценки 0–99, не замеры.
# Одна строка = игрок в издании игры, соответствующем сезону (FC 24 -> 2023/24).
# ---------------------------------------------------------------------------
RATING_COLUMNS = [
    "overall_rating", "potential", "acceleration", "sprint_speed", "agility", "balance",
    "stamina", "strength", "jumping", "reactions", "dribbling", "ball_control",
    "short_passing", "long_passing", "vision", "crossing", "finishing", "shot_power",
    "long_shots", "positioning", "interceptions", "defensive_awareness",
    "standing_tackle", "sliding_tackle", "aggression", "heading_accuracy", "composure",
]


class PlayerRating(Base):
    __tablename__ = "player_ratings"
    __table_args__ = (UniqueConstraint("player_id", "season"), {"schema": S})

    rating_id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey(f"{S}.players.player_id"), nullable=False)
    season = Column(String(10), nullable=False)
    sofifa_id = Column(Integer, nullable=False)
    fifa_edition = Column(String(20))             # 'FC 24'
    version_id = Column(Integer)                  # обновление базы sofifa
    pace = Column(Integer)                        # как в EA: 0.45*acceleration + 0.55*sprint_speed
    created_at = Column(DateTime, server_default=func.now())


for _m in RATING_COLUMNS:
    setattr(PlayerRating, _m, Column(Integer))


class SpeedModel(Base):
    """Журнал обучения калибровочной модели 'рейтинги EA FC -> максимальная скорость'."""
    __tablename__ = "speed_models"
    __table_args__ = {"schema": S}

    model_id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    algorithm = Column(String(50))
    features = Column(Text)                       # JSON-список признаков
    coefficients = Column(Text)                   # JSON {признак: коэффициент}
    intercept = Column(Float)
    n_train = Column(Integer)
    r2_cv = Column(Float)                         # 5-кратная кросс-валидация
    mae_cv = Column(Float)                        # км/ч
    rmse_cv = Column(Float)                       # км/ч
    n_predicted = Column(Integer)
