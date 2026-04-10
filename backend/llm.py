"""
LLM chat integration for FinAlly.

Exposes a narrow interface for the chat route:
    - ``LLMResponse`` / ``TradeInstruction`` / ``WatchlistChange`` — structured
      output schema returned by the LLM (and by the mock).
    - ``call_llm(messages)`` — call LiteLLM via OpenRouter with Cerebras as the
      inference provider, using structured outputs.
    - ``mock_response(user_message)`` — deterministic, rule-based mock used by
      tests and by ``LLM_MOCK=true`` mode.
    - ``get_llm_response(user_message, messages=None)`` — high-level entry point
      that checks ``LLM_MOCK`` and routes to the mock or the real LLM.

See ``planning/PLAN.md`` section 9 for the spec.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

# Load OPENROUTER_API_KEY (and friends) from the project-root .env file.
load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# LiteLLM / OpenRouter / Cerebras configuration — see .claude/skills/cerebras
MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

class TradeInstruction(BaseModel):
    """A single trade the LLM wants to execute on the user's behalf."""

    ticker: str
    side: str  # "buy" or "sell"
    quantity: float


class WatchlistChange(BaseModel):
    """A single watchlist add/remove the LLM wants to apply."""

    ticker: str
    action: str  # "add" or "remove"


class LLMResponse(BaseModel):
    """Top-level structured response returned by the assistant."""

    message: str
    trades: list[TradeInstruction] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Real LLM call
# ---------------------------------------------------------------------------

def call_llm(messages: list[dict]) -> LLMResponse:
    """
    Call the LLM via LiteLLM / OpenRouter / Cerebras with structured outputs.

    ``messages`` is a standard OpenAI-style list of ``{"role", "content"}``
    dicts. Returns an ``LLMResponse`` on success; on any failure (network,
    invalid JSON, validation) returns a safe fallback ``LLMResponse`` with an
    error message and no actions.
    """
    # Imported lazily so that importing this module (and running the mock) does
    # not require litellm to be installed or network access to be available.
    from litellm import completion

    try:
        response = completion(
            model=MODEL,
            messages=messages,
            response_format=LLMResponse,
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned empty content")
        return LLMResponse.model_validate_json(content)
    except ValidationError as exc:
        logger.exception("LLM response failed schema validation")
        return LLMResponse(
            message=(
                "Sorry — I had trouble formatting a response. "
                f"({exc.error_count()} validation errors.)"
            )
        )
    except Exception as exc:  # noqa: BLE001 — we want a safe fallback
        logger.exception("LLM call failed")
        return LLMResponse(
            message=f"Sorry — the assistant is unavailable right now ({exc})."
        )


# ---------------------------------------------------------------------------
# Deterministic mock (LLM_MOCK=true)
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def _first_ticker(text: str, default: str = "AAPL") -> str:
    """
    Return the first ticker-looking uppercase token in ``text``, or ``default``.

    We scan the *original* (not lowercased) text so genuine tickers stand out.
    Stopwords that happen to look like tickers (I, A, AN, THE, ...) are
    filtered out. This is intentionally permissive — the mock only needs to be
    good enough for deterministic tests.
    """
    stopwords = {
        "I", "A", "AN", "THE", "TO", "OF", "IN", "ON", "AT", "IS", "IT",
        "BUY", "SELL", "ADD", "REMOVE", "ME", "MY", "YOU", "WE", "US",
        "AND", "OR", "FOR", "IF", "SO", "DO", "BE", "BY",
    }
    for match in _TICKER_RE.finditer(text):
        token = match.group(1)
        if token not in stopwords:
            return token
    return default


def mock_response(user_message: str) -> LLMResponse:
    """
    Deterministic rule-based LLM mock.

    Rules are evaluated in order against the *lowercased* user message:

    1. "buy"            → buy 1 share of the first ticker (default AAPL)
    2. "sell"           → sell 1 share of the first ticker (default AAPL)
    3. "add" + ticker   → add that ticker to the watchlist
    4. "remove" + ticker → remove that ticker from the watchlist
    5. anything else    → message only, no trades/changes

    See ``planning/PLAN.md`` section 9 ("LLM Mock Mode") for the spec.
    """
    lowered = user_message.lower()

    if "buy" in lowered:
        ticker = _first_ticker(user_message)
        return LLMResponse(
            message=f"Mock mode: buy 1 {ticker}.",
            trades=[TradeInstruction(ticker=ticker, side="buy", quantity=1)],
        )

    if "sell" in lowered:
        ticker = _first_ticker(user_message)
        return LLMResponse(
            message=f"Mock mode: sell 1 {ticker}.",
            trades=[TradeInstruction(ticker=ticker, side="sell", quantity=1)],
        )

    if "add" in lowered:
        ticker = _first_ticker(user_message)
        return LLMResponse(
            message=f"Mock mode: add {ticker} to watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="add")],
        )

    if "remove" in lowered:
        ticker = _first_ticker(user_message)
        return LLMResponse(
            message=f"Mock mode: remove {ticker} from watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="remove")],
        )

    return LLMResponse(message="Mock mode: no action taken.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _is_mock_mode() -> bool:
    return os.getenv("LLM_MOCK", "false").lower() == "true"


def get_llm_response(
    user_message: str,
    messages: Optional[list[dict]] = None,
) -> LLMResponse:
    """
    High-level entry point used by the chat route.

    If ``LLM_MOCK=true`` in the environment, returns a deterministic mock
    response based solely on ``user_message``. Otherwise calls the real LLM
    with ``messages`` (which the caller should build with system prompt,
    portfolio context, chat history, and the new user message).
    """
    if _is_mock_mode():
        return mock_response(user_message)

    if messages is None:
        messages = [{"role": "user", "content": user_message}]
    return call_llm(messages)
