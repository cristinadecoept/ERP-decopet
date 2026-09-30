"""Corre las migraciones de Alembic contra la base que diga el entorno.

La URL nunca se escribe en el historial: sale de DECOPET_DATABASE_URL (o DATABASE_URL),
que en producción es el DSN de Neon y en desarrollo la de docker-compose.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plataforma import modelo  # noqa: E402  (necesita el sys.path de arriba)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = modelo.metadata


def _url():
    u = os.environ.get("DECOPET_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not u:
        raise RuntimeError("Falta DECOPET_DATABASE_URL (o DATABASE_URL) para correr Alembic.")
    if u.startswith("postgres://"):                      # Railway a veces la pasa así
        u = "postgresql://" + u[len("postgres://"):]
    if u.startswith("postgresql://"):                    # que use el driver psycopg3
        u = "postgresql+psycopg://" + u[len("postgresql://"):]
    return u


def run_migrations_offline():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config({"sqlalchemy.url": _url()},
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
