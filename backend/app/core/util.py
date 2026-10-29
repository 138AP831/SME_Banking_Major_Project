"""Small shared helpers: JSON sanitising, masking, clock, ids."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

import numpy as np

MAX_LIST_ITEMS = 400
MAX_STR = 4000


def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    """Convert numpy / dates / sets into plain JSON-safe python (needed for the checkpointer)."""
    if _depth > 12:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 6)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v, _depth + 1) for v in obj]
    if isinstance(obj, np.ndarray):
        return [to_jsonable(v, _depth + 1) for v in obj.tolist()]
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump(), _depth + 1)
    return str(obj)


def summarize(obj: Any, max_items: int = 120, max_str: int = 600) -> Any:
    """Trim very large structures for event storage while keeping them inspectable."""
    obj = to_jsonable(obj)

    def _trim(v: Any) -> Any:
        if isinstance(v, str):
            return v if len(v) <= max_str else v[:max_str] + f"… (+{len(v) - max_str} chars)"
        if isinstance(v, list):
            if len(v) > max_items:
                return [_trim(x) for x in v[:max_items]] + [f"… (+{len(v) - max_items} more items)"]
            return [_trim(x) for x in v]
        if isinstance(v, dict):
            return {k: _trim(x) for k, x in v.items()}
        return v

    return _trim(obj)


# ----------------------------------------------------------------------------- masking
def mask_pan(pan: str | None) -> str | None:
    if not pan:
        return pan
    return pan[:2] + "•" * (len(pan) - 4) + pan[-2:] if len(pan) >= 6 else "•" * len(pan)


def mask_gstin(g: str | None) -> str | None:
    if not g:
        return g
    return g[:2] + "•" * (len(g) - 5) + g[-3:] if len(g) >= 8 else "•" * len(g)


def mask_aadhaar_last4(last4: str | None) -> str:
    return f"XXXX XXXX {last4}" if last4 else "XXXX XXXX ••••"


def mask_phone(p: str | None) -> str | None:
    if not p:
        return p
    return p[:3] + "•" * max(0, len(p) - 5) + p[-2:]


def mask_email(e: str | None) -> str | None:
    if not e or "@" not in e:
        return e
    user, dom = e.split("@", 1)
    return user[:1] + "•••@" + dom


def mask_account(a: str | None) -> str | None:
    if not a:
        return a
    return "•" * max(0, len(a) - 4) + a[-4:]


_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")


def scrub_for_logs(text: str) -> str:
    """Remove Aadhaar-like numbers from anything that might be logged."""
    return _AADHAAR_RE.sub("XXXX XXXX ••••", text)


# ----------------------------------------------------------------------------- misc
def pct(a: float, b: float) -> float:
    return (a / b) if b else 0.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def inr(x: float) -> str:
    """Human INR formatting (Cr / L)."""
    ax = abs(x)
    sign = "-" if x < 0 else ""
    if ax >= 1e7:
        return f"{sign}₹{ax / 1e7:.2f} Cr"
    if ax >= 1e5:
        return f"{sign}₹{ax / 1e5:.1f} L"
    return f"{sign}₹{ax:,.0f}"
