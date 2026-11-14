import json
import os
import sys


def _app_data_dir():
    """Next to the code when run from source; in AppData for the packaged exe (its own folder is a temp dir)."""
    if getattr(sys, "frozen", False):
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PrintCostCalculator")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(_app_data_dir(), "settings.json")

DEFAULT_SETTINGS = {
    "paper_sizes": [
        {"name": "Short (8.5x11 in)", "width_in": 8.5, "height_in": 11.0, "price": 1.0},
        {"name": "Long (8.5x13 in)", "width_in": 8.5, "height_in": 13.0, "price": 1.5},
        {"name": "Legal (8.5x14 in)", "width_in": 8.5, "height_in": 14.0, "price": 1.75},
        {"name": "A4 (8.27x11.69 in)", "width_in": 8.27, "height_in": 11.69, "price": 1.25},
        {"name": "A5 (5.83x8.27 in)", "width_in": 5.83, "height_in": 8.27, "price": 0.75},
        {"name": "A6 (4.13x5.83 in)", "width_in": 4.13, "height_in": 5.83, "price": 0.5},
        {"name": "3R (3.5x5 in)", "width_in": 3.5, "height_in": 5.0, "price": 0.25},
        {"name": "4R (4x6 in)", "width_in": 4.0, "height_in": 6.0, "price": 0.5},
        {"name": "5R (5x7 in)", "width_in": 5.0, "height_in": 7.0, "price": 0.75},
        {"name": "6R (6x8 in)", "width_in": 6.0, "height_in": 8.0, "price": 1.0},
        {"name": "8R (8x10 in)", "width_in": 8.0, "height_in": 10.0, "price": 1.5},
        {"name": "10R (10x12 in)", "width_in": 10.0, "height_in": 12.0, "price": 2.0},
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
    for group in ("ink_prices", "default_percentages", "quality_multipliers", "poster"):
        for k, v in DEFAULT_SETTINGS[group].items():
            data[group].setdefault(k, v)
    if not data.get("a5_a6_added"):
        have = {p["name"] for p in data["paper_sizes"]}
        at = next((i + 1 for i, p in enumerate(data["paper_sizes"]) if p["name"].startswith("A4")),
                  len(data["paper_sizes"]))
        for p in DEFAULT_SETTINGS["paper_sizes"]:
            if p["name"].startswith(("A5", "A6")) and p["name"] not in have:
                data["paper_sizes"].insert(at, dict(p))
                at += 1
        data["a5_a6_added"] = True
    return data


def bulk_pct(tiers, pages):
    """Highest bulk discount % whose page range contains `pages`."""
    return max((t["pct"] for t in tiers or []
                if t["min"] <= pages and (not t.get("max") or pages <= t["max"])), default=0.0)


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)