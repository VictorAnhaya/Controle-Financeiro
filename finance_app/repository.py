from __future__ import annotations

import sqlite3
import json
import re
import unicodedata
from datetime import date
from typing import Any, Iterable

from .database import Database


class FinanceRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def refresh_overdue(self, today: date) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE transactions
                   SET status = 'overdue', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE status = 'pending'
                   AND due_date IS NOT NULL
                   AND due_date < ?
                """,
                (today.isoformat(),),
            )

    def get_transaction(self, transaction_id: int) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
        return self._as_dict(row)

    def list_transactions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []

        if filters.get("month"):
            clauses.append("substr(transaction_date, 1, 7) = ?")
            parameters.append(filters["month"])
        if filters.get("kind"):
            clauses.append("kind = ?")
            parameters.append(filters["kind"])
        if filters.get("status"):
            clauses.append("status = ?")
            parameters.append(filters["status"])
        if filters.get("category"):
            clauses.append("category = ?")
            parameters.append(filters["category"])
        if filters.get("search"):
            clauses.append(
                "(description LIKE ? OR counterparty LIKE ? OR document_number LIKE ?)"
            )
            search = f"%{filters['search']}%"
            parameters.extend([search, search, search])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = min(max(int(filters.get("limit", 200)), 1), 1000)
        query = f"""
            SELECT * FROM transactions
            {where}
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
        """
        parameters.append(limit)

        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def create_transaction(self, values: dict[str, Any]) -> dict[str, Any]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join("?" for _ in values)
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"INSERT INTO transactions ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            transaction_id = int(cursor.lastrowid)
        created = self.get_transaction(transaction_id)
        assert created is not None
        return created

    def create_transaction_for_document(
        self, document_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        columns = ", ".join(values.keys())
        placeholders = ", ".join("?" for _ in values)
        with self.database.connection() as connection:
            document = connection.execute(
                "SELECT * FROM fiscal_documents WHERE id = ?", (document_id,)
            ).fetchone()
            if document is None:
                return None
            if document["transaction_id"] is not None:
                raise sqlite3.IntegrityError("Documento já vinculado a um lançamento.")
            cursor = connection.execute(
                f"INSERT INTO transactions ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            transaction_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE fiscal_documents
                   SET transaction_id = ?, extraction_status = 'linked',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                (transaction_id, document_id),
            )
            transaction = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
            linked_document = connection.execute(
                "SELECT * FROM fiscal_documents WHERE id = ?", (document_id,)
            ).fetchone()
        assert transaction is not None and linked_document is not None
        return dict(transaction), self._document_dict(linked_document)

    def create_transactions_ignoring_duplicates(
        self, rows: Iterable[dict[str, Any]]
    ) -> tuple[int, int]:
        inserted = 0
        ignored = 0
        with self.database.connection() as connection:
            for values in rows:
                columns = ", ".join(values.keys())
                placeholders = ", ".join("?" for _ in values)
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO transactions ({columns}) VALUES ({placeholders})",
                    tuple(values.values()),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    ignored += 1
        return inserted, ignored

    def update_transaction(self, transaction_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        parameters = [*values.values(), transaction_id]
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE transactions
                   SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                parameters,
            )
            if cursor.rowcount == 0:
                return None
        return self.get_transaction(transaction_id)

    def delete_transaction(self, transaction_id: int) -> bool:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE fiscal_documents
                   SET transaction_id = NULL,
                       extraction_status = CASE
                           WHEN confidence >= 0.85 AND issuer_name IS NOT NULL
                                AND issue_date IS NOT NULL AND total_cents IS NOT NULL
                           THEN 'analyzed'
                           ELSE 'needs_review'
                       END,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE transaction_id = ?
                """,
                (transaction_id,),
            )
            cursor = connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        return cursor.rowcount == 1

    @staticmethod
    def client_match_key(name: str, tax_id: str | None = None) -> str:
        digits = re.sub(r"\D", "", tax_id or "")
        if digits:
            return f"tax:{digits}"
        normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()
        return f"name:{normalized}"

    def sync_clients_from_income(self) -> int:
        """Create/link client records from legacy income transactions."""
        linked = 0
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT counterparty, counterparty_tax_id,
                       MIN(transaction_date) AS acquisition_date
                  FROM transactions
                 WHERE kind = 'income'
                   AND status != 'cancelled'
                   AND counterparty IS NOT NULL
                   AND trim(counterparty) != ''
                 GROUP BY COALESCE(NULLIF(counterparty_tax_id, ''), lower(trim(counterparty)))
                """
            ).fetchall()
            for row in rows:
                name = str(row["counterparty"]).strip()
                tax_id = re.sub(r"\D", "", row["counterparty_tax_id"] or "") or None
                match_key = self.client_match_key(name, tax_id)
                connection.execute(
                    """
                    INSERT INTO clients (name, tax_id, match_key, acquisition_date)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(match_key) DO UPDATE SET
                        name = CASE WHEN clients.name = '' THEN excluded.name ELSE clients.name END,
                        tax_id = COALESCE(clients.tax_id, excluded.tax_id),
                        acquisition_date = CASE
                            WHEN clients.acquisition_date IS NULL THEN excluded.acquisition_date
                            WHEN excluded.acquisition_date < clients.acquisition_date THEN excluded.acquisition_date
                            ELSE clients.acquisition_date
                        END,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (name, tax_id, match_key, row["acquisition_date"]),
                )
                client = connection.execute(
                    "SELECT id FROM clients WHERE match_key = ?", (match_key,)
                ).fetchone()
                assert client is not None
                if tax_id:
                    cursor = connection.execute(
                        """
                        UPDATE transactions SET client_id = ?
                         WHERE kind = 'income' AND counterparty_tax_id = ?
                           AND (client_id IS NULL OR client_id != ?)
                        """,
                        (client["id"], tax_id, client["id"]),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE transactions SET client_id = ?
                         WHERE kind = 'income' AND lower(trim(counterparty)) = lower(trim(?))
                           AND (client_id IS NULL OR client_id != ?)
                        """,
                        (client["id"], name, client["id"]),
                    )
                linked += max(cursor.rowcount, 0)
        return linked

    def get_client(self, client_id: int) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return self._as_dict(row)

    def find_client_by_match_key(self, match_key: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM clients WHERE match_key = ?", (match_key,)
            ).fetchone()
        return self._as_dict(row)

    def list_clients(self, month: str, search: str = "", status: str = "") -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = [month]
        if search:
            clauses.append("(c.name LIKE ? OR c.tax_id LIKE ? OR c.contact_name LIKE ? OR c.email LIKE ?)")
            token = f"%{search}%"
            parameters.extend([token, token, token, token])
        if status:
            clauses.append("c.status = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.*,
                       COUNT(CASE WHEN t.kind = 'income' AND t.status != 'cancelled' THEN 1 END) AS invoice_count,
                       COALESCE(SUM(CASE WHEN t.kind = 'income' AND t.status != 'cancelled' THEN t.amount_cents END), 0) AS total_revenue_cents,
                       COUNT(CASE WHEN t.kind = 'income' AND t.status != 'cancelled' AND substr(t.transaction_date, 1, 7) = ? THEN 1 END) AS month_invoice_count,
                       COALESCE(SUM(CASE WHEN t.kind = 'income' AND t.status != 'cancelled' AND substr(t.transaction_date, 1, 7) = ? THEN t.amount_cents END), 0) AS month_revenue_cents,
                       MAX(CASE WHEN t.kind = 'income' AND t.status != 'cancelled' THEN t.transaction_date END) AS last_revenue_date
                  FROM clients c
                  LEFT JOIN transactions t ON t.client_id = c.id
                  {where}
                 GROUP BY c.id
                 ORDER BY c.status ASC, c.name COLLATE NOCASE ASC
                """,
                [month, *parameters],
            ).fetchall()
        return [dict(row) for row in rows]

    def client_options(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT id, name, tax_id FROM clients WHERE status = 'active' ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def client_summary(self, month: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(CASE WHEN status = 'active' THEN 1 END) AS active_count,
                    COUNT(CASE WHEN status = 'inactive' THEN 1 END) AS inactive_count,
                    COUNT(CASE WHEN substr(acquisition_date, 1, 7) = ? THEN 1 END) AS new_count
                FROM clients
                """,
                (month,),
            ).fetchone()
            revenue = connection.execute(
                """
                SELECT COALESCE(SUM(amount_cents), 0) AS revenue_cents
                  FROM transactions
                 WHERE kind = 'income' AND status = 'paid'
                   AND substr(transaction_date, 1, 7) = ?
                """,
                (month,),
            ).fetchone()
        result = dict(row)
        result["revenue_cents"] = int(revenue["revenue_cents"])
        return result

    def create_client(self, values: dict[str, Any]) -> dict[str, Any]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join("?" for _ in values)
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"INSERT INTO clients ({columns}) VALUES ({placeholders})", tuple(values.values())
            )
            client_id = int(cursor.lastrowid)
        created = self.get_client(client_id)
        assert created is not None
        return created

    def update_client(self, client_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE clients SET {assignments},
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                [*values.values(), client_id],
            )
            if cursor.rowcount == 0:
                return None
        return self.get_client(client_id)

    def relink_client_transactions(self, client_id: int, name: str, tax_id: str | None) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE transactions
                   SET counterparty = ?, counterparty_tax_id = ?
                 WHERE client_id = ? AND kind = 'income'
                """,
                (name, tax_id, client_id),
            )

    def upsert_goal(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO goals (month, revenue_target_cents, expense_limit_cents, new_clients_target, notes)
                VALUES (:month, :revenue_target_cents, :expense_limit_cents, :new_clients_target, :notes)
                ON CONFLICT(month) DO UPDATE SET
                    revenue_target_cents = excluded.revenue_target_cents,
                    expense_limit_cents = excluded.expense_limit_cents,
                    new_clients_target = excluded.new_clients_target,
                    notes = excluded.notes,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                values,
            )
            row = connection.execute("SELECT * FROM goals WHERE month = ?", (values["month"],)).fetchone()
        result = self._as_dict(row)
        assert result is not None
        return result

    def get_goal(self, month: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM goals WHERE month = ?", (month,)).fetchone()
        return self._as_dict(row)

    def month_actuals(self, month: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'paid' THEN amount_cents END), 0) AS revenue_cents,
                    COALESCE(SUM(CASE WHEN kind = 'expense' AND status = 'paid' THEN amount_cents END), 0) AS expense_cents
                  FROM transactions WHERE substr(transaction_date, 1, 7) = ?
                """,
                (month,),
            ).fetchone()
            clients = connection.execute(
                "SELECT COUNT(*) AS new_clients FROM clients WHERE substr(acquisition_date, 1, 7) = ?",
                (month,),
            ).fetchone()
        return {**dict(totals), **dict(clients)}

    def dashboard(self, month: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'paid' THEN amount_cents END), 0) AS income_cents,
                    COALESCE(SUM(CASE WHEN kind = 'expense' AND status = 'paid' THEN amount_cents END), 0) AS expense_cents,
                    COALESCE(SUM(CASE WHEN kind = 'expense' AND status IN ('pending', 'overdue') THEN amount_cents END), 0) AS payable_cents,
                    COALESCE(SUM(CASE WHEN status = 'cancelled' THEN amount_cents END), 0) AS cancelled_cents,
                    COUNT(CASE WHEN kind = 'income' AND status = 'paid' THEN 1 END) AS income_count,
                    COUNT(CASE WHEN kind = 'expense' AND status = 'paid' THEN 1 END) AS expense_count,
                    COUNT(CASE WHEN kind = 'expense' AND status = 'overdue' THEN 1 END) AS overdue_count
                FROM transactions
                WHERE substr(transaction_date, 1, 7) = ?
                """,
                (month,),
            ).fetchone()

            expenses_by_category = connection.execute(
                """
                SELECT category, SUM(amount_cents) AS amount_cents
                FROM transactions
                WHERE substr(transaction_date, 1, 7) = ?
                  AND kind = 'expense'
                  AND status = 'paid'
                GROUP BY category
                ORDER BY amount_cents DESC
                """,
                (month,),
            ).fetchall()

            daily_flow = connection.execute(
                """
                SELECT transaction_date,
                       SUM(CASE WHEN kind = 'income' AND status = 'paid' THEN amount_cents ELSE 0 END) AS income_cents,
                       SUM(CASE WHEN kind = 'expense' AND status = 'paid' THEN amount_cents ELSE 0 END) AS expense_cents
                FROM transactions
                WHERE substr(transaction_date, 1, 7) = ?
                GROUP BY transaction_date
                ORDER BY transaction_date
                """,
                (month,),
            ).fetchall()

            budget = connection.execute(
                """
                SELECT COALESCE(SUM(limit_cents), 0) AS limit_cents
                FROM budgets
                WHERE month = ?
                """,
                (month,),
            ).fetchone()

            recent = connection.execute(
                """
                SELECT * FROM transactions
                WHERE substr(transaction_date, 1, 7) = ?
                ORDER BY transaction_date DESC, id DESC
                LIMIT 6
                """,
                (month,),
            ).fetchall()

        return {
            "totals": dict(totals),
            "expenses_by_category": [dict(row) for row in expenses_by_category],
            "daily_flow": [dict(row) for row in daily_flow],
            "budget_limit_cents": int(budget["limit_cents"]),
            "recent": [dict(row) for row in recent],
        }

    def list_budgets(self, month: str) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT b.*,
                       COALESCE(SUM(CASE WHEN t.status = 'paid' THEN t.amount_cents END), 0) AS used_cents
                  FROM budgets b
                  LEFT JOIN transactions t
                    ON t.kind = 'expense'
                   AND t.category = b.category
                   AND substr(t.transaction_date, 1, 7) = b.month
                 WHERE b.month = ?
                 GROUP BY b.id
                 ORDER BY b.category
                """,
                (month,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_budget(self, month: str, category: str, limit_cents: int) -> dict[str, Any]:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO budgets (month, category, limit_cents)
                VALUES (?, ?, ?)
                ON CONFLICT(month, category) DO UPDATE SET
                    limit_cents = excluded.limit_cents,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (month, category, limit_cents),
            )
            row = connection.execute(
                "SELECT * FROM budgets WHERE month = ? AND category = ?", (month, category)
            ).fetchone()
        result = self._as_dict(row)
        assert result is not None
        return result

    def metadata(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            latest = connection.execute(
                "SELECT MAX(substr(transaction_date, 1, 7)) AS latest_month FROM transactions"
            ).fetchone()
            categories = connection.execute(
                "SELECT DISTINCT category FROM transactions ORDER BY category"
            ).fetchall()
            cost_centers = connection.execute(
                "SELECT DISTINCT cost_center FROM transactions WHERE cost_center IS NOT NULL AND cost_center != '' ORDER BY cost_center"
            ).fetchall()
        return {
            "latest_month": latest["latest_month"],
            "categories": [row["category"] for row in categories],
            "cost_centers": [row["cost_center"] for row in cost_centers],
        }

    def backfill_counterparty_tax_ids(self) -> int:
        updated = 0
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, notes
                FROM transactions
                WHERE (counterparty_tax_id IS NULL OR counterparty_tax_id = '')
                  AND notes IS NOT NULL
                """
            ).fetchall()
            for row in rows:
                match = re.search(r"CNPJ(?:/CPF)?(?:\s+cliente)?\s*:\s*([\d./-]+)", row["notes"], re.IGNORECASE)
                if not match:
                    continue
                tax_id = re.sub(r"\D", "", match.group(1))
                if len(tax_id) not in {11, 14}:
                    continue
                connection.execute(
                    "UPDATE transactions SET counterparty_tax_id = ? WHERE id = ?",
                    (tax_id, row["id"]),
                )
                updated += 1
        return updated

    def client_ranking(self, month: str | None = None) -> list[dict[str, Any]]:
        clauses = ["kind = 'income'", "status != 'cancelled'", "counterparty IS NOT NULL", "trim(counterparty) != ''"]
        parameters: list[Any] = []
        if month:
            clauses.append("substr(transaction_date, 1, 7) = ?")
            parameters.append(month)
        where = " AND ".join(clauses)
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(counterparty_tax_id, ''), lower(trim(counterparty))) AS client_key,
                    MAX(counterparty_tax_id) AS tax_id,
                    MAX(counterparty) AS client_name,
                    COUNT(*) AS invoice_count,
                    SUM(amount_cents) AS total_cents,
                    CAST(ROUND(AVG(amount_cents)) AS INTEGER) AS average_ticket_cents
                FROM transactions
                WHERE {where}
                GROUP BY COALESCE(NULLIF(counterparty_tax_id, ''), lower(trim(counterparty)))
                ORDER BY total_cents DESC, invoice_count DESC, client_name ASC
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _document_dict(row: sqlite3.Row) -> dict[str, Any]:
        document = dict(row)
        document["extracted"] = json.loads(document.pop("extracted_json"))
        return document

    def find_document_by_hash(self, sha256: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM fiscal_documents WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return self._document_dict(row) if row is not None else None

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM fiscal_documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._document_dict(row) if row is not None else None

    def list_documents(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM fiscal_documents
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [self._document_dict(row) for row in rows]

    def create_document(self, values: dict[str, Any]) -> dict[str, Any]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join("?" for _ in values)
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"INSERT INTO fiscal_documents ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            document_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM fiscal_documents WHERE id = ?", (document_id,)
            ).fetchone()
        assert row is not None
        return self._document_dict(row)
