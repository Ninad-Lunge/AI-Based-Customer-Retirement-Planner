"""
Tests for the typed settings module (settings.py).

Verifies that the pydantic-settings model reproduces the original inline
os.environ parsing semantics exactly (no behaviour change).
"""

import importlib

import pytest


@pytest.fixture
def fresh_settings(monkeypatch):
    """Return a factory that builds a Settings() with a clean cache + env."""
    def _build(env):
        for key in (
            "FLASK_DEBUG", "FLASK_PORT", "FLASK_HOST", "LOG_LEVEL",
            "CORS_ORIGINS", "RISK_FREE_RATE", "LSTM_EPOCHS", "MC_SIMULATIONS",
        ):
            monkeypatch.delenv(key, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        settings_mod = importlib.import_module("settings")
        importlib.reload(settings_mod)
        return settings_mod.Settings(_env_file=None)
    return _build


def test_defaults_match_original(fresh_settings):
    s = fresh_settings({})
    assert s.debug is False
    assert s.port == 5000
    assert s.host == "0.0.0.0"
    assert s.log_level == "INFO"
    assert s.cors_origins == "*"


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True),
     ("0", False), ("false", False), ("no", False), ("", False)],
)
def test_debug_truthiness_matches_original(fresh_settings, raw, expected):
    s = fresh_settings({"FLASK_DEBUG": raw})
    assert s.debug is expected


def test_cors_single_star_is_allow_all(fresh_settings):
    s = fresh_settings({"CORS_ORIGINS": "*"})
    assert s.cors_origins == "*"


def test_cors_comma_list_is_parsed_and_cleaned(fresh_settings):
    s = fresh_settings({"CORS_ORIGINS": " http://a.com , http://b.com ,"})
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_log_level_is_upper_cased(fresh_settings):
    s = fresh_settings({"LOG_LEVEL": "debug"})
    assert s.log_level == "DEBUG"


def test_port_coerced_to_int(fresh_settings):
    s = fresh_settings({"FLASK_PORT": "8080"})
    assert s.port == 8080 and isinstance(s.port, int)
