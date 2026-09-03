import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP


UNITS_PER_CREDIT = 100
INITIAL_BONUS_CREDITS = Decimal("5.00")
SUBSCRIPTION_DAYS = 30


def credits_to_units(value) -> int:
    amount = Decimal(str(value))
    return int((amount * UNITS_PER_CREDIT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def units_to_credits(units: int) -> Decimal:
    return (Decimal(int(units)) / UNITS_PER_CREDIT).quantize(Decimal("0.01"))


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

    @staticmethod
    def _expiry_from_now(days: int = SUBSCRIPTION_DAYS):
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    def _column_names(self, conn, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init(self):
        with self._lock, self._connect() as conn:
            conn.executescript("""
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

                CREATE TABLE IF NOT EXISTS recharge_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    package_units INTEGER NOT NULL,
                    price_lps TEXT NOT NULL,
                    method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_file_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            user_cols = self._column_names(conn, "users")

            if "balance_units" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN balance_units INTEGER")
                conn.execute("UPDATE users SET balance_units = balance * 100 WHERE balance_units IS NULL")
            conn.execute("UPDATE users SET balance_units = 0 WHERE balance_units IS NULL")

            if "is_active" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            conn.execute("UPDATE users SET is_active = 1 WHERE is_active IS NULL")

            if "subscription_expires_at" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN subscription_expires_at TEXT")

            if "welcome_bonus_granted" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN welcome_bonus_granted INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET welcome_bonus_granted = 0 WHERE welcome_bonus_granted IS NULL")

            check_cols = self._column_names(conn, "checks")
            if "cost_units" not in check_cols:
                conn.execute("ALTER TABLE checks ADD COLUMN cost_units INTEGER")
                conn.execute("UPDATE checks SET cost_units = cost * 100 WHERE cost_units IS NULL")

            movement_cols = self._column_names(conn, "balance_movements")
            if "amount_units" not in movement_cols:
                conn.execute("ALTER TABLE balance_movements ADD COLUMN amount_units INTEGER")
                conn.execute("UPDATE balance_movements SET amount_units = amount * 100 WHERE amount_units IS NULL")

            conn.commit()

    def ensure_user(self, telegram_id: int, username: str | None, first_name: str | None):
        """
        Registra el Telegram ID si todavía no existe.
        IMPORTANTE: los usuarios nuevos que solo hacen /start quedan INACTIVOS.
        Solo /agregar o /activar les da acceso.
        """
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO users
                   (telegram_id, username, first_name, balance, balance_units,
                    created_at, updated_at, is_active, subscription_expires_at, welcome_bonus_granted)
                   VALUES (?, ?, ?, 0, 0, ?, ?, 0, NULL, 0)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                     username=excluded.username,
                     first_name=excluded.first_name,
                     updated_at=excluded.updated_at""",
                (telegram_id, username, first_name, now, now)
            )
            conn.commit()

    def add_subscriber(self, telegram_id: int, days: int = SUBSCRIPTION_DAYS) -> tuple[Decimal, bool, str]:
        """
        Alta/renovación por administrador.
        - Activa por 30 días.
        - Regala 5 créditos SOLO la primera vez.
        - Si ya recibió el bono antes, no vuelve a recibirlo.
        """
        now = self._now()
        expiry = self._expiry_from_now(days)
        bonus_units = credits_to_units(INITIAL_BONUS_CREDITS)

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """SELECT balance_units, welcome_bonus_granted
                   FROM users WHERE telegram_id=?""",
                (telegram_id,),
            ).fetchone()

            if not row:
                conn.execute(
                    """INSERT INTO users
                       (telegram_id, username, first_name, balance, balance_units,
                        created_at, updated_at, is_active, subscription_expires_at, welcome_bonus_granted)
                       VALUES (?, NULL, NULL, 0, 0, ?, ?, 1, ?, 0)""",
                    (telegram_id, now, now, expiry),
                )
                current_units = 0
                bonus_already_granted = False
            else:
                current_units = int(row["balance_units"] or 0)
                bonus_already_granted = bool(row["welcome_bonus_granted"])

            bonus_given = not bonus_already_granted
            new_units = current_units + (bonus_units if bonus_given else 0)

            conn.execute(
                """UPDATE users
                   SET balance_units=?,
                       balance=?,
                       is_active=1,
                       subscription_expires_at=?,
                       welcome_bonus_granted=1,
                       updated_at=?
                   WHERE telegram_id=?""",
                (new_units, new_units / 100, expiry, now, telegram_id),
            )

            if bonus_given:
                conn.execute(
                    """INSERT INTO balance_movements
                       (telegram_id, amount, amount_units, reason, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        telegram_id,
                        bonus_units / 100,
                        bonus_units,
                        "welcome_bonus",
                        now,
                    ),
                )

            conn.commit()
            return units_to_credits(new_units), bonus_given, expiry

    def is_user_active(self, telegram_id: int) -> bool:
        """
        Devuelve True solo si el usuario está activo y su suscripción no venció.
        Usuarios antiguos activos sin fecha de vencimiento se mantienen activos
        hasta que sean renovados/suspendidos manualmente.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT is_active, subscription_expires_at FROM users WHERE telegram_id=?",
                (telegram_id,)
            ).fetchone()

            if row is None:
                return False

            if not bool(row["is_active"]):
                return False

            expires_at = row["subscription_expires_at"]
            if not expires_at:
                return True

            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except Exception:
                return False

            if datetime.now(timezone.utc) >= expiry:
                conn.execute(
                    "UPDATE users SET is_active=0, updated_at=? WHERE telegram_id=?",
                    (self._now(), telegram_id),
                )
                conn.commit()
                return False

            return True

    def set_user_active(self, telegram_id: int, active: bool, renew_days: int | None = None):
        """
        Suspender: active=False, conserva saldo y fecha.
        Activar/renovar: active=True y, si renew_days se indica, crea nueva vigencia.
        NO regala créditos.
        """
        now = self._now()
        expiry = self._expiry_from_now(renew_days) if (active and renew_days) else None

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()

            if not row:
                conn.execute(
                    """INSERT INTO users
                       (telegram_id, username, first_name, balance, balance_units,
                        created_at, updated_at, is_active, subscription_expires_at, welcome_bonus_granted)
                       VALUES (?, NULL, NULL, 0, 0, ?, ?, ?, ?, 0)""",
                    (telegram_id, now, now, 1 if active else 0, expiry),
                )
            elif active and renew_days:
                conn.execute(
                    """UPDATE users
                       SET is_active=1, subscription_expires_at=?, updated_at=?
                       WHERE telegram_id=?""",
                    (expiry, now, telegram_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET is_active=?, updated_at=? WHERE telegram_id=?",
                    (1 if active else 0, now, telegram_id),
                )

            conn.commit()

    def get_subscription_expires_at(self, telegram_id: int) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT subscription_expires_at FROM users WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()
            return row["subscription_expires_at"] if row else None

    def get_balance(self, telegram_id: int) -> Decimal:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT balance_units FROM users WHERE telegram_id=?",
                (telegram_id,)
            ).fetchone()
            return units_to_credits(row["balance_units"]) if row else Decimal("0.00")

    def change_balance(self, telegram_id: int, amount, reason: str) -> Decimal:
        amount_units = credits_to_units(amount)
        now = self._now()

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT balance_units FROM users WHERE telegram_id=?",
                (telegram_id,)
            ).fetchone()

            current_units = int(row["balance_units"]) if row else 0

            if not row:
                conn.execute(
                    """INSERT INTO users
                       (telegram_id, username, first_name, balance, balance_units,
                        created_at, updated_at, is_active, subscription_expires_at, welcome_bonus_granted)
                       VALUES (?, NULL, NULL, 0, 0, ?, ?, 0, NULL, 0)""",
                    (telegram_id, now, now),
                )

            new_units = current_units + amount_units
            if new_units < 0:
                raise ValueError("Saldo insuficiente")

            conn.execute(
                "UPDATE users SET balance_units=?, balance=?, updated_at=? WHERE telegram_id=?",
                (new_units, new_units / 100, now, telegram_id),
            )
            conn.execute(
                """INSERT INTO balance_movements
                   (telegram_id, amount, amount_units, reason, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (telegram_id, amount_units / 100, amount_units, reason, now),
            )
            conn.commit()
            return units_to_credits(new_units)

    def reserve_check_credit(self, telegram_id: int, cost) -> Decimal:
        return self.change_balance(telegram_id, -Decimal(str(cost)), "check_reserved")

    def refund_check_credit(self, telegram_id: int, cost) -> Decimal:
        return self.change_balance(telegram_id, Decimal(str(cost)), "check_refund")

    def add_check(self, telegram_id: int, identifier: str, service_id: int, cost, status: str, raw_response: str):
        cost_units = credits_to_units(cost)
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO checks
                   (telegram_id, identifier, service_id, cost, cost_units, status, raw_response, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    telegram_id,
                    identifier,
                    service_id,
                    cost_units / 100,
                    cost_units,
                    status,
                    raw_response,
                    self._now(),
                ),
            )
            conn.commit()

    def history(self, telegram_id: int, limit: int = 10):
        with self._lock, self._connect() as conn:
            return conn.execute(
                """SELECT identifier, cost_units, status, created_at
                   FROM checks
                   WHERE telegram_id=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (telegram_id, limit),
            ).fetchall()

    def create_recharge_request(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        package_credits,
        price_lps: str,
        method: str,
        status: str,
    ) -> int:
        now = self._now()
        package_units = credits_to_units(package_credits)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO recharge_requests
                   (telegram_id, username, first_name, package_units, price_lps, method, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    telegram_id,
                    username,
                    first_name,
                    package_units,
                    price_lps,
                    method,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_recharge_request(self, request_id: int):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT * FROM recharge_requests WHERE id=?",
                (request_id,),
            ).fetchone()

    def set_recharge_status(self, request_id: int, status: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE recharge_requests SET status=?, updated_at=? WHERE id=?",
                (status, self._now(), request_id),
            )
            conn.commit()

    def set_recharge_receipt(self, request_id: int, file_id: str | None):
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE recharge_requests
                   SET receipt_file_id=?, status='receipt_pending', updated_at=?
                   WHERE id=?""",
                (file_id, self._now(), request_id),
            )
            conn.commit()

    def approve_recharge(self, request_id: int) -> tuple[int, Decimal, Decimal]:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            req = conn.execute(
                "SELECT * FROM recharge_requests WHERE id=?",
                (request_id,),
            ).fetchone()

            if not req:
                raise ValueError("Solicitud no encontrada")
            if req["status"] == "approved":
                raise ValueError("Esta recarga ya fue aprobada")
            if req["status"] != "receipt_pending":
                raise ValueError("La solicitud todavía no tiene un comprobante pendiente")

            telegram_id = int(req["telegram_id"])
            package_units = int(req["package_units"])

            user = conn.execute(
                "SELECT balance_units FROM users WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()

            if not user:
                conn.execute(
                    """INSERT INTO users
                       (telegram_id, username, first_name, balance, balance_units,
                        created_at, updated_at, is_active, subscription_expires_at, welcome_bonus_granted)
                       VALUES (?, ?, ?, 0, 0, ?, ?, 0, NULL, 0)""",
                    (telegram_id, req["username"], req["first_name"], now, now),
                )
                current_units = 0
            else:
                current_units = int(user["balance_units"])

            new_units = current_units + package_units

            conn.execute(
                "UPDATE users SET balance_units=?, balance=?, updated_at=? WHERE telegram_id=?",
                (new_units, new_units / 100, now, telegram_id),
            )
            conn.execute(
                """INSERT INTO balance_movements
                   (telegram_id, amount, amount_units, reason, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    telegram_id,
                    package_units / 100,
                    package_units,
                    f"recharge_approved:{request_id}",
                    now,
                ),
            )
            conn.execute(
                "UPDATE recharge_requests SET status='approved', updated_at=? WHERE id=?",
                (now, request_id),
            )
            conn.commit()

            return (
                telegram_id,
                units_to_credits(package_units),
                units_to_credits(new_units),
            )
