"""Demo clock: the synthetic dataset is anchored to an "as of" date stored in the DB."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.config import get_settings


def now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_as_of_date() -> date:
    """The demo date the synthetic data is anchored to (persisted at seed time)."""
    from app.db.session import session_scope
    from app.models.entities import AppSetting

    try:
        with session_scope() as s:
            row = s.get(AppSetting, "as_of_date")
            if row and row.value:
                return date.fromisoformat(row.value)
    except Exception:
        pass
    cfg = get_settings().demo_as_of_date
    return date.fromisoformat(cfg) if cfg else date.today()
