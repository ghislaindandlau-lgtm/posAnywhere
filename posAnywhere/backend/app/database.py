"""Database engine, session factory and the declarative base.

This is the single place that knows *how* to talk to the database. Every
module obtains a session through the `get_db` FastAPI dependency, which
guarantees the session is always closed after the request finishes.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# PostgreSQL is the runtime database. SQLite is used ONLY by the automated test
# suite, which needs this flag for multi-thread access; it is ignored otherwise.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

# The engine manages the underlying DB connection pool. pool_pre_ping avoids
# handing out stale PostgreSQL connections after idle periods.
engine = create_engine(
    settings.database_url, connect_args=connect_args, pool_pre_ping=True, future=True
)

# SessionLocal() creates a new ORM session bound to the engine above.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Base class that all ORM models inherit from (SQLAlchemy 2.0 style)."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and closes it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Imported by main.py on startup.

    `import app.models` is done locally so SQLAlchemy has registered every
    model class before create_all() runs.
    """
    import app.models  # noqa: F401  (ensures models are registered on Base)

    Base.metadata.create_all(bind=engine)
