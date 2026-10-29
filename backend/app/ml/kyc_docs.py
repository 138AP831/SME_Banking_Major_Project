"""Render SYNTHETIC sample KYC documents (text PDFs, plus a PNG for the OCR path).

Every page carries a "synthetic sample" banner. No real person, company or identifier is used.
``flaws`` lets the demo seed create deliberately inconsistent document sets.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

from app.db.synthetic import CITY, Identity
from app.ml.kyc import verhoeff_check_digit

BANNER = "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT - FOR SOFTWARE DEMONSTRATION ONLY"


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def document_lines(doc_type: str, r: dict, ident: Identity, as_of: date, flaws: dict[str, Any] | None = None) -> list[str]:
    flaws = flaws or {}
    name_upper = r["name"].upper()
    state, code, _, _ = CITY[r["city"]]
    inc = ident.incorporation_date
    pan, gstin = ident.pan, ident.gstin
    if doc_type == "PAN":
        if flaws.get("pan_name"):
            name_upper = flaws["pan_name"].upper()
        return ["INCOME TAX DEPARTMENT", "PERMANENT ACCOUNT NUMBER CARD", f"Name: {name_upper}", f"PAN: {pan}", f"Date of Incorporation: {_fmt(inc)}"]
    if doc_type == "AADHAAR":
        num = ident.aadhaar
        if flaws.get("aadhaar_bad_checksum"):
            last = str((int(num[-1]) + 3) % 10)
            num = num[:-1] + last  # breaks the Verhoeff checksum
        name = flaws.get("aadhaar_name") or ident.director_name
        n = f"{num[:4]} {num[4:8]} {num[8:]}"
        return ["Unique Identification Authority of India", "AADHAAR", f"Name: {name.upper()}", "DOB: 14/03/1978", f"Aadhaar Number: {n}", f"Address: {ident.address}"]
    if doc_type == "GST":
        if flaws.get("gstin_pan_mismatch"):
            wrong_pan = pan[:-1] + ("Q" if pan[-1] != "Q" else "R")
            body = f"{code}{wrong_pan}1Z"
            from app.ml.kyc import gstin_checksum_char

            gstin = body + gstin_checksum_char(body)
        legal = flaws.get("gst_legal_name") or r["name"]
        return ["GOODS AND SERVICES TAX", "REGISTRATION CERTIFICATE (FORM GST REG-06)", f"GSTIN: {gstin}", f"Legal Name: {legal.upper()}", f"Trade Name: {r['name'].upper()}",
                f"Principal Place of Business: {ident.address}", f"Date of Registration: {_fmt(ident.gst_registration_date)}"]
    if doc_type == "INCORPORATION":
        title = {"Private Limited": "CERTIFICATE OF INCORPORATION", "LLP": "CERTIFICATE OF INCORPORATION - LIMITED LIABILITY PARTNERSHIP"}.get(r["form"], "UDYAM REGISTRATION CERTIFICATE")
        label = {"Private Limited": "Corporate Identity Number", "LLP": "LLPIN"}.get(r["form"], "Udyam Registration Number")
        dirs = ident.director_name + (f"; {ident.other_director}" if ident.other_director else "")
        lines = [title, "Registrar of Companies" if r["form"] != "Proprietorship" and r["form"] != "Partnership Firm" else "Ministry of MSME", f"{label}: {ident.registration_number}",
                 f"Company Name: {r['name'].upper()}", f"Date of Incorporation: {_fmt(inc)}", f"Registered Office: {ident.address}", f"Directors: {dirs}"]
        return lines
    if doc_type == "BANK_STATEMENT":
        holder = flaws.get("bank_holder") or r["name"]
        return ["ACCOUNT STATEMENT", f"Account Holder: {holder.upper()}", f"Account Number: {ident.account_number}", f"IFSC: {ident.ifsc}",
                f"Statement Period: {_fmt(as_of - timedelta(days=180))} to {_fmt(as_of)}", "Opening Balance: INR 8,42,310.00", "Closing Balance: INR 12,05,664.00"]
    if doc_type == "ITR":
        return ["INCOME TAX RETURN ACKNOWLEDGEMENT", f"PAN: {pan}", f"Name: {r['name'].upper()}", "Assessment Year: 2025-26", f"Gross Total Income: INR {int(r['rev'] * 12 * 0.09):,}",
                "Acknowledgement Number: 7312" + ident.sme_id[-4:] + "0925"]
    if doc_type == "UTILITY_BILL":
        age = flaws.get("bill_age_days", 24)
        return [f"{ident.utility_provider} - ELECTRICITY BILL", f"Consumer Name: {r['name'].upper()}", f"Consumer No: 4409{ident.sme_id[-4:]}27", f"Service Address: {ident.address}",
                f"Bill Date: {_fmt(as_of - timedelta(days=age))}", "Units Consumed: 2,310", f"Amount Payable: INR {int(r['rev'] * 0.012):,}"]
    raise ValueError(doc_type)


def render_pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 8)
    c.drawString(40, h - 30, BANNER)
    y = h - 90
    for i, line in enumerate(lines):
        c.setFont("Helvetica-Bold" if i < 2 else "Helvetica", 13 if i < 2 else 11)
        c.drawString(50, y, line[:110])
        y -= 24
    c.showPage()
    c.save()
    return buf.getvalue()


def render_png(lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1100, 120 + 56 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
        small = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default(size=30)
        small = ImageFont.load_default(size=18)
    d.text((30, 20), BANNER, fill="black", font=small)
    y = 80
    for line in lines:
        d.text((40, y), line, fill="black", font=font)
        y += 56
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_document(doc_type: str, r: dict, ident: Identity, as_of: date, flaws: dict | None = None, fmt: str = "pdf") -> tuple[str, bytes, str]:
    lines = document_lines(doc_type, r, ident, as_of, flaws)
    if fmt == "png":
        return f"{doc_type.lower()}_{r['id']}.png", render_png(lines), "image/png"
    return f"{doc_type.lower()}_{r['id']}.pdf", render_pdf(lines), "application/pdf"


# Flaw presets for the designed onboarding personas
ONBOARDING_PLAN: dict[str, dict[str, Any]] = {
    # id: {docs to include, flaws, final status intent}
    "SME-1004": {"docs": ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT"],
                 "flaws": {"gst_legal_name": "Bharat Agro Trading Company", "gstin_pan_mismatch": True, "aadhaar_bad_checksum": True}},
    "SME-1007": {"docs": ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT", "UTILITY_BILL"], "flaws": {}},
    "SME-1008": {"docs": ["PAN", "GST", "INCORPORATION"], "flaws": {}},
    "SME-1009": {"docs": ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT", "UTILITY_BILL"], "flaws": {"bill_age_days": 142}},
    "SME-1010": {"docs": ["PAN", "AADHAAR", "GST", "INCORPORATION", "BANK_STATEMENT", "UTILITY_BILL"],
                 "flaws": {"bank_holder": "Lakeview Developers & Builders", "aadhaar_name": "Gopal Venkataraman"}},
}
