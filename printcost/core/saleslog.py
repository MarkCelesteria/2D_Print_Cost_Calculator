import json
import os
import uuid
from datetime import datetime

from printcost.core import pricing

MAX_RECENT = 100
CATEGORIES = [("labor", "Labor"), ("maintenance", "Maintenance"), ("electricity", "Electricity"),
              ("spoilage", "Spoilage"), ("printer", "Printer"), ("profit", "Profit")]
BANKS = [("paper", "Paper"), ("ink", "Ink")] + CATEGORIES
_PATH = os.path.join(os.path.dirname(pricing.SETTINGS_PATH), "sales.json")


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def _load():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("months"), dict) and isinstance(data.get("recent"), list):
            if not isinstance(data.get("spends"), list):
                data["spends"] = []
            return data
    except (OSError, ValueError):
        pass
    return {"months": {}, "recent": [], "spends": []}


def _save(data):
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, default=_default)
    os.replace(tmp, _PATH)


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
    if result.get("poster_fee") is not None:
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
    stored["result"] = {k: v for k, v in result.items() if k != "all_coverage"}
    kind = "ID photos" if receipt.get("idphoto") else "Poster" if receipt.get("poster") else "Print"

    added = {"total": round(total, 2), "pages": pages, "ink": round(parts["ink"], 2),
             "paper": round(parts["paper"], 2), "fee": round(parts["fee"], 2),
             "discount": round(_num(result.get("discount_amount")), 2),
             "cats": {k: round(v, 2) for k, v in parts["cats"].items()}}

    data = _load()
    m = data["months"].setdefault(when.strftime("%Y-%m"), {"total": 0.0, "jobs": 0, "pages": 0})
    m["total"] = round(m["total"] + added["total"], 2)
    m["jobs"] += 1
    m["pages"] += pages
    for k in ("ink", "paper", "fee", "discount"):
        m[k] = round(m.get(k, 0.0) + added[k], 2)
    cats = m.setdefault("cats", {})
    for k, v in added["cats"].items():
        cats[k] = round(cats.get(k, 0.0) + v, 2)
    data["recent"].insert(0, {
        "id": uuid.uuid4().hex[:8],
        "ts": stored["when"],
        "kind": kind,
        "files": ", ".join(receipt.get("files") or []),
        "total": total,
        "added": added,
        "receipt": stored,
    })
    del data["recent"][MAX_RECENT:]
    _save(data)


def months():
    """[(\"2026-11\", {total, jobs, pages}), ...] newest first."""
    return sorted(_load()["months"].items(), reverse=True)


def recent():
    return _load()["recent"]


def _tidy(x):
    x = round(x, 2)
    return 0.0 if abs(x) < 0.005 else x


def delete_sales(ids):
    """Removes receipts from the recent list and takes their amounts back out of the monthly totals."""
    ids = set(ids)
    data = _load()
    keep = []
    for e in data["recent"]:
        if e.get("id") not in ids:
            keep.append(e)
            continue
        a = e.get("added")
        if not a:
            res = (e.get("receipt") or {}).get("result") or {}
            a = {"total": _num(e.get("total")), "pages": int(_num(res.get("num_pages")))}
        key = str(e.get("ts", ""))[:7]
        m = data["months"].get(key)
        if not m:
            continue
        m["total"] = _tidy(m.get("total", 0) - a.get("total", 0))
        m["jobs"] = max(0, m.get("jobs", 0) - 1)
        m["pages"] = max(0, m.get("pages", 0) - a.get("pages", 0))
        for k in ("ink", "paper", "fee", "discount"):
            m[k] = _tidy(m.get(k, 0) - a.get(k, 0))
        cats = m.setdefault("cats", {})
        for c, v in (a.get("cats") or {}).items():
            cats[c] = _tidy(cats.get(c, 0) - v)
        if m["jobs"] <= 0:
            del data["months"][key]
    data["recent"] = keep
    _save(data)


def add_spend(bank, amount, note=""):
    """Takes `amount` out of a bank (e.g. buying a pack of A4 paper from the Paper bank)."""
    data = _load()
    data["spends"].insert(0, {
        "id": uuid.uuid4().hex[:8],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "bank": bank,
        "amount": round(_num(amount), 2),
        "note": (note or "").strip(),
    })
    _save(data)


def delete_spends(ids):
    ids = set(ids)
    data = _load()
    data["spends"] = [s for s in data["spends"] if s.get("id") not in ids]
    _save(data)


def spends():
    return _load()["spends"]


def banks():
    """[(key, label, earned, spent, balance), ...] over all time. Earned comes from the monthly totals,
    so removing a receipt lowers its banks and removing a spending puts the money back."""
    data = _load()
    earned = {k: 0.0 for k, _ in BANKS}
    for m in data["months"].values():
        earned["paper"] += m.get("paper", 0)
        earned["ink"] += m.get("ink", 0)
        for c, v in (m.get("cats") or {}).items():
            if c in earned:
                earned[c] += v
    spent = {k: 0.0 for k, _ in BANKS}
    for s in data["spends"]:
        if s.get("bank") in spent:
            spent[s["bank"]] += _num(s.get("amount"))
    return [(k, label, round(earned[k], 2), round(spent[k], 2), round(earned[k] - spent[k], 2))
            for k, label in BANKS]