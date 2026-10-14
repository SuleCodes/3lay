"""Test setup: the API against an in-memory SQLite database, without Azure.

Settings come from the environment set here; App Configuration is switched
off, so tests never read the real configuration (backend/.env points at it).
SQLite stands in for Postgres, with the "app" schema as an attached database
and JSONB stored as JSON.
"""

import os

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.update({
    "DATABASE_URL": "postgresql://test:test@localhost/unused",  # never connected to
    "CLIENT_FORWARDING_DOMAIN": "in.3lay.test",
    "JWT_SECRET": "test-secret",
    "FRONTEND_URL": "http://localhost:3000",
})

from app import config  # noqa: E402

config.AppConfigurationSource.__call__ = lambda self: {}  # no Azure in tests
config.get_settings.cache_clear()

from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BoardyConversation, ClientConfiguration, DestinationSecret, User  # noqa: E402
from app.security import create_session_token  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach_app_schema(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS app")

    # (SQLite makes the one-active-version index a plain unique one: one version per client here.)
    tables = [User.__table__, BoardyConversation.__table__, ClientConfiguration.__table__,
              DestinationSecret.__table__]
    User.metadata.create_all(engine, tables=tables)
    session = sessionmaker(bind=engine)()
    session.add_all([User(id="u1", email="ops@rolepay.example", username="rolepay-agent"),
                     User(id="u2", email="ops@other.example", username="other-client"),
                     User(id="u3", email="new@example.com", username=None)])
    session.commit()
    yield session
    session.close()


@pytest.fixture
def api(db_session):
    """A test client; api.sign_in(user_id) sets that user's session cookie."""
    from fastapi.testclient import TestClient  # pylint: disable=import-outside-toplevel

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)

    def sign_in(user_id):
        client.cookies.set(config.get_settings().session_cookie_name,
                           create_session_token(user_id))
        return client

    client.sign_in = sign_in
    yield client
    app.dependency_overrides.clear()
