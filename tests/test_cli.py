import argparse
import csv
import json
import sys
from unittest.mock import MagicMock

import pytest

from simplifiapi import cli


# --- parse_arguments ---------------------------------------------------------

def test_parse_arguments_defaults():
    options = cli.parse_arguments([])

    assert options.email is None
    assert options.password is None
    assert options.token is None
    assert options.no_cache is False
    assert options.logout is False
    assert options.accounts is False
    assert options.filename == "output"
    assert options.format == cli.JSON_FORMAT


def test_parse_arguments_reads_flags_and_options():
    options = cli.parse_arguments([
        "--email", "a@b.com",
        "--accounts", "--transactions",
        "--format", "csv",
        "--filename", "out",
        "--no-cache",
    ])

    assert options.email == "a@b.com"
    assert options.accounts is True
    assert options.transactions is True
    assert options.format == "csv"
    assert options.filename == "out"
    assert options.no_cache is True


# --- write_data ----------------------------------------------------------

def test_write_data_writes_indented_json(tmp_path):
    options = argparse.Namespace(filename=str(tmp_path / "out"), format=cli.JSON_FORMAT)

    cli.write_data(options, [{"id": "1"}], "accounts")

    written = json.loads((tmp_path / "out_accounts.json").read_text())
    assert written == [{"id": "1"}]


def test_write_data_writes_csv(tmp_path):
    options = argparse.Namespace(filename=str(tmp_path / "out"), format=cli.CSV_FORMAT)

    cli.write_data(options, [{"id": "1", "name": "Checking"}], "accounts")

    rows = list(csv.DictReader((tmp_path / "out_accounts.csv").open()))
    assert rows == [{"id": "1", "name": "Checking"}]


# --- main: login/cache orchestration ----------------------------------------

@pytest.fixture
def fake_client(monkeypatch):
    """Stands in for cli.Client so main()'s login/caching logic can be
    tested without hitting the network."""
    instance = MagicMock()
    instance.get_datasets.return_value = [{"id": "dataset-1"}]
    monkeypatch.setattr(cli, "Client", MagicMock(return_value=instance))
    return instance


def run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["simplifiapi"] + argv)
    cli.main()


def test_main_reuses_a_valid_cached_token(monkeypatch, fake_client):
    fake_client.load_token.return_value = "cached-token"
    fake_client.verify_token.return_value = True

    run_main(monkeypatch, [])

    fake_client.get_token.assert_not_called()
    fake_client.save_token.assert_called_once_with("cached-token")


def test_main_reauthenticates_when_the_cached_token_is_invalid(monkeypatch, fake_client):
    fake_client.load_token.return_value = "stale-token"
    fake_client.verify_token.side_effect = [False, True]
    fake_client.get_token.return_value = "fresh-token"

    run_main(monkeypatch, ["--email", "a@b.com", "--password", "secret"])

    fake_client.get_token.assert_called_once_with(email="a@b.com", password="secret")
    fake_client.save_token.assert_called_once_with("fresh-token")


def test_main_no_cache_skips_reading_and_writing_the_cached_token(monkeypatch, fake_client):
    fake_client.verify_token.return_value = True
    fake_client.get_token.return_value = "fresh-token"

    run_main(monkeypatch, ["--no-cache", "--email", "a@b.com", "--password", "secret"])

    fake_client.load_token.assert_not_called()
    fake_client.save_token.assert_not_called()


def test_main_logout_clears_the_token_and_exits_before_logging_in(monkeypatch, fake_client):
    run_main(monkeypatch, ["--logout"])

    fake_client.clear_token.assert_called_once()
    fake_client.load_token.assert_not_called()
    fake_client.get_token.assert_not_called()
    fake_client.get_datasets.assert_not_called()
