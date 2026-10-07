from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL, DB_SCHEMA, VECTOR_DIM

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
Base = declarative_base()


# Колонки, появившиеся после первой версии схемы (для баз, созданных раньше)
MIGRATIONS = [
    ("players", "sofifa_id", "INTEGER UNIQUE"),
    ("player_stats", "max_speed_source", "VARCHAR(50)"),
    ("player_stats", "max_speed_est", "FLOAT"),
    ("league_seasons", "avg_team_value", "FLOAT"),
]
# Колонки, которые больше не используются
DROPPED = [
    ("league_seasons", "avg_elo"),   # сила лиги теперь только по стоимости составов (ClubElo убран)
]


def _migrate_vector_dim(conn) -> None:
    """Если набор признаков вектора изменился (config.VECTOR_FEATURES), меняем размерность колонки.
    Старые векторы несовместимы и обнуляются — шаг vectors посчитает их заново."""
    current = conn.execute(text(
        "SELECT atttypmod FROM pg_attribute "
        f"WHERE attrelid = '{DB_SCHEMA}.player_stats'::regclass AND attname = 'feature_vector'")).scalar()
    if current is None or current == VECTOR_DIM:
        return
    conn.execute(text(f"DROP INDEX IF EXISTS {DB_SCHEMA}.player_stats_vec_idx"))
    conn.execute(text(f"ALTER TABLE {DB_SCHEMA}.player_stats "
                      f"ALTER COLUMN feature_vector TYPE vector({VECTOR_DIM}) USING NULL"))


def init_db() -> None:
    """Создаёт схему, расширение pgvector и все таблицы (если их ещё нет)."""
    import models  # noqa: F401  регистрирует модели в Base.metadata

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # create_all не добавляет колонки в уже существующие таблицы — досоздаём вручную
        for table, col, ddl in MIGRATIONS:
            conn.execute(text(f"ALTER TABLE {DB_SCHEMA}.{table} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for table, col in DROPPED:
            conn.execute(text(f"ALTER TABLE {DB_SCHEMA}.{table} DROP COLUMN IF EXISTS {col}"))
        _migrate_vector_dim(conn)
    with engine.begin() as conn:  # индекс для поиска похожих игроков
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS player_stats_vec_idx ON {DB_SCHEMA}.player_stats "
            "USING hnsw (feature_vector vector_cosine_ops)"))
