from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        url = make_url(settings.database_url)
        kwargs = {"pool_pre_ping": True, "hide_parameters": True}
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
                import math
                connection.create_function("ln", 1, math.log, deterministic=True)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self):
        from alembic import command
        from alembic.config import Config
        config = Config()
        import migrations
        config.set_main_option("script_location", str(Path(migrations.__file__).parent))
        config.set_main_option("path_separator", "os")
        with self.engine.begin() as connection:
            # Serialize startup migrations across API and worker on PostgreSQL.
            if connection.dialect.name == "postgresql":
                from sqlalchemy import text
                connection.execute(text("SELECT pg_advisory_xact_lock(723890213)"))
            config.attributes["connection"] = connection
            if self.settings.auto_migrate:
                command.upgrade(config, "head")
            else:
                from alembic.migration import MigrationContext
                from alembic.script import ScriptDirectory
                if MigrationContext.configure(connection).get_current_revision() != ScriptDirectory.from_config(config).get_current_head():
                    raise RuntimeError("Database migration required: python -m app.maintenance migrate")

    @contextmanager
    def session(self):
        with self.session_factory() as session:
            try:
                yield session
            except BaseException:
                session.rollback()
                raise
