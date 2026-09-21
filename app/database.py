from collections.abc import Generator
import hashlib
from pathlib import Path

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
        "content_hash": "VARCHAR(64)",
        "uploader_email": "VARCHAR(320)",
        "discount_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "discount_amount": "FLOAT NOT NULL DEFAULT 0",
        "discount_ends_at": "DATETIME",
        "deleted_at": "DATETIME",
        "deleted_by": "VARCHAR(320)",
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
        media_dir = Path(__file__).resolve().parent / "media" / "ebooks"
        rows = list(connection.execute(text("SELECT id, pdf_path, content_hash FROM ebooks")).mappings())
        known_hashes = {row["content_hash"] for row in rows if row["content_hash"]}
        for row in rows:
            if row["content_hash"] or not row["pdf_path"]:
                continue
            pdf_file = media_dir / Path(row["pdf_path"]).name
            if not pdf_file.is_file():
                continue
            digest = hashlib.sha256()
            with pdf_file.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            content_hash = digest.hexdigest()
            if content_hash not in known_hashes:
                connection.execute(text("UPDATE ebooks SET content_hash = :content_hash WHERE id = :id"), {"content_hash": content_hash, "id": row["id"]})
                known_hashes.add(content_hash)
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_ebooks_content_hash ON ebooks(content_hash) WHERE content_hash IS NOT NULL"))
