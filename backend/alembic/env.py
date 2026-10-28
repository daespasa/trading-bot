from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings
from app.database.models import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

URL = context.config.get_main_option("sqlalchemy.url") or f"sqlite:///{settings.db_path}"


def run_migrations_online():
    with create_engine(URL).connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(url=URL, target_metadata=Base.metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
