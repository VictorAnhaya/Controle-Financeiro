from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

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
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        setup_request = Request(
            f"{self.base_url}/api/auth/setup",
            data=json.dumps(
                {"name": "Administrador", "username": "admin", "password": "senha-segura"}
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(setup_request) as response:
            self.assertEqual(response.status, 201)
        with self.opener.open(f"{self.base_url}/api/meta") as response:
            metadata = json.load(response)
        companies = {item["slug"]: item["id"] for item in metadata["companies"]}
        self.bolotti_id = companies["bolotti-reis"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_dashboard_and_static_application_are_available(self) -> None:
        with self.opener.open(f"{self.base_url}/api/dashboard?month=2026-08") as response:
            payload = json.load(response)
        self.assertEqual(payload["totals"]["income_cents"], 23_053_713)

        with self.opener.open(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("Bolotti Finance", html)

        with self.opener.open(f"{self.base_url}/api/ranking?month=2026-08&scope=month") as response:
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
                "company_id": self.bolotti_id,
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/transactions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request) as response:
            payload = json.load(response)
        self.assertEqual(payload["amount_cents"], 49_990)

    def test_clients_and_goals_are_available_through_api(self) -> None:
        with self.opener.open(f"{self.base_url}/api/clients?month=2026-08") as response:
            clients = json.load(response)
        self.assertEqual(clients["summary"]["active_count"], 17)

        client_request = Request(
            f"{self.base_url}/api/clients",
            data=json.dumps(
                {
                    "name": "Novo Cliente API",
                    "tax_id": "12345678000190",
                    "email": "financeiro@example.com",
                    "acquisition_date": "2026-09-01",
                    "status": "active",
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(client_request) as response:
            client = json.load(response)
        self.assertEqual(client["name"], "Novo Cliente API")

        update_client = Request(
            f"{self.base_url}/api/clients/{client['id']}",
            data=json.dumps({"status": "inactive"}).encode("utf-8"),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(update_client) as response:
            updated_client = json.load(response)
        self.assertEqual(updated_client["status"], "inactive")

        goal_request = Request(
            f"{self.base_url}/api/goals",
            data=json.dumps(
                {
                    "month": "2026-08",
                    "revenue_target": "250000,00",
                    "expense_limit": "20000,00",
                    "new_clients_target": 20,
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(goal_request) as response:
            goals = json.load(response)
        self.assertEqual(goals["goal"]["revenue_target_cents"], 25_000_000)
        self.assertEqual(goals["actual"]["new_clients"], 17)

    def test_xml_document_can_be_analyzed_and_posted(self) -> None:
        xml = (PROJECT_ROOT / "tests" / "fixtures" / "nfe_sample.xml").read_bytes()
        analyze_request = Request(
            f"{self.base_url}/api/documents/analyze",
            data=xml,
            method="POST",
            headers={
                "Content-Type": "application/xml",
                "X-Filename": "nota-123.xml",
                "X-Company-Id": str(self.bolotti_id),
            },
        )
        with self.opener.open(analyze_request) as response:
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
        with self.opener.open(post_request) as response:
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
        )
        application = FinanceHttpApplication(config, service)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), application._handler_class())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.admin_opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_health_check_does_not_require_authentication(self) -> None:
        with urlopen(f"{self.base_url}/api/health") as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_setup_login_and_user_administration(self) -> None:
        with urlopen(f"{self.base_url}/api/auth/status") as response:
            status = json.load(response)
        self.assertTrue(status["setup_required"])
        self.assertFalse(status["authenticated"])

        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/api/meta")
        self.assertEqual(context.exception.code, 401)

        setup_request = Request(
            f"{self.base_url}/api/auth/setup",
            data=json.dumps(
                {"name": "Administrador", "username": "admin", "password": "senha-segura"}
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.admin_opener.open(setup_request) as response:
            setup = json.load(response)
        self.assertEqual(setup["user"]["role"], "admin")

        with self.admin_opener.open(f"{self.base_url}/api/meta") as response:
            self.assertEqual(response.status, 200)

        create_user = Request(
            f"{self.base_url}/api/users",
            data=json.dumps(
                {
                    "name": "Maria Silva",
                    "username": "maria",
                    "password": "outra-senha",
                    "role": "user",
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.admin_opener.open(create_user) as response:
            created = json.load(response)
        self.assertEqual(created["username"], "maria")

        update_user = Request(
            f"{self.base_url}/api/users/{created['id']}",
            data=json.dumps({"name": "Maria Souza", "role": "user", "is_active": True}).encode("utf-8"),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with self.admin_opener.open(update_user) as response:
            updated = json.load(response)
        self.assertEqual(updated["name"], "Maria Souza")

        user_opener = build_opener(HTTPCookieProcessor(CookieJar()))
        login_request = Request(
            f"{self.base_url}/api/auth/login",
            data=json.dumps({"username": "maria", "password": "outra-senha"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with user_opener.open(login_request) as response:
            logged_in = json.load(response)
        self.assertEqual(logged_in["user"]["role"], "user")

        with user_opener.open(f"{self.base_url}/api/meta") as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as forbidden:
            user_opener.open(f"{self.base_url}/api/users")
        self.assertEqual(forbidden.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
