from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .document_reader import analyze_fiscal_document
from .importer import build_nfse_external_key, parse_nfse_workbook
from .repository import FinanceRepository


class ValidationError(ValueError):
    def __init__(self, message: str, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.fields = fields or {}


class DuplicateDocumentError(ValidationError):
    def __init__(self, existing_document: dict[str, Any]):
        super().__init__(
            f"Este arquivo já foi anexado como documento #{existing_document['id']}.",
            {"file": "Documento duplicado."},
        )
        self.existing_document = existing_document


class FinanceService:
    DEFAULT_EXPENSE_CATEGORIES = [
        "Folha e benefícios",
        "Tributos e taxas",
        "Aluguel e condomínio",
        "Tecnologia e sistemas",
        "Serviços terceirizados",
        "Marketing e comercial",
        "Viagens e deslocamentos",
        "Material de escritório",
        "Outros",
    ]
    DEFAULT_COST_CENTERS = ["Administrativo", "Financeiro", "Jurídico", "TI", "Comercial"]

    def __init__(self, repository: FinanceRepository, documents_path: Path | None = None):
        self.repository = repository
        self.documents_path = Path(documents_path or repository.database.path.parent / "documents")
        self.documents_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _money_to_cents(value: Any, field: str = "amount") -> int:
        try:
            normalized = str(value).strip().replace("R$", "").replace(" ", "")
            if "," in normalized:
                normalized = normalized.replace(".", "").replace(",", ".")
            amount = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("Confira os valores informados.", {field: "Valor inválido."}) from exc
        cents = int(amount * 100)
        if cents <= 0:
            raise ValidationError("Confira os valores informados.", {field: "Informe um valor maior que zero."})
        return cents

    @staticmethod
    def _clean_text(value: Any, *, required: bool = False, max_length: int = 180) -> str | None:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if required and not text:
            raise ValidationError("Preencha os campos obrigatórios.")
        return text[:max_length] or None

    @staticmethod
    def _validate_date(value: Any, field: str, *, required: bool = False) -> str | None:
        text = str(value or "").strip()
        if not text and not required:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValidationError("Confira as datas informadas.", {field: "Use uma data válida."}) from exc

    @staticmethod
    def _validate_month(value: Any) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
            raise ValidationError("Informe um mês válido no formato AAAA-MM.")
        return text

    def seed_initial_data(self, seed_path: Path) -> None:
        if seed_path.exists():
            payload = json.loads(seed_path.read_text(encoding="utf-8"))
            rows: list[dict[str, Any]] = []
            for item in payload:
                row = dict(item)
                row["amount_cents"] = self._money_to_cents(row.pop("amount"))
                if not row.get("external_key") and row.get("source", "").startswith("import:finance-report"):
                    cnpj_match = re.search(r"CNPJ:\s*([^|]+)", row.get("notes", ""))
                    if cnpj_match:
                        row["counterparty_tax_id"] = re.sub(r"\D", "", cnpj_match.group(1))
                        row["external_key"] = build_nfse_external_key(
                            row["transaction_date"],
                            cnpj_match.group(1).strip(),
                            row["amount_cents"],
                            row["status"],
                        )
                rows.append(row)
            self.repository.create_transactions_ignoring_duplicates(rows)
        self.repository.backfill_counterparty_tax_ids()

    def metadata(self) -> dict[str, Any]:
        meta = self.repository.metadata()
        meta["categories"] = sorted(set(meta["categories"] + self.DEFAULT_EXPENSE_CATEGORIES))
        meta["cost_centers"] = sorted(set(meta["cost_centers"] + self.DEFAULT_COST_CENTERS))
        return meta

    def list_transactions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        self.repository.refresh_overdue(date.today())
        if filters.get("month"):
            filters["month"] = self._validate_month(filters["month"])
        return self.repository.list_transactions(filters)

    def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validated_transaction(payload)
        values["source"] = "manual"
        return self.repository.create_transaction(values)

    def update_transaction(self, transaction_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.repository.get_transaction(transaction_id)
        if current is None:
            return None
        merged = {**current, **payload}
        values = self._validated_transaction(merged)
        values["source"] = current["source"]
        values["external_key"] = current["external_key"]
        return self.repository.update_transaction(transaction_id, values)

    def delete_transaction(self, transaction_id: int) -> bool:
        return self.repository.delete_transaction(transaction_id)

    def _validated_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", "")).strip()
        status = str(payload.get("status", "")).strip()
        if kind not in {"income", "expense"}:
            raise ValidationError("Tipo de lançamento inválido.", {"kind": "Escolha receita ou despesa."})
        if status not in {"paid", "pending", "overdue", "cancelled"}:
            raise ValidationError("Status inválido.", {"status": "Escolha um status válido."})

        description = self._clean_text(payload.get("description"), required=True)
        category = self._clean_text(payload.get("category"), required=True, max_length=80)
        transaction_date = self._validate_date(payload.get("transaction_date"), "transaction_date", required=True)
        due_date = self._validate_date(payload.get("due_date"), "due_date")
        assert description and category and transaction_date

        if status == "pending" and due_date and due_date < date.today().isoformat():
            status = "overdue"

        raw_tax_id = re.sub(r"\D", "", str(payload.get("counterparty_tax_id") or ""))
        if raw_tax_id and len(raw_tax_id) not in {11, 14}:
            raise ValidationError(
                "Confira a identificação do cliente ou fornecedor.",
                {"counterparty_tax_id": "Informe um CPF ou CNPJ válido."},
            )

        return {
            "kind": kind,
            "description": description,
            "amount_cents": self._money_to_cents(payload.get("amount"), "amount"),
            "transaction_date": transaction_date,
            "due_date": due_date,
            "status": status,
            "category": category,
            "cost_center": self._clean_text(payload.get("cost_center"), max_length=80),
            "counterparty": self._clean_text(payload.get("counterparty"), max_length=180),
            "counterparty_tax_id": raw_tax_id or None,
            "document_number": self._clean_text(payload.get("document_number"), max_length=60),
            "notes": self._clean_text(payload.get("notes"), max_length=1000),
        }

    def dashboard(self, month: str) -> dict[str, Any]:
        resolved_month = self._validate_month(month)
        self.repository.refresh_overdue(date.today())
        result = self.repository.dashboard(resolved_month)
        totals = result["totals"]
        totals["balance_cents"] = int(totals["income_cents"]) - int(totals["expense_cents"])
        budget_limit = int(result["budget_limit_cents"])
        totals["budget_usage_percent"] = (
            round(int(totals["expense_cents"]) / budget_limit * 100, 1) if budget_limit else 0
        )
        return result

    def list_budgets(self, month: str) -> list[dict[str, Any]]:
        return self.repository.list_budgets(self._validate_month(month))

    def client_ranking(self, month: str, scope: str = "month") -> dict[str, Any]:
        if scope not in {"month", "all"}:
            raise ValidationError("Escopo de ranking inválido.")
        resolved_month = self._validate_month(month) if scope == "month" else None
        rows = self.repository.client_ranking(resolved_month)
        total_cents = sum(int(row["total_cents"]) for row in rows)
        items = []
        for position, row in enumerate(rows, start=1):
            item = dict(row)
            item["position"] = position
            item["share_percent"] = round(item["total_cents"] / total_cents * 100, 2) if total_cents else 0
            items.append(item)
        total_invoices = sum(int(row["invoice_count"]) for row in rows)
        top3_cents = sum(int(row["total_cents"]) for row in rows[:3])
        top5_cents = sum(int(row["total_cents"]) for row in rows[:5])
        return {
            "scope": scope,
            "month": resolved_month,
            "items": items,
            "summary": {
                "client_count": len(items),
                "invoice_count": total_invoices,
                "total_cents": total_cents,
                "average_ticket_cents": round(total_cents / total_invoices) if total_invoices else 0,
                "top3_cents": top3_cents,
                "top3_percent": round(top3_cents / total_cents * 100, 2) if total_cents else 0,
                "top5_cents": top5_cents,
                "top5_percent": round(top5_cents / total_cents * 100, 2) if total_cents else 0,
            },
        }

    def upsert_budget(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = self._validate_month(payload.get("month"))
        category = self._clean_text(payload.get("category"), required=True, max_length=80)
        assert category
        limit_cents = self._money_to_cents(payload.get("limit"), "limit")
        return self.repository.upsert_budget(month, category, limit_cents)

    def import_nfse(self, content: bytes) -> dict[str, Any]:
        rows = parse_nfse_workbook(content)
        inserted, ignored = self.repository.create_transactions_ignoring_duplicates(rows)
        active_total = sum(row["amount_cents"] for row in rows if row["status"] == "paid")
        cancelled_total = sum(row["amount_cents"] for row in rows if row["status"] == "cancelled")
        return {
            "found": len(rows),
            "inserted": inserted,
            "ignored": ignored,
            "active_total_cents": active_total,
            "cancelled_total_cents": cancelled_total,
        }

    @staticmethod
    def _public_document(document: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in document.items() if key != "storage_name"}

    def list_documents(self) -> list[dict[str, Any]]:
        return [self._public_document(item) for item in self.repository.list_documents()]

    def analyze_document(self, file_name: str, mime_type: str, content: bytes) -> dict[str, Any]:
        safe_name = Path(file_name or "documento").name[:180]
        if not content:
            raise ValidationError("O arquivo enviado está vazio.")
        digest = hashlib.sha256(content).hexdigest()
        existing = self.repository.find_document_by_hash(digest)
        if existing is not None:
            raise DuplicateDocumentError(self._public_document(existing))

        parsed = analyze_fiscal_document(safe_name, mime_type, content)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".xml", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            suffix = ".bin"
        storage_name = f"{digest}{suffix}"
        target = self.documents_path / storage_name
        temporary = self.documents_path / f".{storage_name}.upload"
        temporary.write_bytes(content)
        temporary.replace(target)

        extracted = parsed.to_dict()
        required_fields_present = bool(parsed.issuer_name and parsed.issue_date and parsed.total_cents)
        status = "analyzed" if parsed.confidence >= 0.85 and required_fields_present else "needs_review"
        try:
            document = self.repository.create_document(
                {
                    "file_name": safe_name,
                    "storage_name": storage_name,
                    "mime_type": mime_type or "application/octet-stream",
                    "file_size_bytes": len(content),
                    "sha256": digest,
                    "document_type": parsed.document_type,
                    "issuer_name": parsed.issuer_name,
                    "issuer_tax_id": parsed.issuer_tax_id,
                    "recipient_name": parsed.recipient_name,
                    "recipient_tax_id": parsed.recipient_tax_id,
                    "document_number": parsed.document_number,
                    "access_key": parsed.access_key,
                    "issue_date": parsed.issue_date,
                    "total_cents": parsed.total_cents,
                    "extraction_status": status,
                    "confidence": parsed.confidence,
                    "extracted_json": json.dumps(extracted, ensure_ascii=False),
                }
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self._public_document(document)

    def create_transaction_from_document(
        self, document_id: int, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        document = self.repository.get_document(document_id)
        if document is None:
            return None
        if document["transaction_id"] is not None:
            raise ValidationError("Este documento já está vinculado a um lançamento.")
        enriched_payload = dict(payload)
        if enriched_payload.get("kind") == "income":
            if not enriched_payload.get("counterparty"):
                enriched_payload["counterparty"] = document.get("recipient_name") or document.get("issuer_name")
            if not enriched_payload.get("counterparty_tax_id"):
                enriched_payload["counterparty_tax_id"] = document.get("recipient_tax_id") or document.get("issuer_tax_id")
        else:
            if not enriched_payload.get("counterparty"):
                enriched_payload["counterparty"] = document.get("issuer_name")
            if not enriched_payload.get("counterparty_tax_id"):
                enriched_payload["counterparty_tax_id"] = document.get("issuer_tax_id")
        values = self._validated_transaction(enriched_payload)
        values["source"] = f"fiscal-document:{document_id}"
        values["external_key"] = f"fiscal-document:{document['sha256']}"
        linked = self.repository.create_transaction_for_document(document_id, values)
        if linked is None:
            return None
        transaction, updated_document = linked
        return {"transaction": transaction, "document": self._public_document(updated_document)}

    def document_file(self, document_id: int) -> tuple[Path, str, str] | None:
        document = self.repository.get_document(document_id)
        if document is None:
            return None
        path = (self.documents_path / document["storage_name"]).resolve()
        if self.documents_path.resolve() not in path.parents or not path.is_file():
            return None
        return path, document["mime_type"], document["file_name"]

    def export_csv(self, filters: dict[str, Any]) -> bytes:
        rows = self.list_transactions(filters)
        output = io.StringIO(newline="")
        output.write("\ufeff")
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "Data",
                "Tipo",
                "Descrição",
                "Valor",
                "Status",
                "Categoria",
                "Centro de custo",
                "Fornecedor/Cliente",
                "CNPJ/CPF",
                "Documento",
                "Vencimento",
                "Observações",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["transaction_date"],
                    "Receita" if row["kind"] == "income" else "Despesa",
                    row["description"],
                    f"{row['amount_cents'] / 100:.2f}".replace(".", ","),
                    row["status"],
                    row["category"],
                    row["cost_center"] or "",
                    row["counterparty"] or "",
                    row["counterparty_tax_id"] or "",
                    row["document_number"] or "",
                    row["due_date"] or "",
                    row["notes"] or "",
                ]
            )
        return output.getvalue().encode("utf-8")
