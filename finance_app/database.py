from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
                    description TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    transaction_date TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL CHECK (status IN ('paid', 'pending', 'overdue', 'cancelled')),
                    category TEXT NOT NULL,
                    cost_center TEXT,
                    counterparty TEXT,
                    counterparty_tax_id TEXT,
                    document_number TEXT,
                    notes TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    external_key TEXT UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );

                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    limit_cents INTEGER NOT NULL CHECK (limit_cents > 0),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(month, category)
                );

                CREATE TABLE IF NOT EXISTS fiscal_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    mime_type TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes > 0),
                    sha256 TEXT NOT NULL UNIQUE,
                    document_type TEXT NOT NULL,
                    issuer_name TEXT,
                    issuer_tax_id TEXT,
                    recipient_name TEXT,
                    recipient_tax_id TEXT,
                    document_number TEXT,
                    access_key TEXT,
                    issue_date TEXT,
                    total_cents INTEGER,
                    extraction_status TEXT NOT NULL CHECK (extraction_status IN ('analyzed', 'needs_review', 'linked')),
                    confidence REAL NOT NULL DEFAULT 0,
                    extracted_json TEXT NOT NULL,
                    transaction_id INTEGER UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_date
                    ON transactions(transaction_date);
                CREATE INDEX IF NOT EXISTS idx_transactions_kind_status
                    ON transactions(kind, status);
                CREATE INDEX IF NOT EXISTS idx_transactions_category
                    ON transactions(category);
                CREATE INDEX IF NOT EXISTS idx_budgets_month
                    ON budgets(month);
                CREATE INDEX IF NOT EXISTS idx_fiscal_documents_issue_date
                    ON fiscal_documents(issue_date);
                CREATE INDEX IF NOT EXISTS idx_fiscal_documents_status
                    ON fiscal_documents(extraction_status);
                CREATE INDEX IF NOT EXISTS idx_sessions_user
                    ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiration
                    ON sessions(expires_at);
                """
            )
            self._ensure_column(connection, "transactions", "counterparty_tax_id", "TEXT")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
