"""Migrating a database must not leave `sys.path` longer for the rest of the session (see the autouse fixture in conftest.py for why that mattered)."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

SERVICE = Path(__file__).resolve().parent.parent


def test_each_migration_call_does_add_a_path_entry_so_the_fixture_is_needed(tmp_path):
    config = Config(str(SERVICE / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'a.db'}")
    before = len(sys.path)
    command.heads(config)
    command.heads(config)
    assert len(sys.path) >= before + 2  # the cause: alembic prepends '.' every call


def test_the_path_has_no_duplicates_at_the_start_of_the_next_test():
    assert len(sys.path) == len(set(sys.path))
