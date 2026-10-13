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

    # Fresh install / fresh sandbox: the sqlite file's folder may not exist yet.
    db_file = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_file and db_file != ":memory:":
        from pathlib import Path

        Path(db_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
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

    if "plays" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("plays")}
        if "related" not in cols:
            statements.append("ALTER TABLE plays ADD COLUMN related JSON DEFAULT '[]'")
        if "bench" not in cols:
            statements.append("ALTER TABLE plays ADD COLUMN bench JSON DEFAULT '[]'")

    if statements:
        with engine.begin() as connection:
            for stmt in statements:
                connection.execute(text(stmt))

    _add_missing_columns()


def _add_missing_columns() -> None:
    """Generic forward migration: add any model column an older DB lacks.

    ``create_all`` creates new tables but never alters existing ones, so a DB
    from an older checkout would 500 on the first query touching a new column.
    Columns are added nullable with their scalar default (SQLite can't add NOT
    NULL without one). Failures are logged, not fatal."""
    import logging

    log = logging.getLogger(__name__)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have or column.primary_key:
                continue
            col_type = column.type.compile(dialect=engine.dialect)
            stmt = f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {preparer.quote(column.name)} {col_type}"
            default = getattr(column.default, "arg", None)
            if isinstance(default, bool):
                stmt += f" DEFAULT {1 if default else 0}"
            elif isinstance(default, (int, float)):
                stmt += f" DEFAULT {default}"
            elif isinstance(default, str):
                stmt += " DEFAULT '" + default.replace("'", "''") + "'"
            try:
                with engine.begin() as connection:
                    connection.execute(text(stmt))
                log.info("Migrated: added %s.%s", table.name, column.name)
            except Exception:  # noqa: BLE001
                log.warning("Could not add column %s.%s", table.name, column.name, exc_info=True)
