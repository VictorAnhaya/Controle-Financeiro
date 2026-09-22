from __future__ import annotations

import json
import mimetypes
import traceback
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .auth import AuthError, AuthService
from .config import AppConfig
from .document_reader import DocumentReadError
from .importer import SpreadsheetImportError
from .service import DuplicateDocumentError, FinanceService, ValidationError


class FinanceHttpApplication:
    def __init__(self, config: AppConfig, service: FinanceService):
        self.config = config
        self.service = service
        self.auth = AuthService(service.repository.database)

    def serve(self) -> None:
        handler_class = self._handler_class()
        server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        print(f"Bolotti Finance disponível em http://{self.config.host}:{self.config.port}")
        print("Pressione Ctrl+C para encerrar.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor encerrado.")
        finally:
            server.server_close()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        service = self.service
        auth = self.auth
        config = self.config

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "BolottiFinance/4.0"

            def log_message(self, format: str, *args: Any) -> None:
                print(f"[{self.log_date_time_string()}] {format % args}")

            def do_GET(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    if parsed.path == "/api/health":
                        return self._json({"status": "ok"})
                    if parsed.path == "/api/auth/status":
                        user = self._current_user()
                        return self._json(
                            {
                                "setup_required": auth.setup_required(),
                                "authenticated": user is not None,
                                "user": user,
                            }
                        )
                    if parsed.path.startswith("/api/"):
                        user = self._require_user()
                        if user is None:
                            return
                    else:
                        return self._serve_static(parsed.path)
                    if parsed.path == "/api/users":
                        if self._require_admin(user) is None:
                            return
                        return self._json(auth.list_users())
                    if parsed.path == "/api/meta":
                        return self._json(service.metadata())
                    if parsed.path == "/api/dashboard":
                        query = self._query(parsed.query)
                        return self._json(service.dashboard(query.get("month", "")))
                    if parsed.path == "/api/ranking":
                        query = self._query(parsed.query)
                        return self._json(
                            service.client_ranking(
                                query.get("month", ""), query.get("scope", "month")
                            )
                        )
                    if parsed.path == "/api/transactions":
                        return self._json(service.list_transactions(self._query(parsed.query)))
                    if parsed.path == "/api/budgets":
                        query = self._query(parsed.query)
                        return self._json(service.list_budgets(query.get("month", "")))
                    if parsed.path == "/api/documents":
                        return self._json(service.list_documents())
                    document_file_id = self._document_action_id(parsed.path, "file")
                    if document_file_id is not None:
                        resolved_file = service.document_file(document_file_id)
                        if resolved_file is None:
                            return self._json({"error": "Documento não encontrado."}, HTTPStatus.NOT_FOUND)
                        file_path, mime_type, file_name = resolved_file
                        safe_download_name = file_name.replace('"', "").replace("\r", "").replace("\n", "")
                        return self._bytes(
                            file_path.read_bytes(),
                            mime_type,
                            headers={"Content-Disposition": f'inline; filename="{safe_download_name}"'},
                        )
                    if parsed.path == "/api/reports/export.csv":
                        data = service.export_csv(self._query(parsed.query))
                        return self._bytes(
                            data,
                            "text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="relatorio-financeiro.csv"'},
                        )
                    return self._json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)
                except Exception as exc:
                    self._handle_exception(exc)

            def do_POST(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    if parsed.path == "/api/auth/setup":
                        user, token = auth.setup_admin(self._json_body())
                        return self._json(
                            {"user": user},
                            HTTPStatus.CREATED,
                            headers={"Set-Cookie": self._session_cookie(token)},
                        )
                    if parsed.path == "/api/auth/login":
                        payload = self._json_body()
                        user, token = auth.authenticate(
                            str(payload.get("username") or ""),
                            str(payload.get("password") or ""),
                        )
                        return self._json(
                            {"user": user},
                            headers={"Set-Cookie": self._session_cookie(token)},
                        )
                    if parsed.path == "/api/auth/logout":
                        auth.logout(self._session_token())
                        return self._json(
                            {"ok": True}, headers={"Set-Cookie": self._clear_session_cookie()}
                        )
                    user = self._require_user()
                    if user is None:
                        return
                    if parsed.path == "/api/users":
                        if self._require_admin(user) is None:
                            return
                        return self._json(auth.create_user(self._json_body()), HTTPStatus.CREATED)
                    if parsed.path == "/api/transactions":
                        return self._json(service.create_transaction(self._json_body()), HTTPStatus.CREATED)
                    if parsed.path == "/api/budgets":
                        return self._json(service.upsert_budget(self._json_body()), HTTPStatus.CREATED)
                    if parsed.path == "/api/import/nfse":
                        content = self._raw_body(config.max_upload_bytes)
                        return self._json(service.import_nfse(content), HTTPStatus.CREATED)
                    if parsed.path == "/api/documents/analyze":
                        content = self._raw_body(config.max_upload_bytes)
                        file_name = unquote(self.headers.get("X-Filename", "documento"))
                        mime_type = self.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0]
                        return self._json(
                            service.analyze_document(file_name, mime_type, content),
                            HTTPStatus.CREATED,
                        )
                    document_id = self._document_action_id(parsed.path, "post")
                    if document_id is not None:
                        result = service.create_transaction_from_document(document_id, self._json_body())
                        if result is None:
                            return self._json({"error": "Documento não encontrado."}, HTTPStatus.NOT_FOUND)
                        return self._json(result, HTTPStatus.CREATED)
                    self._json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)
                except Exception as exc:
                    self._handle_exception(exc)

            def do_PUT(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    user = self._require_user()
                    if user is None:
                        return
                    user_id = self._user_id(parsed.path)
                    if user_id is not None:
                        if self._require_admin(user) is None:
                            return
                        updated_user = auth.update_user(user_id, self._json_body(), user["id"])
                        if updated_user is None:
                            return self._json(
                                {"error": "Usuário não encontrado."}, HTTPStatus.NOT_FOUND
                            )
                        return self._json(updated_user)
                    transaction_id = self._transaction_id(parsed.path)
                    if transaction_id is None:
                        return self._json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)
                    updated = service.update_transaction(transaction_id, self._json_body())
                    if updated is None:
                        return self._json({"error": "Lançamento não encontrado."}, HTTPStatus.NOT_FOUND)
                    return self._json(updated)
                except Exception as exc:
                    self._handle_exception(exc)

            def do_DELETE(self) -> None:
                try:
                    if self._require_user() is None:
                        return
                    parsed = urlparse(self.path)
                    transaction_id = self._transaction_id(parsed.path)
                    if transaction_id is None:
                        return self._json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)
                    if not service.delete_transaction(transaction_id):
                        return self._json({"error": "Lançamento não encontrado."}, HTTPStatus.NOT_FOUND)
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                except Exception as exc:
                    self._handle_exception(exc)

            def _session_token(self) -> str | None:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                except Exception:
                    return None
                morsel = cookie.get("finance_session")
                return morsel.value if morsel else None

            def _current_user(self) -> dict[str, Any] | None:
                return auth.session_user(self._session_token())

            def _require_user(self) -> dict[str, Any] | None:
                user = self._current_user()
                if user is None:
                    self._json(
                        {"error": "Sua sessão expirou. Entre novamente."},
                        HTTPStatus.UNAUTHORIZED,
                    )
                return user

            def _require_admin(self, user: dict[str, Any]) -> dict[str, Any] | None:
                if user["role"] != "admin":
                    self._json(
                        {"error": "Apenas administradores podem gerenciar usuários."},
                        HTTPStatus.FORBIDDEN,
                    )
                    return None
                return user

            def _session_cookie(self, token: str) -> str:
                forwarded_https = (
                    self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
                    == "https"
                )
                secure = forwarded_https or self.headers.get("Host", "").endswith(".onrender.com")
                parts = [
                    f"finance_session={token}",
                    "Path=/",
                    "HttpOnly",
                    "SameSite=Lax",
                    f"Max-Age={AuthService.SESSION_HOURS * 3600}",
                ]
                if secure:
                    parts.append("Secure")
                return "; ".join(parts)

            def _clear_session_cookie(self) -> str:
                parts = [
                    "finance_session=",
                    "Path=/",
                    "HttpOnly",
                    "SameSite=Lax",
                    "Max-Age=0",
                ]
                forwarded_https = (
                    self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
                    == "https"
                )
                if forwarded_https or self.headers.get("Host", "").endswith(".onrender.com"):
                    parts.append("Secure")
                return "; ".join(parts)

            @staticmethod
            def _transaction_id(path: str) -> int | None:
                prefix = "/api/transactions/"
                if not path.startswith(prefix):
                    return None
                try:
                    return int(path.removeprefix(prefix))
                except ValueError:
                    return None

            @staticmethod
            def _user_id(path: str) -> int | None:
                prefix = "/api/users/"
                if not path.startswith(prefix):
                    return None
                try:
                    return int(path.removeprefix(prefix))
                except ValueError:
                    return None

            @staticmethod
            def _document_action_id(path: str, action: str) -> int | None:
                prefix = "/api/documents/"
                suffix = f"/{action}"
                if not path.startswith(prefix) or not path.endswith(suffix):
                    return None
                raw_id = path[len(prefix) : -len(suffix)]
                try:
                    return int(raw_id)
                except ValueError:
                    return None

            @staticmethod
            def _query(raw_query: str) -> dict[str, str]:
                return {key: values[-1] for key, values in parse_qs(raw_query).items() if values}

            def _json_body(self) -> dict[str, Any]:
                content_type = self.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    raise ValidationError("O corpo da requisição deve ser JSON.")
                raw = self._raw_body(1024 * 1024)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValidationError("JSON inválido.") from exc
                if not isinstance(payload, dict):
                    raise ValidationError("O conteúdo enviado deve ser um objeto JSON.")
                return payload

            def _raw_body(self, maximum: int) -> bytes:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValidationError("Tamanho de arquivo inválido.") from exc
                if length <= 0:
                    raise ValidationError("Nenhum conteúdo foi enviado.")
                if length > maximum:
                    raise ValidationError(f"O arquivo excede o limite de {maximum // 1024 // 1024} MB.")
                return self.rfile.read(length)

            def _serve_static(self, request_path: str) -> None:
                relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
                target = (config.static_path / relative).resolve()
                static_root = config.static_path.resolve()
                if static_root not in target.parents and target != static_root:
                    return self._json({"error": "Arquivo não encontrado."}, HTTPStatus.NOT_FOUND)
                if not target.is_file():
                    target = config.static_path / "index.html"
                content_type, _ = mimetypes.guess_type(target.name)
                self._bytes(target.read_bytes(), content_type or "application/octet-stream")

            def _json(
                self,
                payload: Any,
                status: HTTPStatus = HTTPStatus.OK,
                headers: dict[str, str] | None = None,
            ) -> None:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self._bytes(
                    data,
                    "application/json; charset=utf-8",
                    status=status,
                    headers=headers,
                )

            def _bytes(
                self,
                data: bytes,
                content_type: str,
                status: HTTPStatus = HTTPStatus.OK,
                headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def _handle_exception(self, exc: Exception) -> None:
                if isinstance(exc, AuthError):
                    payload: dict[str, Any] = {"error": str(exc)}
                    if exc.fields:
                        payload["fields"] = exc.fields
                    return self._json(payload, HTTPStatus(exc.status))
                if isinstance(exc, (ValidationError, SpreadsheetImportError, DocumentReadError)):
                    payload: dict[str, Any] = {"error": str(exc)}
                    if isinstance(exc, ValidationError) and exc.fields:
                        payload["fields"] = exc.fields
                    if isinstance(exc, DuplicateDocumentError):
                        payload["existing_document"] = exc.existing_document
                        return self._json(payload, HTTPStatus.CONFLICT)
                    return self._json(payload, HTTPStatus.UNPROCESSABLE_ENTITY)
                traceback.print_exc()
                self._json({"error": "Ocorreu um erro interno. Tente novamente."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        return RequestHandler
