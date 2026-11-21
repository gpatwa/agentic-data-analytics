"""The live wiring for the Databricks draft driver: refuses without --allow-live and without the environment, takes no secret as an argument, adapts the CLI session."""

from __future__ import annotations

import pytest
from reference_stack import draft_live as dl


def test_it_refuses_without_allow_live(capsys):
    assert dl.main(["--profile", "p", "--warehouse-name", "w", "--catalog", "c", "--schema", "s", "--tables", "t"]) == 2
    assert "--allow-live" in capsys.readouterr().err


def test_it_refuses_without_the_model_environment_and_names_only_the_variables(monkeypatch):
    for k in dl.REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as exc:
        dl.model_from_environment()
    text = str(exc.value)
    assert all(k in text for k in dl.REQUIRED) and "refused" in text


def test_no_secret_can_be_passed_as_an_argument():
    import argparse

    import reference_stack.draft_live as mod

    with pytest.raises(SystemExit):
        mod.main(["--allow-live", "--api-key", "x", "--profile", "p", "--warehouse-name", "w", "--catalog", "c", "--schema", "s", "--tables", "t"])
    assert argparse.ArgumentParser  # an unknown option is an argparse error, not a silent accept


def test_the_adapter_gives_a_stream_the_sources_can_read_in_batches():
    class FakeCli:
        def query(self, sql):
            return [(1,), (2,), (3,)]

    stream = dl.CliReadSession(FakeCli()).execute("SELECT 1", {})
    assert stream.fetchmany(2) == [(1,), (2,)] and stream.fetchmany(2) == [(3,)] and stream.fetchmany(2) == []


def test_the_model_is_built_from_the_environment_only(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k" * 16)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "dep")
    client, key = dl.model_from_environment()
    assert key == "k" * 16 and client is not None
