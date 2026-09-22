from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from finance_app.config import AppConfig
from finance_app.database import Database
from finance_app.http import FinanceHttpApplication
from finance_app.repository import FinanceRepository
from finance_app.service import FinanceService


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class HttpApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        database = Database(temporary_path / "http-test.db")
        database.migrate()
        service = FinanceService(FinanceRepository(database))
        service.seed_initial_data(PROJECT_ROOT / "data" / "initial_transactions.json")
        config = AppConfig(
            host="127.0.0.1",
            port=0,
            database_path=temporary_path / "http-test.db",
            seed_path=PROJECT_ROOT / "data" / "initial_transactions.json",
            static_path=PROJECT_ROOT / "finance_app" / "static",
            documents_path=temporary_path / "documents",
        )
        application = FinanceHttpApplication(config, service)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), application._handler_class())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_dashboard_and_static_application_are_available(self) -> None:
        with urlopen(f"{self.base_url}/api/dashboard?month=2026-08") as response:
            payload = json.load(response)
        self.assertEqual(payload["totals"]["income_cents"], 23_053_713)

        with urlopen(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("Bolotti Finance", html)

        with urlopen(f"{self.base_url}/api/ranking?month=2026-08&scope=month") as response:
            ranking = json.load(response)
        self.assertEqual(ranking["summary"]["client_count"], 17)
        self.assertEqual(ranking["items"][0]["position"], 1)

    def test_transaction_can_be_created_through_api(self) -> None:
        body = json.dumps(
            {
                "kind": "expense",
                "description": "Internet do escritório",
                "amount": "499,90",
                "transaction_date": "2026-08-20",
                "status": "paid",
                "category": "Tecnologia e sistemas",
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/transactions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["amount_cents"], 49_990)

    def test_xml_document_can_be_analyzed_and_posted(self) -> None:
        xml = (PROJECT_ROOT / "tests" / "fixtures" / "nfe_sample.xml").read_bytes()
        analyze_request = Request(
            f"{self.base_url}/api/documents/analyze",
            data=xml,
            method="POST",
            headers={"Content-Type": "application/xml", "X-Filename": "nota-123.xml"},
        )
        with urlopen(analyze_request) as response:
            document = json.load(response)
        self.assertEqual(document["document_number"], "123")

        body = json.dumps(
            {
                "kind": "expense",
                "description": "NF-e 123 — FORNECEDOR EXEMPLO LTDA",
                "amount": "1234,56",
                "transaction_date": "2026-08-30",
                "status": "pending",
                "category": "Outros",
            }
        ).encode("utf-8")
        post_request = Request(
            f"{self.base_url}/api/documents/{document['id']}/post",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(post_request) as response:
            linked = json.load(response)
        self.assertEqual(linked["transaction"]["amount_cents"], 123_456)
        self.assertEqual(linked["document"]["extraction_status"], "linked")


class HttpAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        database = Database(temporary_path / "auth-test.db")
        database.migrate()
        service = FinanceService(FinanceRepository(database))
        config = AppConfig(
            host="127.0.0.1",
            port=0,
            database_path=temporary_path / "auth-test.db",
            seed_path=PROJECT_ROOT / "data" / "initial_transactions.json",
            static_path=PROJECT_ROOT / "finance_app" / "static",
            documents_path=temporary_path / "documents",
            auth_username="equipe",
            auth_password="senha-segura",
        )
        application = FinanceHttpApplication(config, service)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), application._handler_class())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_health_check_does_not_require_authentication(self) -> None:
        with urlopen(f"{self.base_url}/api/health") as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_application_requires_valid_credentials(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/api/meta")
        self.assertEqual(context.exception.code, 401)

        credentials = base64.b64encode(b"equipe:senha-segura").decode("ascii")
        request = Request(
            f"{self.base_url}/api/meta",
            headers={"Authorization": f"Basic {credentials}"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
