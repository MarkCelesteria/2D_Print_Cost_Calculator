"""Sales log: monthly totals (kept forever, tiny) + the last MAX_RECENT receipts (so they can be reopened)."""
import json
import os
import uuid
from datetime import datetime

from printcost.core import pricing

MAX_RECENT = 100
# same keys as the percentages in Settings
CATEGORIES = [("labor", "Labor"), ("maintenance", "Maintenance"), ("electricity", "Electricity"),
              ("spoilage", "Spoilage"), ("printer", "Printer"), ("profit", "Profit")]
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


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _parts(result):
    """Splits what the customer paid into paper, ink and the Settings percentages.
    Shares are scaled by total/gross so they already reflect any discount."""
    total, gross = _num(result.get("total")), _num(result.get("gross"))
    f = total / gross if gross > 0 else 1.0
    parts = {"ink": 0.0, "paper": 0.0, "fee": 0.0, "cats": {k: 0.0 for k, _ in CATEGORIES}}
    if result.get("poster_fee") is not None:  # flat poster/ID fee: ink, paper and handling are inside it
        parts["fee"] = total
    else:
        parts["ink"] = _num(result.get("ink_total")) * f
        parts["paper"] = _num(result.get("paper_total")) * f
        amounts = result.get("category_amounts") or {}
        for k, _ in CATEGORIES:
            parts["cats"][k] = _num(amounts.get(k)) * f
    return parts


def _when(value, fallback):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def log_sale(receipt):
    """Adds one printed job to the monthly totals and the recent list.
    `receipt` is the same dict the Receipt dialog uses: its "when" (which makes the receipt number) is set here
    if it isn't yet, so the receipt reopened from history has the same number and date the customer got."""
    result = receipt.get("result") or {}
    total = _num(result.get("total"))
    pages = int(_num(result.get("num_pages")))
    parts = _parts(result)
    when = _when(receipt.get("when"), datetime.now())
    receipt["when"] = when

    stored = dict(receipt)
    stored["when"] = when.isoformat(timespec="seconds")
    stored["result"] = {k: v for k, v in result.items() if k != "all_coverage"}  # big, and the receipt doesn't use it
    kind = "ID photos" if receipt.get("idphoto") else "Poster" if receipt.get("poster") else "Print"

    data = _load()
    m = data["months"].setdefault(when.strftime("%Y-%m"), {"total": 0.0, "jobs": 0, "pages": 0})
    m["total"] = round(m["total"] + total, 2)
    m["jobs"] += 1
    m["pages"] += pages
    for k in ("ink", "paper", "fee"):
        m[k] = round(m.get(k, 0.0) + parts[k], 2)
    m["discount"] = round(m.get("discount", 0.0) + _num(result.get("discount_amount")), 2)
    cats = m.setdefault("cats", {})
    for k, v in parts["cats"].items():
        cats[k] = round(cats.get(k, 0.0) + v, 2)
    data["recent"].insert(0, {
        "id": uuid.uuid4().hex[:8],
        "ts": stored["when"],
        "kind": kind,
        "files": ", ".join(receipt.get("files") or []),
        "total": total,
        "receipt": stored,
    })
    del data["recent"][MAX_RECENT:]
    _save(data)


def months():
    """[(\"2026-11\", {total, jobs, pages}), ...] newest first."""
    return sorted(_load()["months"].items(), reverse=True)


def recent():
    return _load()["recent"]