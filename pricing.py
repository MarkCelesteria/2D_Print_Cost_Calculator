import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "paper_sizes": [
        {"name": "Short (8.5x11 in)", "width_in": 8.5, "height_in": 11.0, "price": 1.0},
        {"name": "Long / Legal (8.5x13 in)", "width_in": 8.5, "height_in": 13.0, "price": 1.5},
        {"name": "A4 (8.27x11.69 in)", "width_in": 8.27, "height_in": 11.69, "price": 1.25},
    ],
    "ink_prices": {
        "C": 15.0,
        "M": 15.0,
        "Y": 12.0,
        "K": 8.0,
    },
    "default_percentages": {
        "labor": 5.0,
        "maintenance": 5.0,
        "electricity": 3.0,
        "spoilage": 3.0,
        "printer": 10.0,
        "profit": 10.0,
    },
    "quality_multipliers": {"draft": 0.7, "standard": 1.0, "high": 2.8},
    "min_price_per_page": None,
    "min_price_color": None,
    "color_threshold_pct": 0.5,
    "default_discount": 0.0,
    "asked_about_autostart": False,
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
    for group in ("ink_prices", "default_percentages", "quality_multipliers"):
        for k, v in DEFAULT_SETTINGS[group].items():
            data[group].setdefault(k, v)
    return data


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
