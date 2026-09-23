from __future__ import annotations

import csv
import calendar
import hashlib
import io
import json
import re
import unicodedata
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
            companies = {item["slug"]: item["id"] for item in self.repository.list_companies()}
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
                notes = str(row.get("notes") or "")
                if "São José dos Pinhais" in notes or "Sao Jose dos Pinhais" in notes:
                    row["company_id"] = companies["brc"]
                elif "Curitiba" in notes:
                    row["company_id"] = companies["wbk"]
                if row.get("company_id") and row.get("external_key"):
                    row["external_key"] = f"company:{row['company_id']}:{row['external_key']}"
                rows.append(row)
            self.repository.create_transactions_ignoring_duplicates(rows)
        self.repository.backfill_counterparty_tax_ids()
        self.repository.backfill_companies_by_municipality()
        self.repository.sync_clients_from_income()

    def _resolve_company(self, value: Any, *, required: bool = False) -> int | None:
        text = str(value or "").strip()
        if text in {"", "all"}:
            if required:
                raise ValidationError(
                    "Selecione a empresa.", {"company_id": "Escolha uma empresa cadastrada."}
                )
            return None
        try:
            company_id = int(text)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Empresa inválida.") from exc
        if self.repository.get_company(company_id) is None:
            raise ValidationError("Empresa não encontrada.")
        return company_id

    @staticmethod
    def _scope_key(company_id: int | None) -> str:
        return f"company:{company_id}" if company_id is not None else "all"

    def metadata(self) -> dict[str, Any]:
        meta = self.repository.metadata()
        meta["categories"] = sorted(set(meta["categories"] + self.DEFAULT_EXPENSE_CATEGORIES))
        meta["cost_centers"] = sorted(set(meta["cost_centers"] + self.DEFAULT_COST_CENTERS))
        meta["clients"] = self.repository.client_options()
        return meta

    def list_companies(self) -> list[dict[str, Any]]:
        return self.repository.list_companies()

    def create_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._clean_text(payload.get("name"), required=True, max_length=120)
        municipality = self._clean_text(payload.get("municipality"), max_length=120)
        assert name
        normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
        if not slug:
            raise ValidationError("Informe um nome de empresa válido.", {"name": "Use letras ou números."})
        created = self.repository.create_company(name, slug, municipality)
        if created is None:
            raise ValidationError("Já existe uma empresa com esse nome.", {"name": "Use outro nome."})
        return created

    def delete_company(self, company_id: int) -> bool:
        company = self.repository.get_company(company_id)
        if company is None:
            return False
        deleted, usage = self.repository.delete_company(company_id)
        if not deleted and any(usage.values()):
            labels = {
                "transactions": "lançamento(s)",
                "documents": "nota(s) fiscal(is)",
                "goals": "meta(s)",
                "budgets": "orçamento(s)",
            }
            details = ", ".join(
                f"{count} {labels[key]}" for key, count in usage.items() if count
            )
            raise ValidationError(
                f"A empresa não pode ser excluída porque possui {details}. "
                "Exclua os registros vinculados antes de tentar novamente."
            )
        return deleted

    def list_transactions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        self.repository.refresh_overdue(date.today())
        if filters.get("month"):
            filters["month"] = self._validate_month(filters["month"])
        filters["company_id"] = self._resolve_company(filters.get("company"))
        return self.repository.list_transactions(filters)

    def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validated_transaction(payload)
        values["source"] = "manual"
        created = self.repository.create_transaction(values)
        if values["kind"] == "income" and not values.get("client_id"):
            self.repository.sync_clients_from_income()
            created = self.repository.get_transaction(created["id"]) or created
        return created

    def update_transaction(self, transaction_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.repository.get_transaction(transaction_id)
        if current is None:
            return None
        merged = {**current, **payload}
        values = self._validated_transaction(merged)
        values["source"] = current["source"]
        values["external_key"] = current["external_key"]
        updated = self.repository.update_transaction(transaction_id, values)
        if updated and values["kind"] == "income" and not values.get("client_id"):
            self.repository.sync_clients_from_income()
            updated = self.repository.get_transaction(transaction_id) or updated
        return updated

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

        client_id: int | None = None
        if kind == "income" and str(payload.get("client_id") or "").strip():
            try:
                client_id = int(payload["client_id"])
            except (TypeError, ValueError) as exc:
                raise ValidationError("Cliente inválido.", {"client_id": "Selecione um cliente válido."}) from exc
            client = self.repository.get_client(client_id)
            if client is None:
                raise ValidationError("Cliente não encontrado.", {"client_id": "Selecione um cliente válido."})
            if client["status"] != "active":
                raise ValidationError("O cliente selecionado está inativo.", {"client_id": "Reative o cliente antes de usá-lo."})
            payload = dict(payload)
            payload["counterparty"] = client["name"]
            raw_tax_id = client["tax_id"] or ""

        return {
            "kind": kind,
            "description": description,
            "amount_cents": self._money_to_cents(payload.get("amount"), "amount"),
            "transaction_date": transaction_date,
            "due_date": due_date,
            "status": status,
            "category": category,
            "cost_center": self._clean_text(payload.get("cost_center"), max_length=80),
            "company_id": self._resolve_company(payload.get("company_id"), required=True),
            "client_id": client_id,
            "counterparty": self._clean_text(payload.get("counterparty"), max_length=180),
            "counterparty_tax_id": raw_tax_id or None,
            "document_number": self._clean_text(payload.get("document_number"), max_length=60),
            "notes": self._clean_text(payload.get("notes"), max_length=1000),
        }

    def list_clients(self, filters: dict[str, Any]) -> dict[str, Any]:
        month = self._validate_month(filters.get("month"))
        status = str(filters.get("status") or "").strip()
        if status not in {"", "active", "inactive"}:
            raise ValidationError("Status de cliente inválido.")
        search = self._clean_text(filters.get("search"), max_length=120) or ""
        company_id = self._resolve_company(filters.get("company"))
        return {
            "items": self.repository.list_clients(month, search, status, company_id),
            "summary": self.repository.client_summary(month, company_id),
            "month": month,
            "company_id": company_id,
        }

    def _validated_client(self, payload: dict[str, Any], current_id: int | None = None) -> dict[str, Any]:
        name = self._clean_text(payload.get("name"), required=True, max_length=180)
        assert name
        tax_id = re.sub(r"\D", "", str(payload.get("tax_id") or "")) or None
        if tax_id and len(tax_id) not in {11, 14}:
            raise ValidationError("Confira o CPF ou CNPJ.", {"tax_id": "Informe 11 ou 14 dígitos."})
        email = self._clean_text(payload.get("email"), max_length=180)
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValidationError("Confira o e-mail.", {"email": "Informe um e-mail válido."})
        status = str(payload.get("status") or "active").strip()
        if status not in {"active", "inactive"}:
            raise ValidationError("Status de cliente inválido.", {"status": "Escolha ativo ou inativo."})
        match_key = self.repository.client_match_key(name, tax_id)
        duplicate = self.repository.find_client_by_match_key(match_key)
        if duplicate and duplicate["id"] != current_id:
            raise ValidationError(
                "Já existe um cliente com esse nome ou CPF/CNPJ.",
                {"tax_id" if tax_id else "name": "Cliente já cadastrado."},
            )
        return {
            "name": name,
            "tax_id": tax_id,
            "match_key": match_key,
            "contact_name": self._clean_text(payload.get("contact_name"), max_length=180),
            "email": email,
            "phone": self._clean_text(payload.get("phone"), max_length=40),
            "acquisition_date": self._validate_date(payload.get("acquisition_date"), "acquisition_date"),
            "status": status,
            "notes": self._clean_text(payload.get("notes"), max_length=1000),
        }

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validated_client(payload)
        client = self.repository.create_client(values)
        self.repository.relink_client_transactions(client["id"], client["name"], client["tax_id"])
        return client

    def update_client(self, client_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.repository.get_client(client_id)
        if current is None:
            return None
        values = self._validated_client({**current, **payload}, current_id=client_id)
        client = self.repository.update_client(client_id, values)
        if client:
            self.repository.relink_client_transactions(client_id, client["name"], client["tax_id"])
        return client

    @staticmethod
    def _month_shift(month: str, delta: int) -> str:
        year, number = map(int, month.split("-"))
        index = year * 12 + number - 1 + delta
        return f"{index // 12:04d}-{index % 12 + 1:02d}"

    def upsert_goal(self, payload: dict[str, Any]) -> dict[str, Any]:
        month = self._validate_month(payload.get("month"))
        company_id = self._resolve_company(payload.get("company"))
        try:
            new_clients_target = int(payload.get("new_clients_target", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Confira a meta de clientes.", {"new_clients_target": "Informe um número inteiro."}) from exc
        if new_clients_target < 0:
            raise ValidationError("Confira a meta de clientes.", {"new_clients_target": "Não pode ser negativa."})
        self.repository.upsert_goal(
            {
                "scope_key": self._scope_key(company_id),
                "company_id": company_id,
                "month": month,
                "revenue_target_cents": self._money_to_cents(payload.get("revenue_target"), "revenue_target"),
                "expense_limit_cents": self._money_to_cents(payload.get("expense_limit"), "expense_limit"),
                "new_clients_target": new_clients_target,
                "notes": self._clean_text(payload.get("notes"), max_length=1000),
            }
        )
        return self.goal_projection(month, company_id)

    def goal_projection(self, month: str, company_id: int | None = None) -> dict[str, Any]:
        resolved = self._validate_month(month)
        scope_key = self._scope_key(company_id)
        today = date.today()
        current_month = today.strftime("%Y-%m")
        actual = self.repository.month_actuals(resolved, company_id)
        goal = self.repository.get_goal(resolved, scope_key)
        history: list[dict[str, Any]] = []
        for offset in range(-5, 1):
            history_month = self._month_shift(resolved, offset)
            values = self.repository.month_actuals(history_month, company_id)
            history.append({"month": history_month, **values, "goal": self.repository.get_goal(history_month, scope_key)})

        if resolved < current_month:
            projected_revenue = int(actual["revenue_cents"])
            projected_expense = int(actual["expense_cents"])
        elif resolved == current_month:
            days = calendar.monthrange(today.year, today.month)[1]
            projected_revenue = round(int(actual["revenue_cents"]) / today.day * days)
            projected_expense = round(int(actual["expense_cents"]) / today.day * days)
        else:
            prior = [
                self.repository.month_actuals(self._month_shift(resolved, offset), company_id)
                for offset in (-3, -2, -1)
            ]
            projected_revenue = round(sum(int(item["revenue_cents"]) for item in prior) / 3)
            projected_expense = round(sum(int(item["expense_cents"]) for item in prior) / 3)

        base_revenue = int(actual["revenue_cents"])
        base_notes = int(actual["invoice_count"])
        average_ticket = round(base_revenue / base_notes) if base_notes else 0
        scenarios = []
        for key, label, variation in (
            ("pessimistic", "Pessimista (-15%)", -0.15),
            ("base", "Base", 0.0),
            ("optimistic", "Otimista (+15%)", 0.15),
            ("strategic", "Meta estratégica (+25%)", 0.25),
        ):
            monthly = round(base_revenue * (1 + variation))
            notes_month = max(round(base_notes * (1 + variation)), 0)
            scenarios.append(
                {
                    "key": key,
                    "label": label,
                    "variation_percent": variation * 100,
                    "monthly_revenue_cents": monthly,
                    "annual_revenue_cents": monthly * 12,
                    "notes_month": notes_month,
                    "notes_year": notes_month * 12,
                    "average_ticket_cents": round(monthly / notes_month) if notes_month else 0,
                }
            )

        month_names = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
        ]
        seasonal = {1: (0.90, "Baixa (férias)"), 2: (0.95, "Estável"), 10: (1.05, "Alta (pré-fechamento)"), 11: (1.02, "Estável"), 12: (1.10, "Alta (fechamento)")}
        forecast_months: list[dict[str, Any]] = []
        accumulated = 0
        for offset in range(1, 7):
            forecast_key = self._month_shift(resolved, offset)
            year, number = map(int, forecast_key.split("-"))
            factor, seasonal_label = seasonal.get(number, (1.0, "Estável"))
            estimated_notes = max(round(base_notes * factor), 0)
            estimated_revenue = round(base_revenue * factor)
            accumulated += estimated_revenue
            forecast_months.append(
                {
                    "month": forecast_key,
                    "label": f"{month_names[number - 1]}/{year}",
                    "estimated_notes": estimated_notes,
                    "seasonality": seasonal_label,
                    "factor": factor,
                    "revenue_cents": estimated_revenue,
                    "accumulated_cents": accumulated,
                }
            )

        revenue_target = int(goal["revenue_target_cents"]) if goal else 0
        expense_limit = int(goal["expense_limit_cents"]) if goal else 0
        client_target = int(goal["new_clients_target"]) if goal else 0
        actual_revenue = int(actual["revenue_cents"])
        actual_expense = int(actual["expense_cents"])
        actual_clients = int(actual["new_clients"])
        return {
            "month": resolved,
            "company_id": company_id,
            "scope_key": scope_key,
            "goal": goal,
            "actual": actual,
            "projection": {
                "revenue_cents": projected_revenue,
                "expense_cents": projected_expense,
                "result_cents": projected_revenue - projected_expense,
            },
            "progress": {
                "revenue_percent": round(actual_revenue / revenue_target * 100, 1) if revenue_target else 0,
                "expense_percent": round(actual_expense / expense_limit * 100, 1) if expense_limit else 0,
                "clients_percent": round(actual_clients / client_target * 100, 1) if client_target else 0,
                "revenue_remaining_cents": max(revenue_target - actual_revenue, 0),
                "expense_available_cents": max(expense_limit - actual_expense, 0),
                "clients_remaining": max(client_target - actual_clients, 0),
            },
            "history": history,
            "scenarios": scenarios,
            "forecast_months": forecast_months,
            "average_ticket_cents": average_ticket,
        }

    def get_goal_projection(self, month: str, company: Any = None) -> dict[str, Any]:
        return self.goal_projection(month, self._resolve_company(company))

    def dashboard(self, month: str, company: Any = None) -> dict[str, Any]:
        resolved_month = self._validate_month(month)
        company_id = self._resolve_company(company)
        self.repository.refresh_overdue(date.today())
        result = self.repository.dashboard(resolved_month, company_id)
        totals = result["totals"]
        totals["balance_cents"] = int(totals["income_cents"]) - int(totals["expense_cents"])
        budget_limit = int(result["budget_limit_cents"])
        totals["budget_usage_percent"] = (
            round(int(totals["expense_cents"]) / budget_limit * 100, 1) if budget_limit else 0
        )
        return result

    def list_budgets(self, month: str, company: Any = None) -> list[dict[str, Any]]:
        company_id = self._resolve_company(company)
        return self.repository.list_budgets(
            self._validate_month(month), self._scope_key(company_id), company_id
        )

    def client_ranking(
        self, month: str, scope: str = "month", company: Any = None
    ) -> dict[str, Any]:
        if scope not in {"month", "all"}:
            raise ValidationError("Escopo de ranking inválido.")
        resolved_month = self._validate_month(month) if scope == "month" else None
        company_id = self._resolve_company(company)
        rows = self.repository.client_ranking(resolved_month, company_id)
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
            "company_id": company_id,
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
        company_id = self._resolve_company(payload.get("company"))
        category = self._clean_text(payload.get("category"), required=True, max_length=80)
        assert category
        limit_cents = self._money_to_cents(payload.get("limit"), "limit")
        return self.repository.upsert_budget(
            month, category, limit_cents, self._scope_key(company_id), company_id
        )

    def import_nfse(self, content: bytes, company: Any = None) -> dict[str, Any]:
        selected_company_id = self._resolve_company(company)
        company_rows = self.repository.list_companies()
        companies = {item["slug"]: item["id"] for item in company_rows}
        company_slugs = {item["id"]: item["slug"] for item in company_rows}
        rows = parse_nfse_workbook(content)
        company_counts: dict[str, int] = {item["slug"]: 0 for item in company_rows}
        for row in rows:
            company_id = selected_company_id
            if company_id is None:
                notes = str(row.get("notes") or "")
                if "São José dos Pinhais" in notes or "Sao Jose dos Pinhais" in notes:
                    company_id = companies["brc"]
                    company_counts["brc"] += 1
                elif "Curitiba" in notes:
                    company_id = companies["wbk"]
                    company_counts["wbk"] += 1
                else:
                    raise ValidationError(
                        "Não foi possível identificar a empresa de uma das notas. "
                        "Selecione BRC ou WBK antes de importar."
                    )
            else:
                slug = company_slugs.get(company_id, "")
                if slug in company_counts:
                    company_counts[slug] += 1
            row["company_id"] = company_id
            if row.get("external_key"):
                row["external_key"] = f"company:{company_id}:{row['external_key']}"
        inserted, ignored = self.repository.create_transactions_ignoring_duplicates(rows)
        self.repository.sync_clients_from_income()
        active_total = sum(row["amount_cents"] for row in rows if row["status"] == "paid")
        cancelled_total = sum(row["amount_cents"] for row in rows if row["status"] == "cancelled")
        return {
            "found": len(rows),
            "inserted": inserted,
            "ignored": ignored,
            "active_total_cents": active_total,
            "cancelled_total_cents": cancelled_total,
            "company_counts": company_counts,
        }

    @staticmethod
    def _public_document(document: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in document.items() if key != "storage_name"}

    def list_documents(self, company: Any = None) -> list[dict[str, Any]]:
        company_id = self._resolve_company(company)
        return [self._public_document(item) for item in self.repository.list_documents(company_id=company_id)]

    def delete_document(self, document_id: int) -> bool:
        document = self.repository.get_document(document_id)
        if document is None:
            return False
        if not self.repository.delete_document(document_id):
            return False
        path = (self.documents_path / document["storage_name"]).resolve()
        if self.documents_path.resolve() in path.parents:
            path.unlink(missing_ok=True)
        return True

    def analyze_document(
        self, file_name: str, mime_type: str, content: bytes, company: Any
    ) -> dict[str, Any]:
        company_id = self._resolve_company(company, required=True)
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
                    "company_id": company_id,
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
        enriched_payload["company_id"] = document.get("company_id")
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
        if values["kind"] == "income" and not values.get("client_id"):
            self.repository.sync_clients_from_income()
            transaction = self.repository.get_transaction(transaction["id"]) or transaction
        return {"transaction": transaction, "document": self._public_document(updated_document)}

    def company_comparison(self, month: str) -> dict[str, Any]:
        return self.repository.company_comparison(self._validate_month(month))

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
                "Empresa",
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
        company_names = {item["id"]: item["name"] for item in self.repository.list_companies()}
        for row in rows:
            writer.writerow(
                [
                    company_names.get(row.get("company_id"), "Não definida"),
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
