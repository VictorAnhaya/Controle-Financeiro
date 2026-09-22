from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


class DocumentReadError(ValueError):
    pass


@dataclass(slots=True)
class ParsedFiscalDocument:
    document_type: str
    issuer_name: str | None = None
    issuer_tax_id: str | None = None
    recipient_name: str | None = None
    recipient_tax_id: str | None = None
    document_number: str | None = None
    access_key: str | None = None
    issue_date: str | None = None
    total_cents: int | None = None
    confidence: float = 0
    field_confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    text_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_fiscal_document(file_name: str, mime_type: str, content: bytes) -> ParsedFiscalDocument:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".xml" or mime_type in {"application/xml", "text/xml"}:
        return _read_xml(content)
    if suffix == ".pdf" or mime_type == "application/pdf":
        return _read_pdf(content)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"} or mime_type.startswith("image/"):
        return _read_image(content)
    raise DocumentReadError("Formato não aceito. Envie XML, PDF, PNG, JPG, WEBP ou TIFF.")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_section(root: ElementTree.Element, names: Iterable[str]) -> ElementTree.Element | None:
    accepted = set(names)
    return next((element for element in root.iter() if _local_name(element.tag) in accepted), None)


def _text_in(section: ElementTree.Element | None, names: Iterable[str]) -> str | None:
    if section is None:
        return None
    accepted = set(names)
    for element in section.iter():
        if _local_name(element.tag) in accepted and element.text:
            value = re.sub(r"\s+", " ", element.text).strip()
            if value:
                return value
    return None


def _tax_id(section: ElementTree.Element | None) -> str | None:
    value = _text_in(section, {"CNPJ", "CpfCnpj", "CPF"})
    digits = re.sub(r"\D", "", value or "")
    return digits or None


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()[:19]
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(normalized[: len(datetime.now().strftime(pattern))], pattern).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    return "-".join(match.groups()) if match else None


def _money_cents(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d,.-]", "", value)
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    cents = int(amount * 100)
    return cents if cents > 0 else None


def _read_xml(content: bytes) -> ParsedFiscalDocument:
    upper_prefix = content[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise DocumentReadError("XML recusado por conter declaração externa não permitida.")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise DocumentReadError("O XML está inválido ou corrompido.") from exc

    nfe_info = _find_section(root, {"infNFe"})
    nfse_info = _find_section(root, {"InfNfse", "infNFSe", "InfDeclaracaoPrestacaoServico"})
    is_nfe = nfe_info is not None
    document_type = "NF-e" if is_nfe else "NFS-e"
    issuer = _find_section(root, {"emit"}) if is_nfe else _find_section(root, {"PrestadorServico", "prestador"})
    recipient = _find_section(root, {"dest"}) if is_nfe else _find_section(root, {"TomadorServico", "tomador"})
    ide = _find_section(root, {"ide"}) if is_nfe else nfse_info
    total_section = _find_section(root, {"ICMSTot"}) if is_nfe else _find_section(root, {"ValoresNfse", "Valores"})

    number = _text_in(ide, {"nNF"} if is_nfe else {"Numero", "NumeroNfse"})
    issue_date = _iso_date(_text_in(ide, {"dhEmi", "dEmi"} if is_nfe else {"DataEmissao", "Competencia"}))
    total = _money_cents(
        _text_in(total_section, {"vNF"} if is_nfe else {"ValorLiquidoNfse", "ValorServicos"})
    )
    issuer_name = _text_in(issuer, {"xNome"} if is_nfe else {"RazaoSocial", "NomeFantasia"})
    recipient_name = _text_in(recipient, {"xNome"} if is_nfe else {"RazaoSocial", "NomeFantasia"})

    access_key = None
    if nfe_info is not None:
        access_key = re.sub(r"\D", "", nfe_info.attrib.get("Id", "")) or None
    if not access_key:
        access_key = _text_in(root, {"chNFe", "CodigoVerificacao"})

    fields = {
        "document_number": 1.0 if number else 0,
        "issue_date": 1.0 if issue_date else 0,
        "total_cents": 1.0 if total else 0,
        "issuer_name": 1.0 if issuer_name else 0,
        "issuer_tax_id": 1.0 if _tax_id(issuer) else 0,
    }
    confidence = sum(fields.values()) / len(fields)
    warnings = [] if confidence == 1 else ["Alguns campos não estavam presentes no XML e precisam ser conferidos."]
    return ParsedFiscalDocument(
        document_type=document_type,
        issuer_name=issuer_name,
        issuer_tax_id=_tax_id(issuer),
        recipient_name=recipient_name,
        recipient_tax_id=_tax_id(recipient),
        document_number=number,
        access_key=access_key,
        issue_date=issue_date,
        total_cents=total,
        confidence=round(confidence, 2),
        field_confidence=fields,
        warnings=warnings,
    )


def _read_pdf(content: bytes) -> ParsedFiscalDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentReadError(
            "Para ler PDF, instale as dependências com: py -m pip install -r requirements.txt"
        ) from exc
    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
    except Exception as exc:
        raise DocumentReadError("Não foi possível ler o PDF enviado.") from exc
    if len(text.strip()) < 30:
        raise DocumentReadError(
            "Este PDF parece ser apenas uma imagem. Envie a nota como PNG/JPG para usar o OCR."
        )
    return _read_document_text(text, "PDF")


def _read_image(content: bytes) -> ParsedFiscalDocument:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise DocumentReadError(
            "Para ler imagens, instale Pillow e pytesseract pelo arquivo requirements.txt."
        ) from exc
    try:
        image = Image.open(BytesIO(content))
        image.verify()
        image = Image.open(BytesIO(content))
        text = pytesseract.image_to_string(image, lang="por")
    except pytesseract.TesseractNotFoundError as exc:
        raise DocumentReadError(
            "O mecanismo Tesseract OCR não está instalado no Windows. Consulte o README do projeto."
        ) from exc
    except Exception as exc:
        raise DocumentReadError("Não foi possível processar a imagem enviada.") from exc
    if len(text.strip()) < 20:
        raise DocumentReadError("A imagem não possui texto legível o suficiente para extração.")
    return _read_document_text(text, "OCR")


def _match_first(patterns: Iterable[str], text: str, flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" :-")
    return None


def _read_document_text(raw_text: str, source: str) -> ParsedFiscalDocument:
    text = raw_text.replace("\r", "\n")
    compact = re.sub(r"[ \t]+", " ", text)
    upper = compact.upper()
    document_type = "NFS-e" if "NFS-E" in upper or "NOTA FISCAL DE SERVI" in upper else "NF-e"

    tax_ids = re.findall(r"\b\d{2}[. ]?\d{3}[. ]?\d{3}[/ ]?\d{4}[- ]?\d{2}\b", compact)
    issuer_name = _match_first(
        [
            r"(?:RAZ[ÃA]O SOCIAL|PRESTADOR(?: DE SERVI[CÇ]OS)?|EMITENTE)\s*[:\-]?\s*\n\s*([^\n]{3,120})",
            r"NOME\s*/?\s*RAZ[ÃA]O SOCIAL\s*[:\-]?\s*([^\n]{3,120})",
        ],
        compact,
    )
    recipient_name = _match_first(
        [
            r"(?:TOMADOR(?: DE SERVI[CÇ]OS)?|DESTINAT[ÁA]RIO|CLIENTE)\s*[:\-]?\s*\n\s*([^\n]{3,120})",
            r"DADOS DO (?:TOMADOR|DESTINAT[ÁA]RIO)\s*[:\-]?\s*\n\s*([^\n]{3,120})",
        ],
        compact,
    )
    number = _match_first(
        [
            r"(?:N[ÚU]MERO DA (?:NFS-E|NOTA)|N[ÚU]MERO|N[º°])\s*[:\-]?\s*(\d{1,20})",
            r"(?:NF-E|NFS-E)\s*(?:N[º°])?\s*[:\-]?\s*(\d{1,20})",
        ],
        compact,
    )
    date_value = _match_first(
        [r"(?:DATA (?:DE )?EMISS[ÃA]O|EMISS[ÃA]O)\s*[:\-]?\s*(\d{2}[/-]\d{2}[/-]\d{4})", r"\b(\d{2}/\d{2}/\d{4})\b"],
        compact,
    )
    total_value = _match_first(
        [
            r"(?:VALOR TOTAL (?:DA NOTA|DA NFS-E|DA NF-E|DOS SERVI[CÇ]OS)|TOTAL DA (?:NOTA|NFS-E|NF-E)|VALOR L[IÍ]QUIDO)\s*[:\-]?\s*R?\$?\s*([\d.]+,\d{2})",
            r"(?:VALOR DOS SERVI[CÇ]OS)\s*[:\-]?\s*R?\$?\s*([\d.]+,\d{2})",
        ],
        compact,
    )
    access_key_match = re.search(r"\b(?:\d[ .-]?){44}\b", compact)
    access_key = re.sub(r"\D", "", access_key_match.group(0)) if access_key_match else None

    field_confidence = {
        "document_number": 0.82 if number else 0,
        "issue_date": 0.88 if date_value else 0,
        "total_cents": 0.9 if total_value else 0,
        "issuer_name": 0.72 if issuer_name else 0,
        "issuer_tax_id": 0.85 if tax_ids else 0,
    }
    present = [score for score in field_confidence.values() if score]
    confidence = sum(present) / len(field_confidence)
    warnings = [f"Leitura realizada por {source}; confirme os campos antes de criar o lançamento."]
    if not total_value:
        warnings.append("O valor total não foi identificado automaticamente.")
    return ParsedFiscalDocument(
        document_type=document_type,
        issuer_name=issuer_name,
        issuer_tax_id=re.sub(r"\D", "", tax_ids[0]) if tax_ids else None,
        recipient_name=recipient_name,
        recipient_tax_id=re.sub(r"\D", "", tax_ids[1]) if len(tax_ids) > 1 else None,
        document_number=number,
        access_key=access_key,
        issue_date=_iso_date(date_value),
        total_cents=_money_cents(total_value),
        confidence=round(confidence, 2),
        field_confidence=field_confidence,
        warnings=warnings,
        text_preview=re.sub(r"\s+", " ", compact).strip()[:3000],
    )
