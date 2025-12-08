"""Helper to run alembic migrations programmatically."""
import os
from alembic import command
from alembic.config import Config


def run_migrations():
    here = os.path.dirname(__file__)
    alembic_cfg = Config(os.path.join(here, '..', 'alembic.ini'))
    alembic_cfg.set_main_option('script_location', os.path.join(here, '..', 'alembic'))
    # If DATABASE_URL is set, alembic env.py will use it.
    command.upgrade(alembic_cfg, 'head')


if __name__ == '__main__':
    run_migrations()
