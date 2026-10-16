from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base


class Database:
    def __init__(self, settings: Settings):
        url = make_url(settings.database_url)
        kwargs = {"pool_pre_ping": True}
        if url.drivername.startswith("sqlite"):
            if url.database and url.database != ":memory:":
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if url.database == ":memory:":
                from sqlalchemy.pool import StaticPool
                kwargs["poolclass"] = StaticPool
        elif url.drivername.startswith("postgresql"):
            kwargs["connect_args"] = {"connect_timeout": 10}
        self.engine = create_engine(settings.database_url, **kwargs)
        if url.drivername.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with self.session_factory() as session:
            try:
                yield session
            except BaseException:
                session.rollback()
                raise
