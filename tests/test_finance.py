from __future__ import annotations

import os
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
            }
        )
        after_cancelled = self.service.client_ranking("2026-08")
        self.assertEqual(after_cancelled["items"][1]["total_cents"], 5_065_391)

    def test_xml_document_is_extracted_linked_and_deduplicated(self) -> None:
        xml_path = PROJECT_ROOT / "tests" / "fixtures" / "nfe_sample.xml"
        content = xml_path.read_bytes()
        document = self.service.analyze_document("nota-123.xml", "application/xml", content)

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
            self.service.analyze_document("copia.xml", "application/xml", content)

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
            self.service.analyze_document("arquivo.txt", "text/plain", b"conteudo qualquer")

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
            "nota-456.pdf", "application/pdf", output.getvalue()
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
