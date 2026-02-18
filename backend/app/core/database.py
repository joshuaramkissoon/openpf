from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()


def _run_lightweight_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    statements: list[str] = []

    if "account_snapshots" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("account_snapshots")}
        if "account_kind" not in cols:
            statements.append("ALTER TABLE account_snapshots ADD COLUMN account_kind VARCHAR(24) DEFAULT 'invest'")

    if "position_snapshots" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("position_snapshots")}
        if "account_kind" not in cols:
            statements.append("ALTER TABLE position_snapshots ADD COLUMN account_kind VARCHAR(24) DEFAULT 'invest'")
        if "total_cost" not in cols:
            statements.append("ALTER TABLE position_snapshots ADD COLUMN total_cost FLOAT DEFAULT 0")

    if "chat_messages" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("chat_messages")}
        if "tool_calls" not in cols:
            statements.append("ALTER TABLE chat_messages ADD COLUMN tool_calls JSON DEFAULT NULL")

    if not statements:
        return

    with engine.begin() as connection:
        for stmt in statements:
            connection.execute(text(stmt))
