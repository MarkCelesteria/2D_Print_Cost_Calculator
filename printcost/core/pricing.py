import json
import os
import sys


def _app_data_dir():
    """Next to the code when run from source; in AppData for the packaged exe (its own folder is a temp dir)."""
    if getattr(sys, "frozen", False):
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PrintCostCalculator")
        os.makedirs(path, exist_ok=True)
        return path
    here = os.path.dirname(os.path.abspath(__file__))   # printcost/core -> the project folder
    return os.path.dirname(os.path.dirname(here))        # settings.json stays next to main.py


SETTINGS_PATH = os.path.join(_app_data_dir(), "settings.json")

DEFAULT_SETTINGS = {
    "paper_sizes": [
        # bond paper (US)
        {"name": "Short (8.5x11 in)", "width_in": 8.5, "height_in": 11.0, "price": 1.0},
        {"name": "Long (8.5x13 in)", "width_in": 8.5, "height_in": 13.0, "price": 1.5},
        {"name": "Legal (8.5x14 in)", "width_in": 8.5, "height_in": 14.0, "price": 1.75},
        {"name": "Executive (7.25x10.5 in)", "width_in": 7.25, "height_in": 10.5, "price": 1.0},
        {"name": "Statement / Half letter (5.5x8.5 in)", "width_in": 5.5, "height_in": 8.5, "price": 0.75},
        {"name": "Tabloid / Ledger (11x17 in)", "width_in": 11.0, "height_in": 17.0, "price": 2.75},
        # A series
        {"name": "A6 (4.13x5.83 in)", "width_in": 4.13, "height_in": 5.83, "price": 0.5},
        {"name": "A5 (5.83x8.27 in)", "width_in": 5.83, "height_in": 8.27, "price": 0.75},
        {"name": "A4 (8.27x11.69 in)", "width_in": 8.27, "height_in": 11.69, "price": 1.25},
        {"name": "A3 (11.69x16.54 in)", "width_in": 11.69, "height_in": 16.54, "price": 2.75},
        {"name": "A3+ / Super B (13x19 in)", "width_in": 13.0, "height_in": 19.0, "price": 3.5},
        # B series
        {"name": "B5 (7.17x10.12 in)", "width_in": 7.17, "height_in": 10.12, "price": 1.0},
        {"name": "B4 (10.12x14.33 in)", "width_in": 10.12, "height_in": 14.33, "price": 2.0},
        # photo sizes
        {"name": "2R (2.5x3.5 in)", "width_in": 2.5, "height_in": 3.5, "price": 0.25},
        {"name": "3R (3.5x5 in)", "width_in": 3.5, "height_in": 5.0, "price": 0.25},
        {"name": "4R (4x6 in)", "width_in": 4.0, "height_in": 6.0, "price": 0.5},
        {"name": "5R (5x7 in)", "width_in": 5.0, "height_in": 7.0, "price": 0.75},
        {"name": "6R (6x8 in)", "width_in": 6.0, "height_in": 8.0, "price": 1.0},
        {"name": "8R (8x10 in)", "width_in": 8.0, "height_in": 10.0, "price": 1.5},
        {"name": "10R (10x12 in)", "width_in": 10.0, "height_in": 12.0, "price": 2.0},
        {"name": "11x14 in", "width_in": 11.0, "height_in": 14.0, "price": 2.5},
        {"name": "12x18 in", "width_in": 12.0, "height_in": 18.0, "price": 3.75},
        # cards and envelopes
        {"name": "Hagaki / Postcard (3.94x5.83 in)", "width_in": 3.94, "height_in": 5.83, "price": 0.5},
        {"name": "Panorama (4x10 in)", "width_in": 4.0, "height_in": 10.0, "price": 0.75},
        {"name": "Envelope #10 (4.13x9.5 in)", "width_in": 4.13, "height_in": 9.5, "price": 0.75},
        {"name": "Envelope DL (4.33x8.66 in)", "width_in": 4.33, "height_in": 8.66, "price": 0.75},
    ],
    "paper_types": [
        {"name": "Plain paper", "price_factor": 1.0, "ink_factor": 1.0, "driver": "Plain Paper"},
        {"name": "Matte photo paper", "price_factor": 4.0, "ink_factor": 1.2, "driver": "Matte Photo Paper"},
        {"name": "Glossy photo paper", "price_factor": 6.0, "ink_factor": 1.3, "driver": "Glossy Photo Paper"},
        {"name": "Semi-gloss / Luster", "price_factor": 7.0, "ink_factor": 1.3, "driver": "Semi-gloss / Luster Photo Paper"},
        {"name": "Cardstock", "price_factor": 3.0, "ink_factor": 1.0, "driver": "Thick Paper / Cardstock"},
        {"name": "Sticker paper", "price_factor": 5.0, "ink_factor": 1.1, "driver": "Glossy Photo Paper"},
    ],
    "ink_prices": {
        "C": 10.0,
        "M": 10.0,
        "Y": 10.0,
        "K": 13.0,
    },
    "default_percentages": {
        "labor": 5.0,
        "maintenance": 5.0,
        "electricity": 3.0,
        "spoilage": 3.0,
        "printer": 10.0,
        "profit": 10.0,
    },
    "quality_multipliers": {"draft": 0.7, "standard": 1.0, "high": 1.5},
    "high_black_mix": 60.0,
    "min_price_per_page": None,
    "min_price_color": None,
    "max_price_per_page": None,
    "color_threshold_pct": 0.5,
    "shop_name": "Print Cost Calculator",
    "has_permit": False,
    "tin": "",
    "discounts": [],
    "bulk_discounts": [],
    "asked_about_autostart": False,
    "poster": {
        "cols": 3, "rows": 2, "landscape": False, "fill": False,
        "overlap_in": 0.0, "margin_in": 0.25, "marks": False, "labels": False,
        "pricing_mode": "calculated", "fee": 0.0,
    },
    "duplex": {"extra_per_sheet": 0.0, "discount_pct": 0.0, "margin_in": 0.25, "printers": {}},
    "idphoto": {
        "entries": [{"name": "2×2 in", "w": 2.0, "h": 2.0, "qty": 6}],
        "orientation": "auto", "gap_in": 0, "margin_in": 0.25, "lines": False,
        "pricing_mode": "calculated", "fee": 0.0, "sets": {},
    },
}

def load_default_settings() -> dict:
    import copy
    return copy.deepcopy(DEFAULT_SETTINGS)


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        save_settings(DEFAULT_SETTINGS)
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    for key, value in DEFAULT_SETTINGS.items():
        data.setdefault(key, json.loads(json.dumps(value)))
    for group in ("ink_prices", "default_percentages", "quality_multipliers", "poster", "duplex", "idphoto"):
        for k, v in DEFAULT_SETTINGS[group].items():
            data[group].setdefault(k, json.loads(json.dumps(v)))
    if not data.get("paper_sizes_v2"):  # one time: add the newer default sizes; existing ones and prices stay as they are
        sizes, defaults = data["paper_sizes"], DEFAULT_SETTINGS["paper_sizes"]
        for n, p in enumerate(defaults):
            if p["name"] in {s["name"] for s in sizes}:
                continue
            prev = defaults[n - 1]["name"] if n else None
            at = next((i + 1 for i, s in enumerate(sizes) if s["name"] == prev), 0 if n == 0 else len(sizes))
            sizes.insert(at, dict(p))
        data["paper_sizes_v2"] = True
    return data


def bulk_pct(tiers, pages):
    """Highest bulk discount % whose page range contains `pages`."""
    return max((t["pct"] for t in tiers or []
                if t["min"] <= pages and (not t.get("max") or pages <= t["max"])), default=0.0)


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)