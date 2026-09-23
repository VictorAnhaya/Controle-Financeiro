from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import importlib.util
from io import BytesIO
from pathlib import Path

from finance_app.database import Database
from finance_app.repository import FinanceRepository
from finance_app.document_reader import DocumentReadError
from finance_app.service import DuplicateDocumentError, FinanceService, ValidationError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_XLSX = os.getenv("SOURCE_XLSX")
PDF_TEST_AVAILABLE = bool(importlib.util.find_spec("reportlab") and importlib.util.find_spec("pypdf"))


class FinanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Database(Path(self.temporary_directory.name) / "test.db")
        database.migrate()
        self.repository = FinanceRepository(database)
        self.service = FinanceService(self.repository, Path(self.temporary_directory.name) / "documents")
        companies = {item["slug"]: item["id"] for item in self.repository.list_companies()}
        self.bolotti_id = companies["bolotti-reis"]
        self.wbk_id = companies["wbk"]

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_seed_reconciles_with_financial_report_and_is_idempotent(self) -> None:
        seed = PROJECT_ROOT / "data" / "initial_transactions.json"
        self.service.seed_initial_data(seed)
        self.service.seed_initial_data(seed)

        dashboard = self.service.dashboard("2026-08")

        self.assertEqual(dashboard["totals"]["income_cents"], 23_053_713)
        self.assertEqual(dashboard["totals"]["cancelled_cents"], 4_869_237)
        self.assertEqual(dashboard["totals"]["income_count"], 25)
        self.assertEqual(len(self.service.list_transactions({"month": "2026-08"})), 29)

    def test_companies_match_workbook_and_consolidated_totals(self) -> None:
        self.service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")

        consolidated = self.service.company_comparison("2026-08")
        by_slug = {item["slug"]: item for item in consolidated["items"]}

        self.assertEqual(by_slug["bolotti-reis"]["revenue_cents"], 12_366_537)
        self.assertEqual(by_slug["bolotti-reis"]["invoice_count"], 14)
        self.assertEqual(by_slug["wbk"]["revenue_cents"], 10_687_176)
        self.assertEqual(by_slug["wbk"]["invoice_count"], 11)
        self.assertEqual(by_slug["wbk"]["cancelled_cents"], 4_869_237)
        self.assertEqual(consolidated["totals"]["revenue_cents"], 23_053_713)
        self.assertEqual(consolidated["totals"]["invoice_count"], 25)
        self.assertEqual(consolidated["totals"]["unassigned_count"], 0)

        bolotti = self.service.dashboard("2026-08", self.bolotti_id)
        wbk = self.service.dashboard("2026-08", self.wbk_id)
        self.assertEqual(bolotti["totals"]["income_cents"], 12_366_537)
        self.assertEqual(wbk["totals"]["income_cents"], 10_687_176)

    def test_goals_are_independent_per_company(self) -> None:
        self.service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")
        bolotti = self.service.upsert_goal(
            {
                "company": self.bolotti_id,
                "month": "2026-08",
                "revenue_target": "150.000,00",
                "expense_limit": "10.000,00",
                "new_clients_target": 12,
            }
        )
        wbk = self.service.upsert_goal(
            {
                "company": self.wbk_id,
                "month": "2026-08",
                "revenue_target": "120.000,00",
                "expense_limit": "8.000,00",
                "new_clients_target": 10,
            }
        )

        self.assertEqual(bolotti["actual"]["revenue_cents"], 12_366_537)
        self.assertEqual(wbk["actual"]["revenue_cents"], 10_687_176)
        self.assertEqual(
            self.service.get_goal_projection("2026-08", self.bolotti_id)["goal"][
                "revenue_target_cents"
            ],
            15_000_000,
        )
        self.assertEqual(
            self.service.get_goal_projection("2026-08", self.wbk_id)["goal"][
                "revenue_target_cents"
            ],
            12_000_000,
        )

    def test_expense_and_budget_update_dashboard(self) -> None:
        created = self.service.create_transaction(
            {
                "kind": "expense",
                "description": "Licença do sistema",
                "amount": "1.250,50",
                "transaction_date": "2026-08-15",
                "due_date": "2026-08-15",
                "status": "paid",
                "category": "Tecnologia e sistemas",
                "cost_center": "TI",
                "company_id": self.bolotti_id,
            }
        )
        self.assertEqual(created["amount_cents"], 125_050)

        self.service.upsert_budget(
            {"month": "2026-08", "category": "Tecnologia e sistemas", "limit": "2.000,00"}
        )
        dashboard = self.service.dashboard("2026-08")
        budgets = self.service.list_budgets("2026-08")

        self.assertEqual(dashboard["totals"]["expense_cents"], 125_050)
        self.assertEqual(dashboard["budget_limit_cents"], 200_000)
        self.assertEqual(dashboard["totals"]["budget_usage_percent"], 62.5)
        self.assertEqual(budgets[0]["used_cents"], 125_050)

    def test_invalid_amount_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_transaction(
                {
                    "kind": "expense",
                    "description": "Valor inválido",
                    "amount": "0",
                    "transaction_date": "2026-08-15",
                    "status": "paid",
                    "category": "Outros",
                }
            )

    def test_client_ranking_repositions_gm_after_new_revenue(self) -> None:
        self.service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")
        initial = self.service.client_ranking("2026-08")
        self.assertEqual(initial["summary"]["client_count"], 17)
        self.assertEqual(initial["summary"]["total_cents"], 23_053_713)
        self.assertEqual(initial["summary"]["top3_cents"], 12_073_723)
        self.assertEqual(initial["summary"]["top3_percent"], 52.37)
        self.assertEqual(initial["items"][0]["tax_id"], "50251656000155")
        self.assertEqual(initial["items"][0]["total_cents"], 5_391_726)

        self.service.create_transaction(
            {
                "kind": "income",
                "description": "Nova NFS-e — G&M PALLETS LTDA",
                "amount": "30.000,00",
                "transaction_date": "2026-08-31",
                "status": "paid",
                "category": "Honorários e serviços",
                "counterparty": "G&M PALLETS LTDA",
                "counterparty_tax_id": "51.356.054/0001-25",
                "company_id": self.wbk_id,
            }
        )
        updated = self.service.client_ranking("2026-08")
        gm = updated["items"][1]
        self.assertEqual(gm["position"], 2)
        self.assertEqual(gm["tax_id"], "51356054000125")
        self.assertEqual(gm["total_cents"], 5_065_391)
        self.assertEqual(gm["invoice_count"], 3)

        self.service.create_transaction(
            {
                "kind": "income",
                "description": "Nota cancelada — G&M PALLETS LTDA",
                "amount": "50.000,00",
                "transaction_date": "2026-08-31",
                "status": "cancelled",
                "category": "Honorários e serviços",
                "counterparty": "G&M PALLETS LTDA",
                "counterparty_tax_id": "51.356.054/0001-25",
                "company_id": self.wbk_id,
            }
        )
        after_cancelled = self.service.client_ranking("2026-08")
        self.assertEqual(after_cancelled["items"][1]["total_cents"], 5_065_391)

    def test_clients_are_backfilled_and_can_be_managed(self) -> None:
        self.service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")
        result = self.service.list_clients({"month": "2026-08"})

        self.assertEqual(result["summary"]["active_count"], 17)
        self.assertEqual(result["summary"]["revenue_cents"], 23_053_713)
        self.assertTrue(all(item["invoice_count"] > 0 for item in result["items"]))

        client = self.service.create_client(
            {
                "name": "Cliente Teste Ltda",
                "tax_id": "12.345.678/0001-90",
                "contact_name": "Ana",
                "email": "ana@example.com",
                "phone": "(11) 99999-9999",
                "acquisition_date": "2026-09-05",
                "status": "active",
            }
        )
        income = self.service.create_transaction(
            {
                "kind": "income",
                "description": "Contrato mensal",
                "amount": "2.500,00",
                "transaction_date": "2026-09-10",
                "status": "paid",
                "category": "Honorários e serviços",
                "client_id": client["id"],
                "company_id": self.bolotti_id,
            }
        )
        self.assertEqual(income["client_id"], client["id"])
        self.assertEqual(income["counterparty"], "Cliente Teste Ltda")
        self.assertEqual(income["counterparty_tax_id"], "12345678000190")

        updated = self.service.update_client(client["id"], {"status": "inactive"})
        self.assertEqual(updated["status"], "inactive")
        with self.assertRaises(ValidationError):
            self.service.create_transaction(
                {
                    "kind": "income",
                    "description": "Não permitido",
                    "amount": "100,00",
                    "transaction_date": "2026-09-11",
                    "status": "paid",
                    "category": "Honorários e serviços",
                    "client_id": client["id"],
                    "company_id": self.bolotti_id,
                }
            )

    def test_goals_report_actuals_and_past_month_projection(self) -> None:
        self.service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")
        result = self.service.upsert_goal(
            {
                "month": "2026-08",
                "revenue_target": "250.000,00",
                "expense_limit": "20.000,00",
                "new_clients_target": "20",
                "notes": "Meta mensal",
            }
        )

        self.assertEqual(result["goal"]["revenue_target_cents"], 25_000_000)
        self.assertEqual(result["actual"]["revenue_cents"], 23_053_713)
        self.assertEqual(result["actual"]["new_clients"], 17)
        self.assertEqual(result["projection"]["revenue_cents"], 23_053_713)
        self.assertEqual(result["progress"]["revenue_remaining_cents"], 1_946_287)
        self.assertEqual(result["progress"]["clients_percent"], 85.0)

    def test_existing_database_is_migrated_without_losing_transactions(self) -> None:
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, description TEXT NOT NULL,
                amount_cents INTEGER NOT NULL, transaction_date TEXT NOT NULL,
                due_date TEXT, status TEXT NOT NULL, category TEXT NOT NULL,
                cost_center TEXT, counterparty TEXT, counterparty_tax_id TEXT,
                document_number TEXT, notes TEXT, source TEXT NOT NULL DEFAULT 'manual',
                external_key TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO transactions
                (kind, description, amount_cents, transaction_date, status, category, counterparty)
            VALUES ('income', 'Receita antiga', 10000, '2026-08-01', 'paid', 'Serviços', 'Cliente antigo')
            """
        )
        connection.commit()
        connection.close()

        database = Database(legacy_path)
        database.migrate()
        service = FinanceService(FinanceRepository(database))
        service.seed_initial_data(Path(self.temporary_directory.name) / "missing-seed.json")

        clients = service.list_clients({"month": "2026-08"})
        self.assertEqual(clients["summary"]["active_count"], 1)
        self.assertEqual(clients["items"][0]["name"], "Cliente antigo")
        self.assertEqual(service.dashboard("2026-08")["totals"]["income_cents"], 10000)

    def test_xml_document_is_extracted_linked_and_deduplicated(self) -> None:
        xml_path = PROJECT_ROOT / "tests" / "fixtures" / "nfe_sample.xml"
        content = xml_path.read_bytes()
        document = self.service.analyze_document(
            "nota-123.xml", "application/xml", content, self.bolotti_id
        )

        self.assertEqual(document["document_type"], "NF-e")
        self.assertEqual(document["issuer_name"], "FORNECEDOR EXEMPLO LTDA")
        self.assertEqual(document["issuer_tax_id"], "12345678000190")
        self.assertEqual(document["document_number"], "123")
        self.assertEqual(document["issue_date"], "2026-08-30")
        self.assertEqual(document["total_cents"], 123_456)
        self.assertEqual(document["extraction_status"], "analyzed")

        linked = self.service.create_transaction_from_document(
            document["id"],
            {
                "kind": "expense",
                "description": "NF-e 123 — FORNECEDOR EXEMPLO LTDA",
                "amount": "1.234,56",
                "transaction_date": "2026-08-30",
                "status": "pending",
                "category": "Outros",
                "counterparty": "FORNECEDOR EXEMPLO LTDA",
                "document_number": "123",
            },
        )
        self.assertIsNotNone(linked)
        self.assertEqual(linked["transaction"]["amount_cents"], 123_456)
        self.assertEqual(linked["document"]["extraction_status"], "linked")

        with self.assertRaises(DuplicateDocumentError):
            self.service.analyze_document("copia.xml", "application/xml", content, self.bolotti_id)

        self.assertTrue(self.service.delete_transaction(linked["transaction"]["id"]))
        unlinked_document = self.repository.get_document(document["id"])
        self.assertIsNone(unlinked_document["transaction_id"])
        self.assertEqual(unlinked_document["extraction_status"], "analyzed")

        income_link = self.service.create_transaction_from_document(
            document["id"],
            {
                "kind": "income",
                "description": "NF-e 123 recebida como receita",
                "amount": "1.234,56",
                "transaction_date": "2026-08-30",
                "status": "paid",
                "category": "Honorários e serviços",
            },
        )
        self.assertEqual(income_link["transaction"]["counterparty"], "GRUPO BOLOTTI REIS LTDA")
        self.assertEqual(income_link["transaction"]["counterparty_tax_id"], "98765432000110")

    def test_unsupported_document_is_rejected(self) -> None:
        with self.assertRaises(DocumentReadError):
            self.service.analyze_document(
                "arquivo.txt", "text/plain", b"conteudo qualquer", self.bolotti_id
            )

    @unittest.skipUnless(PDF_TEST_AVAILABLE, "Dependências de PDF não disponíveis")
    def test_text_pdf_extracts_main_fields(self) -> None:
        from reportlab.pdfgen.canvas import Canvas

        output = BytesIO()
        canvas = Canvas(output)
        lines = [
            "NOTA FISCAL DE SERVIÇOS NFS-e",
            "RAZÃO SOCIAL",
            "FORNECEDOR PDF LTDA",
            "CNPJ 12.345.678/0001-90",
            "NÚMERO DA NOTA: 456",
            "DATA DE EMISSÃO: 30/08/2026",
            "VALOR TOTAL DA NOTA: R$ 2.345,67",
        ]
        for index, line in enumerate(lines):
            canvas.drawString(70, 780 - index * 24, line)
        canvas.save()

        document = self.service.analyze_document(
            "nota-456.pdf", "application/pdf", output.getvalue(), self.bolotti_id
        )
        self.assertEqual(document["document_type"], "NFS-e")
        self.assertEqual(document["issuer_name"], "FORNECEDOR PDF LTDA")
        self.assertEqual(document["issuer_tax_id"], "12345678000190")
        self.assertEqual(document["document_number"], "456")
        self.assertEqual(document["issue_date"], "2026-08-30")
        self.assertEqual(document["total_cents"], 234_567)

    @unittest.skipUnless(SOURCE_XLSX and Path(SOURCE_XLSX).exists(), "Planilha-fonte não disponível")
    def test_original_workbook_import_reconciles_and_deduplicates(self) -> None:
        content = Path(SOURCE_XLSX).read_bytes()
        first = self.service.import_nfse(content)
        second = self.service.import_nfse(content)
        dashboard = self.service.dashboard("2026-08")

        self.assertEqual(first["inserted"], 29)
        self.assertEqual(first["active_total_cents"], 23_053_713)
        self.assertEqual(first["cancelled_total_cents"], 4_869_237)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["ignored"], 29)
        self.assertEqual(dashboard["totals"]["income_cents"], 23_053_713)


if __name__ == "__main__":
    unittest.main()
