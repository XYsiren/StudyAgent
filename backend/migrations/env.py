from alembic import context


def run(connection):
    context.configure(connection=connection, target_metadata=None, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is None:
    from app.config import get_settings
    from sqlalchemy import create_engine
    with create_engine(get_settings().database_url).connect() as connection:
        run(connection)
else:
    run(connection)
