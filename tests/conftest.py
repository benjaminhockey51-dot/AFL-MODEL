from __future__ import annotations

import sqlalchemy as sa
import pytest
from sqlalchemy.orm import sessionmaker

import afl_model.db.connection as connection
from afl_model.db.models import Base


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite database per test — never touches data/afl.db."""
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine, monkeypatch):
    """A Session bound to db_engine, wired in as the app's session factory
    so code that calls afl_model.db.connection.get_session() transparently
    uses the in-memory test database instead of the real one.
    """
    session_factory = sessionmaker(bind=db_engine, future=True)
    monkeypatch.setattr(connection, "_engine", db_engine)
    monkeypatch.setattr(connection, "_SessionFactory", session_factory)

    session = session_factory()
    yield session
    session.close()
