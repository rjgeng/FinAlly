"""
Unit tests for the market data factory.

These tests cover:
- create_provider returns SimulatorProvider when MASSIVE_API_KEY is absent
- create_provider returns SimulatorProvider when MASSIVE_API_KEY is empty
- create_provider returns MassiveProvider when MASSIVE_API_KEY is set
- The get_watchlist callable is forwarded to the created provider
"""
import os
from unittest.mock import patch

from backend.market.factory import create_provider
from backend.market.simulator import SimulatorProvider
from backend.market.massive_provider import MassiveProvider


def _watchlist() -> list[str]:
    return ["AAPL", "TSLA"]


def test_returns_simulator_when_no_api_key():
    env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, SimulatorProvider)


def test_returns_simulator_when_api_key_empty_string():
    with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=False):
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, SimulatorProvider)


def test_returns_simulator_when_api_key_whitespace_only():
    with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=False):
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, SimulatorProvider)


def test_returns_massive_provider_when_api_key_set():
    with patch.dict(os.environ, {"MASSIVE_API_KEY": "real-key-123"}, clear=False):
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, MassiveProvider)


def test_simulator_receives_get_watchlist_callable():
    """The get_watchlist callable must be forwarded to SimulatorProvider."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MASSIVE_API_KEY", None)
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, SimulatorProvider)
    assert provider._get_watchlist() == ["AAPL", "TSLA"]


def test_massive_receives_get_watchlist_callable():
    """The get_watchlist callable must be forwarded to MassiveProvider."""
    with patch.dict(os.environ, {"MASSIVE_API_KEY": "key-abc"}, clear=False):
        provider = create_provider(get_watchlist=_watchlist)
    assert isinstance(provider, MassiveProvider)
    assert provider._get_watchlist() == ["AAPL", "TSLA"]
