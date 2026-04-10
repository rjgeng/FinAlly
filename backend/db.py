"""
SQLite persistence layer for FinAlly.

All application state lives here: user profile, watchlist, positions, trades,
portfolio snapshots, and chat message history. The module uses the stdlib
``sqlite3`` driver with a single module-level connection in WAL mode so that
background tasks and request handlers can read concurrently.

The database file lives at ``<repo_root>/db/finally.db`` by default. Tests can
override the location by assigning to :data:`DB_PATH` before calling
:func:`init_db` (or via ``monkeypatch``).

Public functions all default to ``user_id="default"`` because v1 is single-user
and the ``user_id`` column is kept only to preserve a future multi-user path.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH: Path = Path(__file__).parent.parent / "db" / "finally.db"

DEFAULT_USER_ID = "default"
DEFAULT_CASH_BALANCE = 10000.0
DEFAULT_WATCHLIST = [
    "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
    "NVDA", "META", "JPM", "V", "NFLX",
]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY,
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id  TEXT NOT NULL,
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS positions (
    user_id    TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    actions    TEXT,
    created_at TEXT NOT NULL
);
"""


class InsufficientCashError(ValueError):
    """Raised when a buy would drive cash below zero."""


class OversellError(ValueError):
    """Raised when a sell would drive a position below zero."""


# Connection state ------------------------------------------------------------
# We keep a single module-level connection guarded by a lock. SQLite's WAL mode
# allows concurrent readers alongside a single writer, which is all FinAlly
# needs: the SSE background task reads, and request handlers read/write.

_conn: sqlite3.Connection | None = None
_conn_path: Path | str | None = None
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path | str) -> sqlite3.Connection:
    # ``check_same_thread=False`` lets the background market task and request
    # threads share the connection; the module-level lock serialises writes.
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL is skipped for ``:memory:`` since it has no on-disk journal.
    if str(path) != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
    return conn


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    with _lock:
        # If DB_PATH changed since the last connect (e.g. tests pointed it at a
        # tmp file), drop the stale connection and reopen.
        if _conn is None or _conn_path != DB_PATH:
            if _conn is not None:
                try:
                    _conn.close()
                except sqlite3.Error:
                    pass
            path = DB_PATH
            if isinstance(path, Path) and str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            _conn = _connect(path)
            _conn_path = path
        return _conn


def close_db() -> None:
    """Close the module-level connection. Safe to call multiple times."""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None
                _conn_path = None


# Init / seed -----------------------------------------------------------------

def init_db() -> None:
    """Create the schema if missing and seed default data if empty."""
    with _lock:
        conn = _get_conn()
        conn.executescript(_SCHEMA_SQL)

        # Seed default user profile if missing.
        row = conn.execute(
            "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                (DEFAULT_USER_ID, DEFAULT_CASH_BALANCE, _now()),
            )

        # Seed default watchlist only when the user has no entries at all.
        existing = conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE user_id = ?",
            (DEFAULT_USER_ID,),
        ).fetchone()[0]
        if existing == 0:
            now = _now()
            conn.executemany(
                "INSERT INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
                [(DEFAULT_USER_ID, t, now) for t in DEFAULT_WATCHLIST],
            )


# Watchlist -------------------------------------------------------------------

def get_watchlist_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, ticker",
            (user_id,),
        ).fetchall()
        return [r["ticker"] for r in rows]


def add_watchlist_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Add a ticker to the watchlist.

    Returns True if the ticker was newly inserted, False if it was already
    present for this user.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        raise ValueError("ticker must be a non-empty string")
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
                (user_id, ticker, _now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_watchlist_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Remove a ticker from the watchlist. Returns True if a row was deleted."""
    ticker = ticker.upper().strip()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        )
        return cur.rowcount > 0


# Cash / positions ------------------------------------------------------------

def get_cash_balance(user_id: str = DEFAULT_USER_ID) -> float:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"user_id {user_id!r} not found in users_profile")
        return float(row["cash_balance"])


def get_positions(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at "
            "FROM positions WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
        return [
            {
                "ticker": r["ticker"],
                "quantity": float(r["quantity"]),
                "avg_cost": float(r["avg_cost"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


def execute_trade(
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Execute a buy or sell trade atomically.

    - ``buy``  debits cash and increases position; ``avg_cost`` is the running
      weighted average of all buys.
    - ``sell`` credits cash and decreases position; when quantity reaches zero
      the position row is deleted.

    Returns a dict describing the outcome. On validation failure (unknown side,
    non-positive quantity/price, insufficient cash, oversell) returns
    ``{"success": False, "executed_quantity": 0, "executed_price": price, "error": "..."}``
    and makes no database changes.
    """
    ticker = ticker.upper().strip()
    side = side.lower().strip()

    if side not in ("buy", "sell"):
        return _trade_failure(price, f"invalid side: {side!r}")
    if quantity <= 0:
        return _trade_failure(price, "quantity must be positive")
    if price <= 0:
        return _trade_failure(price, "price must be positive")
    if not ticker:
        return _trade_failure(price, "ticker must be a non-empty string")

    cost = quantity * price

    with _lock:
        conn = _get_conn()
        try:
            conn.execute("BEGIN")

            cash_row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
            if cash_row is None:
                conn.execute("ROLLBACK")
                return _trade_failure(price, f"user_id {user_id!r} not found")
            cash = float(cash_row["cash_balance"])

            pos_row = conn.execute(
                "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
                (user_id, ticker),
            ).fetchone()
            current_qty = float(pos_row["quantity"]) if pos_row else 0.0
            current_avg = float(pos_row["avg_cost"]) if pos_row else 0.0

            now = _now()

            if side == "buy":
                if cost > cash + 1e-9:
                    conn.execute("ROLLBACK")
                    return _trade_failure(
                        price,
                        f"insufficient cash: need {cost:.2f}, have {cash:.2f}",
                    )
                new_cash = cash - cost
                new_qty = current_qty + quantity
                # Weighted-average cost over all buys.
                if current_qty > 0:
                    new_avg = (
                        (current_qty * current_avg) + (quantity * price)
                    ) / new_qty
                else:
                    new_avg = price

                conn.execute(
                    "UPDATE users_profile SET cash_balance = ? WHERE id = ?",
                    (new_cash, user_id),
                )
                if pos_row is None:
                    conn.execute(
                        "INSERT INTO positions (user_id, ticker, quantity, avg_cost, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (user_id, ticker, new_qty, new_avg, now),
                    )
                else:
                    conn.execute(
                        "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                        "WHERE user_id = ? AND ticker = ?",
                        (new_qty, new_avg, now, user_id, ticker),
                    )
            else:  # sell
                if quantity > current_qty + 1e-9:
                    conn.execute("ROLLBACK")
                    return _trade_failure(
                        price,
                        f"oversell: trying to sell {quantity} but only hold {current_qty}",
                    )
                new_cash = cash + cost
                new_qty = current_qty - quantity

                conn.execute(
                    "UPDATE users_profile SET cash_balance = ? WHERE id = ?",
                    (new_cash, user_id),
                )
                # Treat values within 1e-9 as exactly zero to avoid ghost positions.
                if abs(new_qty) <= 1e-9:
                    conn.execute(
                        "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
                        (user_id, ticker),
                    )
                else:
                    # Avg cost is unchanged on sell.
                    conn.execute(
                        "UPDATE positions SET quantity = ?, updated_at = ? "
                        "WHERE user_id = ? AND ticker = ?",
                        (new_qty, now, user_id, ticker),
                    )

            # Append to trade log.
            conn.execute(
                "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, ticker, side, quantity, price, now),
            )

            conn.execute("COMMIT")
        except sqlite3.Error as e:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return _trade_failure(price, f"database error: {e}")

    return {
        "success": True,
        "executed_quantity": quantity,
        "executed_price": price,
        "error": None,
    }


def _trade_failure(price: float, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "executed_quantity": 0.0,
        "executed_price": price,
        "error": error,
    }


# Portfolio snapshots ---------------------------------------------------------

def record_portfolio_snapshot(
    total_value: float, user_id: str = DEFAULT_USER_ID
) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, total_value, _now()),
        )


def get_portfolio_snapshots(
    user_id: str = DEFAULT_USER_ID, limit: int = 100
) -> list[dict[str, Any]]:
    """Return the most recent snapshots in chronological (oldest-first) order."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots "
            "WHERE user_id = ? ORDER BY recorded_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        # Oldest-first is nicer for plotting.
        return [
            {"total_value": float(r["total_value"]), "recorded_at": r["recorded_at"]}
            for r in reversed(rows)
        ]


# Chat messages ---------------------------------------------------------------

def add_chat_message(
    role: str,
    content: str,
    actions: Any = None,
    user_id: str = DEFAULT_USER_ID,
) -> str:
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role!r}")
    message_id = str(uuid.uuid4())
    actions_json = json.dumps(actions) if actions is not None else None
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, user_id, role, content, actions_json, _now()),
        )
    return message_id


def get_chat_history(
    user_id: str = DEFAULT_USER_ID, limit: int = 20
) -> list[dict[str, Any]]:
    """Return the most recent chat messages in chronological (oldest-first) order."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "actions": json.loads(r["actions"]) if r["actions"] else None,
                "created_at": r["created_at"],
            }
            for r in reversed(rows)
        ]
