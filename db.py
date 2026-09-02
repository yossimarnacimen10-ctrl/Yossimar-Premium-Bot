import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone

class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _init(self):
        with self._lock, self._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    identifier TEXT NOT NULL,
                    service_id INTEGER NOT NULL,
                    cost INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    raw_response TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS balance_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            ''')

    def ensure_user(self, telegram_id: int, username: str | None, first_name: str | None):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                '''INSERT INTO users (telegram_id, username, first_name, balance, created_at, updated_at)
                   VALUES (?, ?, ?, 0, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                     username=excluded.username,
                     first_name=excluded.first_name,
                     updated_at=excluded.updated_at''',
                (telegram_id, username, first_name, now, now)
            )

    def get_balance(self, telegram_id: int) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            return int(row["balance"]) if row else 0

    def change_balance(self, telegram_id: int, amount: int, reason: str) -> int:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            current = int(row["balance"]) if row else 0
            if not row:
                conn.execute(
                    "INSERT INTO users (telegram_id, username, first_name, balance, created_at, updated_at) VALUES (?, NULL, NULL, 0, ?, ?)",
                    (telegram_id, now, now),
                )
            new_balance = current + amount
            if new_balance < 0:
                raise ValueError("Saldo insuficiente")
            conn.execute(
                "UPDATE users SET balance=?, updated_at=? WHERE telegram_id=?",
                (new_balance, now, telegram_id),
            )
            conn.execute(
                "INSERT INTO balance_movements (telegram_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, amount, reason, now),
            )
            conn.commit()
            return new_balance

    def reserve_check_credit(self, telegram_id: int, cost: int) -> int:
        return self.change_balance(telegram_id, -cost, "check_reserved")

    def refund_check_credit(self, telegram_id: int, cost: int) -> int:
        return self.change_balance(telegram_id, cost, "check_refund")

    def add_check(self, telegram_id: int, identifier: str, service_id: int, cost: int, status: str, raw_response: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO checks (telegram_id, identifier, service_id, cost, status, raw_response, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, identifier, service_id, cost, status, raw_response, self._now()),
            )

    def history(self, telegram_id: int, limit: int = 10):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT identifier, cost, status, created_at FROM checks WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
                (telegram_id, limit),
            ).fetchall()
