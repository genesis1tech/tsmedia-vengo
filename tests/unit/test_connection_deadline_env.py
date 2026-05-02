from __future__ import annotations

from tsv6.core.production_main import env_bool, env_int


def test_env_bool_defaults_when_missing_or_invalid(monkeypatch):
    monkeypatch.delenv("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", raising=False)
    assert env_bool("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", True) is True

    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", "maybe")
    assert env_bool("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", False) is False


def test_env_bool_parses_common_true_false_values(monkeypatch):
    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", "false")
    assert env_bool("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", True) is False

    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", "YES")
    assert env_bool("TSV6_CONNECTION_DEADLINE_FORCE_REBOOT", False) is True


def test_env_int_defaults_when_missing_invalid_or_below_minimum(monkeypatch):
    monkeypatch.delenv("TSV6_CONNECTION_DEADLINE_MINUTES", raising=False)
    assert env_int("TSV6_CONNECTION_DEADLINE_MINUTES", 30, minimum=1) == 30

    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_MINUTES", "bad")
    assert env_int("TSV6_CONNECTION_DEADLINE_MINUTES", 30, minimum=1) == 30

    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_MINUTES", "0")
    assert env_int("TSV6_CONNECTION_DEADLINE_MINUTES", 30, minimum=1) == 30


def test_env_int_parses_valid_value(monkeypatch):
    monkeypatch.setenv("TSV6_CONNECTION_DEADLINE_MINUTES", "45")
    assert env_int("TSV6_CONNECTION_DEADLINE_MINUTES", 30, minimum=1) == 45
