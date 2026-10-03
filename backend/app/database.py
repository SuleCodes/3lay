from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# All of 3lay's tables live in their own Postgres schema rather than `public`.
# Supabase automatically exposes `public` through its REST API using keys that
# are safe to ship to browsers, so keeping users and API key hashes out of it
# means they can't be reached that way.
DB_SCHEMA = "app"

engine = create_engine(
    settings.database_url,
    # Poolers (Supabase's Supavisor included) close idle connections; check a
    # connection is alive before handing it out instead of failing a request.
    pool_pre_ping=True,
    # psycopg prepares frequently-run statements server-side, which breaks
    # behind a transaction-mode pooler (port 6543). Disabling it costs little
    # and keeps both the session and transaction poolers working.
    connect_args={"prepare_threshold": None},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
