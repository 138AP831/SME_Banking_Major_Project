"""Document AI & KYC pipeline (pure functions, no DB access).

Pipeline stages (each is a separate function so the LangGraph KYC workflow can run them as nodes):

    validate_file -> classify_document -> extract_fields -> validate_formats
                  -> cross_document_checks -> compute_application_status

* Text PDFs are read with pypdf; images use Tesseract OCR *if installed*, otherwise a clear
  error is returned so the reviewer can type the fields in manually.
* Extraction is rule-based (regex on labelled lines) - the ``extract`` interface is pluggable.
* Aadhaar numbers are checksum-validated in memory and only the last 4 digits are ever stored.
* External verification (sanctions / PEP / registry / identity) is NEVER performed here. The
  simulated provider returns clearly labelled placeholder results.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ----------------------------------------------------------------------------- constants
DOC_TYPES = ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT", "ITR", "UTILITY_BILL"]
DOC_LABELS = {
    "PAN": "PAN card",
    "AADHAAR": "Aadhaar (authorised signatory)",
    "GST": "GST registration certificate",
    "INCORPORATION": "Incorporation / registration certificate",
    "BANK_STATEMENT": "Bank statement",
    "ITR": "Income tax return acknowledgement",
    "UTILITY_BILL": "Utility bill (address proof)",
}
REQUIRED_DOCS = ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT", "UTILITY_BILL"]
MAX_FILE_BYTES = 8 * 1024 * 1024
ALLOWED_MIME = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
}
LOW_CONFIDENCE_THRESHOLD = 0.75

SENSITIVE_FIELDS = {"pan", "gstin", "aadhaar_last4", "account_number", "cin", "registration_number"}

# ----------------------------------------------------------------------------- checksums
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    if not num.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum_char(body14: str) -> str:
    total = 0
    for i, ch in enumerate(body14):
        v = _GST_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _GST_CHARS[(36 - total % 36) % 36]


PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-[0-9]{4}$")
UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PINCODE_RE = re.compile(r"\b[1-9][0-9]{5}\b")
PAN_ENTITY_CHARS = set("CFPHATBLJG")


def valid_pan(v: str) -> bool:
    return bool(PAN_RE.match(v)) and v[3] in PAN_ENTITY_CHARS


def valid_gstin(v: str) -> bool:
    return bool(GSTIN_RE.match(v)) and gstin_checksum_char(v[:14]) == v[14] and valid_pan(v[2:12])


def valid_registration(v: str) -> bool:
    return bool(CIN_RE.match(v) or LLPIN_RE.match(v) or UDYAM_RE.match(v))


# ----------------------------------------------------------------------------- file validation
@dataclass
class FileCheck:
    ok: bool
    kind: str | None = None  # pdf | image
    error: str | None = None
    sha256: str | None = None
    size: int = 0


def validate_file(filename: str, content: bytes, mime: str | None) -> FileCheck:
    size = len(content)
    if size == 0:
        return FileCheck(False, error="The file is empty.", size=size)
    if size > MAX_FILE_BYTES:
        return FileCheck(False, error=f"File is {size / 1e6:.1f} MB; the limit is {MAX_FILE_BYTES // (1024 * 1024)} MB.", size=size)
    mime_l = (mime or "").lower()
    kind = ALLOWED_MIME.get(mime_l)
    # trust magic bytes over the declared type
    if content[:5] == b"%PDF-":
        kind = "pdf"
    elif content[:8] == b"\x89PNG\r\n\x1a\n" or content[:3] == b"\xff\xd8\xff":
        kind = "image"
    if kind is None:
        return FileCheck(False, error="Unsupported file type. Upload a PDF, PNG or JPEG.", size=size)
    if mime_l in ("application/pdf",) and kind != "pdf":
        return FileCheck(False, error="File content does not match the declared PDF type.", size=size)
    return FileCheck(True, kind=kind, sha256=hashlib.sha256(content).hexdigest(), size=size)


# ----------------------------------------------------------------------------- text acquisition
def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class TextResult:
    text: str
    method: str
    ocr_confidence: float | None = None
    error: str | None = None


def read_text(content: bytes, kind: str) -> TextResult:
    if kind == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:  # noqa: BLE001
            return TextResult("", "pdf_text", error=f"PDF could not be read: {type(e).__name__}.")
        if len(text.strip()) < 20:
            return TextResult(
                "", "pdf_text",
                error="No text layer found in this PDF (likely a scan). Upload a PNG/JPEG for OCR or enter the fields manually.",
            )
        return TextResult(text, "pdf_text")
    # image -> OCR
    if not ocr_available():
        return TextResult(
            "", "ocr_unavailable",
            error="OCR engine (Tesseract) is not installed on this server. Enter the fields manually or upload a text PDF.",
        )
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(content)).convert("L")
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        confs = [float(c) for c, t in zip(data["conf"], data["text"]) if t.strip() and float(c) >= 0]
        text = pytesseract.image_to_string(img)
        avg = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        if len(text.strip()) < 10:
            return TextResult("", "ocr_tesseract", avg, error="OCR found no readable text in the image.")
        return TextResult(text, "ocr_tesseract", avg)
    except Exception as e:  # noqa: BLE001
        return TextResult("", "ocr_tesseract", error=f"OCR failed: {type(e).__name__}.")


# ----------------------------------------------------------------------------- classification
_CLASS_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "PAN": [("permanent account number", 3), ("income tax department", 1.5), ("pan:", 2), ("pan card", 2)],
    "AADHAAR": [("aadhaar", 3), ("unique identification", 2), ("uidai", 2), ("enrolment", 1)],
    "GST": [("goods and services tax", 2.5), ("gstin", 3), ("registration certificate", 1), ("trade name", 1)],
    "INCORPORATION": [("certificate of incorporation", 3), ("corporate identity number", 2.5), ("registrar of companies", 2), ("limited liability partnership", 2), ("udyam", 2.5)],
    "BANK_STATEMENT": [("account statement", 3), ("ifsc", 2), ("opening balance", 1.5), ("closing balance", 1.5), ("statement period", 1.5)],
    "ITR": [("income tax return", 3), ("acknowledgement number", 2.5), ("assessment year", 2), ("itr-", 1.5)],
    "UTILITY_BILL": [("consumer no", 2.5), ("electricity", 2), ("bill date", 1.5), ("units consumed", 1.5), ("utility bill", 2.5), ("amount payable", 1)],
}
_FILENAME_HINTS = {
    "pan": "PAN", "aadhaar": "AADHAAR", "aadhar": "AADHAAR", "gst": "GST", "incorp": "INCORPORATION", "cin": "INCORPORATION",
    "udyam": "INCORPORATION", "bank": "BANK_STATEMENT", "statement": "BANK_STATEMENT", "itr": "ITR", "utility": "UTILITY_BILL",
    "electricity": "UTILITY_BILL", "bill": "UTILITY_BILL",
}


def classify_document(filename: str, text: str, declared: str | None = None) -> dict:
    low = text.lower()
    scores = {t: sum(w for kw, w in kws if kw in low) for t, kws in _CLASS_KEYWORDS.items()}
    fn = filename.lower()
    for hint, t in _FILENAME_HINTS.items():
        if hint in fn:
            scores[t] += 0.75
    total = sum(scores.values())
    best = max(scores, key=lambda k: scores[k]) if total > 0 else None
    conf = (scores[best] / total) if best and total else 0.0
    # absolute evidence also matters (a single weak keyword is not confident)
    conf = min(conf, 0.5 + scores[best] / 10) if best else 0.0
    result = {"predicted_type": best, "confidence": round(conf, 3), "scores": {k: round(v, 2) for k, v in scores.items() if v}}
    if declared and declared in DOC_TYPES:
        result["declared_type"] = declared
        result["type_mismatch"] = bool(best and best != declared and conf >= 0.5)
        result["final_type"] = declared if not result["type_mismatch"] else declared
    else:
        result["final_type"] = best
    return result


# ----------------------------------------------------------------------------- extraction
def _find(text: str, *labels: str) -> str | None:
    for label in labels:
        m = re.search(rf"(?im)^\s*{label}\s*[:\-]\s*(.+?)\s*$", text)
        if m:
            return m.group(1).strip()
    return None


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    v = v.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


_FIELD_SPECS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "PAN": [("pan", ("PAN", "Permanent Account Number")), ("name", ("Name",)), ("date_of_incorporation", ("Date of Incorporation", "Date of Incorporation/Formation"))],
    "AADHAAR": [("name", ("Name",)), ("dob", ("DOB", "Date of Birth")), ("aadhaar_number", ("Aadhaar Number", "Aadhaar No")), ("address", ("Address",))],
    "GST": [("gstin", ("GSTIN",)), ("legal_name", ("Legal Name", "Legal Name of Business")), ("trade_name", ("Trade Name",)),
            ("address", ("Principal Place of Business", "Address")), ("registration_date", ("Date of Registration",))],
    "INCORPORATION": [("registration_number", ("Corporate Identity Number", "CIN", "LLPIN", "Registration Number", "Udyam Registration Number")),
                      ("company_name", ("Company Name", "Name of Company", "Name of Entity", "Name")), ("date_of_incorporation", ("Date of Incorporation", "Date of Registration")),
                      ("registered_office", ("Registered Office", "Address")), ("directors", ("Directors", "Partners", "Proprietor"))],
    "BANK_STATEMENT": [("account_holder", ("Account Holder", "Account Name")), ("account_number", ("Account Number", "Account No")), ("ifsc", ("IFSC", "IFSC Code")),
                       ("statement_period", ("Statement Period",)), ("closing_balance", ("Closing Balance",))],
    "ITR": [("pan", ("PAN",)), ("name", ("Name", "Assessee Name")), ("assessment_year", ("Assessment Year",)), ("gross_total_income", ("Gross Total Income",)),
            ("acknowledgement_number", ("Acknowledgement Number",))],
    "UTILITY_BILL": [("consumer_name", ("Consumer Name", "Customer Name")), ("address", ("Service Address", "Address")), ("bill_date", ("Bill Date",)),
                     ("amount_payable", ("Amount Payable",)), ("consumer_number", ("Consumer No", "Consumer Number"))],
}


def extract_fields(doc_type: str, text: str, ocr_confidence: float | None = None) -> dict[str, dict]:
    """Return ``{field: {value, confidence, found}}``. Sensitive values are returned raw to the
    caller (validation needs them) - the persistence layer is responsible for masking."""
    out: dict[str, dict] = {}
    base = 0.97 if ocr_confidence is None else max(0.2, min(0.99, ocr_confidence))
    for fname, labels in _FIELD_SPECS.get(doc_type, []):
        v = _find(text, *labels)
        if v is not None:
            v = re.sub(r"\s{2,}", " ", v)
            if fname in ("pan", "gstin", "ifsc"):
                v = v.replace(" ", "").upper()
            if fname == "aadhaar_number":
                v = re.sub(r"[^0-9]", "", v)
        out[fname] = {"value": v, "confidence": round(base if v else 0.0, 3), "found": v is not None}
    return out


def validate_formats(doc_type: str, fields: dict[str, dict], as_of: date) -> list[dict]:
    """Format validations. Mutates field confidences when a validation fails."""
    checks: list[dict] = []

    def add(code: str, field_name: str, ok: bool, msg: str, severity: str = "review") -> None:
        checks.append({"code": code, "field": field_name, "passed": ok, "message": msg, "severity": "info" if ok else severity})
        if not ok and field_name in fields:
            fields[field_name]["valid"] = False
            fields[field_name]["confidence"] = round(min(fields[field_name]["confidence"], 0.45), 3)
        elif ok and field_name in fields:
            fields[field_name]["valid"] = True

    for fname, spec in fields.items():
        if not spec["found"]:
            add("FIELD_MISSING", fname, False, f"Required field '{fname}' was not found on the document.")
            fields[fname]["confidence"] = 0.0
    g = lambda k: (fields.get(k) or {}).get("value")  # noqa: E731

    if doc_type == "PAN" and g("pan"):
        add("PAN_FORMAT", "pan", valid_pan(g("pan")), "PAN format AAAAA9999A with a valid holder-type character." if valid_pan(g("pan")) else "PAN does not match the AAAAA9999A format / holder type.")
    if doc_type == "ITR" and g("pan"):
        add("PAN_FORMAT", "pan", valid_pan(g("pan")), "PAN format valid." if valid_pan(g("pan")) else "PAN format invalid.")
    if doc_type == "GST" and g("gstin"):
        ok = valid_gstin(g("gstin"))
        add("GSTIN_FORMAT", "gstin", ok, "GSTIN structure and checksum are valid." if ok else "GSTIN structure or checksum is invalid.", "blocking")
    if doc_type == "AADHAAR" and g("aadhaar_number"):
        n = g("aadhaar_number")
        ok = len(n) == 12 and verhoeff_valid(n) and n[0] not in "01"
        add("AADHAAR_CHECKSUM", "aadhaar_number", ok, "Aadhaar number length and Verhoeff checksum are valid." if ok else "Aadhaar number failed the length/Verhoeff checksum validation.", "blocking")
    if doc_type == "INCORPORATION" and g("registration_number"):
        ok = valid_registration(g("registration_number").replace(" ", "").upper())
        add("REGISTRATION_FORMAT", "registration_number", ok, "Registration number format is valid." if ok else "Registration number (CIN / LLPIN / Udyam) format is invalid.")
    if doc_type == "BANK_STATEMENT" and g("ifsc"):
        ok = bool(IFSC_RE.match(g("ifsc")))
        add("IFSC_FORMAT", "ifsc", ok, "IFSC format valid." if ok else "IFSC format invalid.")
    for dname in ("date_of_incorporation", "registration_date", "bill_date", "dob"):
        if g(dname):
            d = _parse_date(g(dname))
            ok = d is not None and d <= as_of
            add("DATE_FORMAT", dname, ok, f"{dname} parsed as {d}." if ok else f"{dname} is not a valid past date.")
    if doc_type == "UTILITY_BILL" and g("bill_date"):
        d = _parse_date(g("bill_date"))
        if d:
            age = (as_of - d).days
            add("BILL_RECENCY", "bill_date", age <= 90, f"Bill is {age} days old (limit 90)." if age <= 90 else f"Bill is {age} days old; address proofs must be at most 90 days old.")
    return checks


def document_confidence(fields: dict[str, dict], checks: list[dict]) -> float:
    if not fields:
        return 0.0
    vals = [f["confidence"] for f in fields.values()]
    conf = sum(vals) / len(vals)
    return round(conf, 3)


def to_storage_fields(doc_type: str, fields: dict[str, dict]) -> dict[str, dict]:
    """Storage representation: Aadhaar number is reduced to its last 4 digits (never stored in full)."""
    out: dict[str, dict] = {}
    for k, v in fields.items():
        v = dict(v)
        if k == "aadhaar_number":
            # only the last 4 digits are ever persisted
            raw = v.get("value") or ""
            v["value"] = raw[-4:] if raw else None
            v["sensitive"] = True
            k = "aadhaar_last4"
        elif k in SENSITIVE_FIELDS:
            v["sensitive"] = True
        out[k] = v
    return out


def mask_fields_for_role(fields: dict[str, dict], reveal: bool) -> dict[str, dict]:
    from app.core.util import mask_account, mask_aadhaar_last4, mask_gstin, mask_pan

    out: dict[str, dict] = {}
    for k, v in fields.items():
        v = dict(v)
        val = v.get("value")
        if val and (v.get("sensitive") or k in SENSITIVE_FIELDS):
            if k == "aadhaar_last4":
                v["value"] = mask_aadhaar_last4(val)  # never revealable
                v["masked"] = True
            elif not reveal:
                if k == "pan":
                    v["value"] = mask_pan(val)
                elif k == "gstin":
                    v["value"] = mask_gstin(val)
                elif k == "account_number":
                    v["value"] = mask_account(val)
                else:
                    v["value"] = val[:3] + "•" * max(0, len(val) - 5) + val[-2:]
                v["masked"] = True
        out[k] = v
    return out


# ----------------------------------------------------------------------------- cross-document checks
_ENTITY_SUFFIXES = [
    (r"\bprivate limited\b", "pvt ltd"), (r"\bpvt\.? ltd\.?\b", "pvt ltd"), (r"\blimited liability partnership\b", "llp"),
    (r"\band\b", "&"),
]


def normalize_name(n: str | None) -> str:
    n = (n or "").lower()
    n = re.sub(r"[^\w&\s]", " ", n)
    for pat, rep in _ENTITY_SUFFIXES:
        n = re.sub(pat, rep, n)
    return re.sub(r"\s+", " ", n).strip()


def name_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return round(max(seq, jac), 3)


def _doc_field(docs: dict[str, dict], dtype: str, field_name: str) -> str | None:
    d = docs.get(dtype)
    if not d:
        return None
    return ((d.get("fields") or {}).get(field_name) or {}).get("value")


def cross_document_checks(docs: dict[str, dict], sme: dict, as_of: date, open_credit_amount: float = 0.0) -> list[dict]:
    """``docs``: {doc_type: {"fields": storage-fields, "status": ...}} ; ``sme``: SME master record."""
    checks: list[dict] = []

    def add(code: str, passed: bool, message: str, severity: str = "review", docs_involved: list[str] | None = None, evidence: dict | None = None):
        checks.append({"code": code, "passed": passed, "severity": "info" if passed else severity, "message": message,
                       "documents": docs_involved or [], "evidence": evidence or {}})

    # 1. required documents
    required = list(REQUIRED_DOCS) + (["ITR"] if open_credit_amount >= 5_000_000 else [])
    missing = [t for t in required if t not in docs]
    add("REQUIRED_DOCUMENTS", not missing,
        "All required documents are on file." if not missing else "Missing required documents: " + ", ".join(DOC_LABELS[m] for m in missing) + ".",
        "blocking", missing, {"missing": missing, "required": required})

    # 2. name consistency
    names = {
        "PAN": _doc_field(docs, "PAN", "name"), "GST": _doc_field(docs, "GST", "legal_name"),
        "INCORPORATION": _doc_field(docs, "INCORPORATION", "company_name"), "BANK_STATEMENT": _doc_field(docs, "BANK_STATEMENT", "account_holder"),
        "ITR": _doc_field(docs, "ITR", "name"), "UTILITY_BILL": _doc_field(docs, "UTILITY_BILL", "consumer_name"),
    }
    present = {k: v for k, v in names.items() if v}
    if len(present) >= 2:
        ref_key = "INCORPORATION" if "INCORPORATION" in present else ("PAN" if "PAN" in present else next(iter(present)))
        sims = {k: name_similarity(present[ref_key], v) for k, v in present.items() if k != ref_key}
        worst = min(sims.values()) if sims else 1.0
        bad = {k: s for k, s in sims.items() if s < 0.85}
        add("ENTITY_NAME_MATCH", not bad,
            "Business name is consistent across documents." if not bad else
            "Business name differs from the " + DOC_LABELS[ref_key].lower() + " in: " + ", ".join(f"{DOC_LABELS[k]} ({s:.0%} match)" for k, s in bad.items()) + ".",
            "review", [ref_key, *bad.keys()], {"reference": ref_key, "similarities": sims, "values": present, "worst": worst})

    # 3. PAN <-> GSTIN
    pan = _doc_field(docs, "PAN", "pan")
    gstin = _doc_field(docs, "GST", "gstin")
    if pan and gstin:
        ok = gstin[2:12] == pan
        add("PAN_GSTIN_LINK", ok, "PAN embedded in the GSTIN matches the PAN card." if ok else "PAN embedded in the GSTIN does not match the PAN card number.",
            "blocking", ["PAN", "GST"], {"pan_on_card": "match" if ok else "mismatch"})
    itr_pan = _doc_field(docs, "ITR", "pan")
    if pan and itr_pan:
        ok = pan == itr_pan
        add("PAN_ITR_MATCH", ok, "PAN on the ITR acknowledgement matches the PAN card." if ok else "PAN on the ITR acknowledgement differs from the PAN card.", "blocking", ["PAN", "ITR"])

    # 4. master-record consistency
    if pan and sme.get("pan"):
        ok = pan == sme["pan"]
        add("PAN_MASTER_MATCH", ok, "PAN matches the application master record." if ok else "PAN on the document does not match the PAN on the application.", "blocking", ["PAN"])

    # 5. signatory
    aad_name = _doc_field(docs, "AADHAAR", "name")
    directors = _doc_field(docs, "INCORPORATION", "directors") or sme.get("director_name")
    if aad_name and directors:
        cands = [d.strip() for d in re.split(r"[;,&]| and ", directors) if d.strip()]
        best = max((name_similarity(aad_name, c) for c in cands), default=0.0)
        add("SIGNATORY_MATCH", best >= 0.9, "Authorised signatory name matches a listed director/partner." if best >= 0.9
            else f"Aadhaar name does not match any listed director/partner (best match {best:.0%}).", "review", ["AADHAAR", "INCORPORATION"], {"best_similarity": best})

    # 6. address
    gst_addr = _doc_field(docs, "GST", "address")
    util_addr = _doc_field(docs, "UTILITY_BILL", "address")
    if gst_addr and util_addr:
        a, b = set(normalize_name(gst_addr).split()), set(normalize_name(util_addr).split())
        j = len(a & b) / len(a | b) if a | b else 0
        pin_a, pin_b = PINCODE_RE.findall(gst_addr), PINCODE_RE.findall(util_addr)
        pin_ok = (not pin_a or not pin_b) or pin_a[-1] == pin_b[-1]
        ok = j >= 0.55 and pin_ok
        add("ADDRESS_MATCH", ok, "GST business address agrees with the utility-bill address." if ok else "GST business address does not agree with the utility-bill address.",
            "review", ["GST", "UTILITY_BILL"], {"token_overlap": round(j, 2), "pincode_match": pin_ok})

    # 7. date logic
    inc = _parse_date(_doc_field(docs, "INCORPORATION", "date_of_incorporation"))
    reg = _parse_date(_doc_field(docs, "GST", "registration_date"))
    if inc and reg:
        ok = reg >= inc
        add("DATE_SEQUENCE", ok, "GST registration is dated on/after incorporation." if ok else "GST registration predates the incorporation date.", "review", ["INCORPORATION", "GST"])
    pan_inc = _parse_date(_doc_field(docs, "PAN", "date_of_incorporation"))
    if inc and pan_inc:
        ok = inc == pan_inc
        add("INCORPORATION_DATE_MATCH", ok, "Incorporation date matches between PAN and registration certificate." if ok else "Incorporation date differs between PAN and registration certificate.", "review", ["PAN", "INCORPORATION"])
    return checks


# ----------------------------------------------------------------------------- application status
def build_exceptions(doc_results: list[dict], checks: list[dict]) -> list[dict]:
    exceptions: list[dict] = []
    for d in doc_results:
        if d["status"] == "failed":
            exceptions.append({"code": "EXTRACTION_FAILED", "severity": "blocking", "document": d["doc_type"], "message": d.get("error") or "Extraction failed.",
                               "resolution_hint": "Correct the fields manually or re-upload a clearer file."})
        elif d["confidence"] < LOW_CONFIDENCE_THRESHOLD:
            exceptions.append({"code": "LOW_CONFIDENCE", "severity": "review", "document": d["doc_type"],
                               "message": f"{DOC_LABELS.get(d['doc_type'], d['doc_type'])}: extraction confidence {d['confidence']:.0%} is below the {LOW_CONFIDENCE_THRESHOLD:.0%} threshold.",
                               "resolution_hint": "Review the extracted values against the document and correct them."})
        for c in d.get("validation", []):
            if not c["passed"] and c["code"] != "FIELD_MISSING":
                exceptions.append({"code": c["code"], "severity": c["severity"], "document": d["doc_type"], "message": f"{DOC_LABELS.get(d['doc_type'], d['doc_type'])}: {c['message']}",
                                   "resolution_hint": "Verify against the original document."})
    for c in checks:
        if not c["passed"]:
            exceptions.append({"code": c["code"], "severity": c["severity"], "document": ", ".join(c["documents"]) or None, "message": c["message"],
                               "resolution_hint": "Request a corrected document from the customer." if c["code"] == "REQUIRED_DOCUMENTS" else "Compare the documents and resolve the discrepancy."})
    # de-duplicate by (code, document)
    seen, uniq = set(), []
    for e in exceptions:
        key = (e["code"], e.get("document"), e["message"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


def compute_application_status(doc_results: list[dict], checks: list[dict]) -> dict:
    exceptions = build_exceptions(doc_results, checks)
    missing = next((c["evidence"].get("missing", []) for c in checks if c["code"] == "REQUIRED_DOCUMENTS" and not c["passed"]), [])
    non_missing_exc = [e for e in exceptions if e["code"] != "REQUIRED_DOCUMENTS"]
    if not doc_results:
        status = "in_progress"
        reason = "No documents have been uploaded yet."
    elif non_missing_exc:
        status = "exception"
        reason = f"{len(non_missing_exc)} exception(s) require human review before this application can proceed."
    elif missing:
        status = "in_progress"
        reason = "Documents are incomplete: " + ", ".join(DOC_LABELS[m] for m in missing) + "."
    else:
        status = "eligible_for_approval"
        reason = "All checks passed. A KYC analyst must still confirm approval (no automatic approval)."
    return {"status": status, "reason": reason, "exceptions": exceptions, "missing_documents": missing,
            "requires_human_review": status in ("exception",) or bool(missing and non_missing_exc)}


# ----------------------------------------------------------------------------- simulated verification
def simulated_external_verification(sme_name: str) -> dict:
    """NO external service is connected. These are placeholders so the UI/workflow shape is
    visible; ``performed`` is always False and every row is labelled SIMULATED."""
    label = "SIMULATED - no external verification service is connected; nothing was actually checked."
    return {
        "provider": "none_connected",
        "performed": False,
        "simulated": True,
        "notice": label,
        "checks": [
            {"name": "Sanctions screening", "status": "not_performed", "simulated_result": "no_match", "label": label},
            {"name": "PEP screening", "status": "not_performed", "simulated_result": "no_match", "label": label},
            {"name": "Company registry lookup (MCA/GSTN)", "status": "not_performed", "simulated_result": "record_found", "label": label},
            {"name": "Identity verification (UIDAI/PAN-NSDL)", "status": "not_performed", "simulated_result": "verified", "label": label},
        ],
    }


# ----------------------------------------------------------------------------- one-shot pipeline
@dataclass
class ProcessedDocument:
    doc_type: str | None
    status: str
    method: str
    fields: dict = field(default_factory=dict)  # storage representation
    raw_fields: dict = field(default_factory=dict)  # transient, NOT persisted
    confidence: float = 0.0
    validation: list = field(default_factory=list)
    classification: dict = field(default_factory=dict)
    error: str | None = None
    sha256: str | None = None


def process_document(filename: str, content: bytes, mime: str | None, as_of: date, declared_type: str | None = None) -> ProcessedDocument:
    fc = validate_file(filename, content, mime)
    if not fc.ok:
        return ProcessedDocument(declared_type, "failed", "none", error=fc.error)
    tr = read_text(content, fc.kind or "pdf")
    if tr.error:
        return ProcessedDocument(declared_type, "failed", tr.method, error=tr.error, sha256=fc.sha256)
    cls = classify_document(filename, tr.text, declared_type)
    dtype = cls.get("final_type")
    if not dtype:
        return ProcessedDocument(None, "failed", tr.method, classification=cls, error="Could not determine the document type. Select the type manually.", sha256=fc.sha256)
    raw = extract_fields(dtype, tr.text, tr.ocr_confidence)
    checks = validate_formats(dtype, raw, as_of)
    conf = document_confidence(raw, checks)
    if cls.get("type_mismatch"):
        checks.append({"code": "TYPE_MISMATCH", "field": "document_type", "passed": False, "severity": "review",
                       "message": f"Declared as {DOC_LABELS[declared_type]} but content looks like {DOC_LABELS[cls['predicted_type']]}."})
        conf = min(conf, 0.5)
    stored = to_storage_fields(dtype, raw)
    status = "low_confidence" if conf < LOW_CONFIDENCE_THRESHOLD else "extracted"
    return ProcessedDocument(dtype, status, tr.method, stored, raw, conf, checks, cls, None, fc.sha256)
