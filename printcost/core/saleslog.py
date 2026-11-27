"""Sales log: monthly totals (kept forever, tiny) + the last MAX_RECENT receipts (so they can be reopened)."""
import json
import os
import uuid
from datetime import datetime

from printcost.core import pricing

MAX_RECENT = 100
_PATH = os.path.join(os.path.dirname(pricing.SETTINGS_PATH), "sales.json")


def _default(o):
    if hasattr(o, "item"):  # numpy numbers
        return o.item()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def _load():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("months"), dict) and isinstance(data.get("recent"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"months": {}, "recent": []}


def _save(data):
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, default=_default)
    os.replace(tmp, _PATH)  # a crash mid-write can't corrupt the log


def log_sale(receipt):
    """Adds one printed job to the monthly totals and the recent list."""
    result = receipt.get("result") or {}
    total = float(result.get("total", 0) or 0)
    pages = int(result.get("num_pages", 0) or 0)
    kind = "ID photos" if receipt.get("idphoto") else "Poster" if receipt.get("poster") else "Print"
    now = datetime.now()

    data = _load()
    m = data["months"].setdefault(now.strftime("%Y-%m"), {"total": 0.0, "jobs": 0, "pages": 0})
    m["total"] = round(m["total"] + total, 2)
    m["jobs"] += 1
    m["pages"] += pages
    data["recent"].insert(0, {
        "id": uuid.uuid4().hex[:8],
        "ts": now.isoformat(timespec="seconds"),
        "kind": kind,
        "files": ", ".join(receipt.get("files") or []),
        "total": total,
        "receipt": receipt,
    })
    del data["recent"][MAX_RECENT:]
    _save(data)


def months():
    """[(\"2026-11\", {total, jobs, pages}), ...] newest first."""
    return sorted(_load()["months"].items(), reverse=True)


def recent():
    return _load()["recent"]
