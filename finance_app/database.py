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

                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    municipality TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );

                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    tax_id TEXT UNIQUE,
                    match_key TEXT NOT NULL UNIQUE,
                    contact_name TEXT,
                    email TEXT,
                    phone TEXT,
                    acquisition_date TEXT,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );

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
                    company_id INTEGER,
                    client_id INTEGER,
                    counterparty TEXT,
                    counterparty_tax_id TEXT,
                    document_number TEXT,
                    notes TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    external_key TEXT UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL,
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT
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

                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT NOT NULL UNIQUE,
                    revenue_target_cents INTEGER NOT NULL CHECK (revenue_target_cents > 0),
                    expense_limit_cents INTEGER NOT NULL CHECK (expense_limit_cents > 0),
                    new_clients_target INTEGER NOT NULL DEFAULT 0 CHECK (new_clients_target >= 0),
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );

                CREATE TABLE IF NOT EXISTS company_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    company_id INTEGER,
                    month TEXT NOT NULL,
                    revenue_target_cents INTEGER NOT NULL CHECK (revenue_target_cents > 0),
                    expense_limit_cents INTEGER NOT NULL CHECK (expense_limit_cents > 0),
                    new_clients_target INTEGER NOT NULL DEFAULT 0 CHECK (new_clients_target >= 0),
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(scope_key, month),
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS company_budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    company_id INTEGER,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    limit_cents INTEGER NOT NULL CHECK (limit_cents > 0),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(scope_key, month, category),
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
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
                    company_id INTEGER,
                    transaction_id INTEGER UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL,
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT
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
                CREATE INDEX IF NOT EXISTS idx_clients_status
                    ON clients(status);
                CREATE INDEX IF NOT EXISTS idx_clients_acquisition_date
                    ON clients(acquisition_date);
                CREATE INDEX IF NOT EXISTS idx_company_goals_scope_month
                    ON company_goals(scope_key, month);
                CREATE INDEX IF NOT EXISTS idx_company_budgets_scope_month
                    ON company_budgets(scope_key, month);
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
            self._ensure_column(
                connection,
                "transactions",
                "client_id",
                "INTEGER REFERENCES clients(id) ON DELETE SET NULL",
            )
            self._ensure_column(
                connection,
                "transactions",
                "company_id",
                "INTEGER REFERENCES companies(id) ON DELETE RESTRICT",
            )
            self._ensure_column(
                connection,
                "fiscal_documents",
                "company_id",
                "INTEGER REFERENCES companies(id) ON DELETE RESTRICT",
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_client ON transactions(client_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_company_date ON transactions(company_id, transaction_date)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_company ON fiscal_documents(company_id)"
            )
            self._migrate_default_companies(connection)
            connection.execute(
                """
                UPDATE users
                   SET role = 'user', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE role = 'admin'
                   AND id != (SELECT MIN(id) FROM users WHERE role = 'admin')
                """
            )
            connection.execute(
                """
                UPDATE transactions
                   SET company_id = (SELECT id FROM companies WHERE slug = 'brc')
                 WHERE company_id IS NULL
                   AND (notes LIKE '%São José dos Pinhais%' OR notes LIKE '%Sao Jose dos Pinhais%')
                """
            )
            connection.execute(
                """
                UPDATE transactions
                   SET company_id = (SELECT id FROM companies WHERE slug = 'wbk')
                 WHERE company_id IS NULL AND notes LIKE '%Curitiba%'
                """
            )
            connection.execute(
                """
                UPDATE transactions
                   SET external_key = 'company:' || company_id || ':' || external_key
                 WHERE company_id IS NOT NULL AND external_key IS NOT NULL
                   AND external_key NOT LIKE 'company:%'
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO company_goals
                    (scope_key, company_id, month, revenue_target_cents, expense_limit_cents, new_clients_target, notes)
                SELECT 'all', NULL, month, revenue_target_cents, expense_limit_cents, new_clients_target, notes
                  FROM goals
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO company_budgets
                    (scope_key, company_id, month, category, limit_cents)
                SELECT 'all', NULL, month, category, limit_cents FROM budgets
                """
            )

    @staticmethod
    def _migrate_default_companies(connection: sqlite3.Connection) -> None:
        old_company = connection.execute(
            "SELECT id FROM companies WHERE slug = 'bolotti-reis'"
        ).fetchone()
        brc_company = connection.execute(
            "SELECT id FROM companies WHERE slug = 'brc'"
        ).fetchone()

        if old_company is not None and brc_company is None:
            connection.execute(
                """
                UPDATE companies
                   SET name = 'BRC', slug = 'brc', municipality = 'São José dos Pinhais/PR',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (old_company["id"],),
            )
        elif old_company is not None and brc_company is not None:
            old_id = int(old_company["id"])
            brc_id = int(brc_company["id"])
            old_prefix = f"company:{old_id}:"
            new_prefix = f"company:{brc_id}:"
            connection.execute(
                """
                UPDATE transactions
                   SET external_key = ? || substr(external_key, ?)
                 WHERE company_id = ?
                   AND external_key LIKE ?
                   AND NOT EXISTS (
                       SELECT 1
                         FROM transactions AS target
                        WHERE target.external_key = ? || substr(transactions.external_key, ?)
                   )
                """,
                (
                    new_prefix,
                    len(old_prefix) + 1,
                    old_id,
                    f"{old_prefix}%",
                    new_prefix,
                    len(old_prefix) + 1,
                ),
            )
            connection.execute(
                "UPDATE transactions SET company_id = ? WHERE company_id = ?",
                (brc_id, old_id),
            )
            connection.execute(
                "UPDATE fiscal_documents SET company_id = ? WHERE company_id = ?",
                (brc_id, old_id),
            )
            connection.execute(
                """
                DELETE FROM company_goals
                 WHERE company_id = ?
                   AND month IN (SELECT month FROM company_goals WHERE company_id = ?)
                """,
                (old_id, brc_id),
            )
            connection.execute(
                "UPDATE company_goals SET company_id = ?, scope_key = ? WHERE company_id = ?",
                (brc_id, f"company:{brc_id}", old_id),
            )
            connection.execute(
                """
                DELETE FROM company_budgets
                 WHERE company_id = ?
                   AND EXISTS (
                       SELECT 1
                         FROM company_budgets AS target
                        WHERE target.company_id = ?
                          AND target.month = company_budgets.month
                          AND target.category = company_budgets.category
                   )
                """,
                (old_id, brc_id),
            )
            connection.execute(
                "UPDATE company_budgets SET company_id = ?, scope_key = ? WHERE company_id = ?",
                (brc_id, f"company:{brc_id}", old_id),
            )
            connection.execute("DELETE FROM companies WHERE id = ?", (old_id,))

        connection.execute(
            """
            INSERT INTO companies (name, slug, municipality)
            VALUES ('BRC', 'brc', 'São José dos Pinhais/PR')
            ON CONFLICT(slug) DO UPDATE SET
                name = 'BRC', municipality = 'São José dos Pinhais/PR',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO companies (name, slug, municipality)
            VALUES ('WBK', 'wbk', 'Curitiba/PR')
            ON CONFLICT(slug) DO UPDATE SET
                name = 'WBK', municipality = 'Curitiba/PR',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
