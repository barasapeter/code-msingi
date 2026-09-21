from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Provide one database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Apply the small SQLite schema upgrades needed by this starter project.

    `Base.metadata.create_all()` only creates missing tables; it never adds columns
    to an existing table. Keep production schema changes in Alembic migrations.
    """
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    if "ebooks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("ebooks")}
    upgrades = {
        "details": "TEXT",
        "pdf_path": "VARCHAR(500)",
        "thumbnail_path": "VARCHAR(500)",
        "uploader_email": "VARCHAR(320)",
        "discount_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "discount_amount": "FLOAT NOT NULL DEFAULT 0",
        "discount_ends_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, sql_type in upgrades.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE ebooks ADD COLUMN {name} {sql_type}"))
        connection.execute(
            text("UPDATE ebooks SET uploader_email = :master WHERE uploader_email IS NULL OR uploader_email = ''"),
            {"master": settings.master_admin_email},
        )
        payment_columns = {column["name"] for column in inspector.get_columns("mpesa_payments")} if "mpesa_payments" in inspector.get_table_names() else set()
        if "mpesa_payments" in inspector.get_table_names() and "amount" not in payment_columns:
            connection.execute(text("ALTER TABLE mpesa_payments ADD COLUMN amount FLOAT"))
