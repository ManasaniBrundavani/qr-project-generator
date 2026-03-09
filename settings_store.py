import json
import os


SETTINGS_FILE = "app_settings.json"


DEFAULT_SETTINGS = {
    "auto_detect_ip": True,
    "manual_qr_base_url": "",
    "public_base_url": "",
    "flask_port": 5000,
    "auto_update_qr_urls": True,
    "last_qr_base_url": "",
    "expiry_enabled": True,
    "expiry_days": 150,
    "video_fit": "contain",
    "font_scale": 1.0,
    "spacing_scale": 1.0,
    "grid_columns": 6,
}


def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
                if isinstance(data, dict):
                    settings.update(data)
        except Exception:
            # Fall back to defaults if settings file is invalid.
            pass
    return settings


def save_settings(settings):
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings or {})
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file_obj:
        json.dump(merged, file_obj, indent=2)
    return merged
