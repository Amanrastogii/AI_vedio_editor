"""Per-project editor settings (stored in Project.editor_settings) with defaults + validation."""
from copy import deepcopy
from typing import Any, Dict, Optional

CAPTION_PRESETS = ("classic", "bold", "karaoke", "minimal")
REFRAME_MODES = ("smart", "center", "fit")

DEFAULTS: Dict[str, Any] = {
    "captions": {
        "enabled": False,          # burn captions into renders
        "preset": "bold",          # classic | bold | karaoke | minimal
        "position": "bottom",      # top | center | bottom
        "font_size": 64,           # at 1080p height
        "uppercase": False,
        "max_chars": 32,           # per caption line
        "max_lines": 2,
        "highlight_color": "#FFD400",
        "text_color": "#FFFFFF",
        "hide_fillers": True,      # don't show "um"/"uh" in captions
    },
    "reframe": {
        "mode": "smart",           # when output aspect ≠ source: smart (follow subject) | center | fit (bars)
    },
}


def _clean_color(v: Any, fallback: str) -> str:
    s = str(v or "")
    return s.upper() if len(s) == 7 and s.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in s[1:]) \
        else fallback


def resolve(stored: Optional[Dict]) -> Dict[str, Any]:
    """Stored (possibly partial / stale) settings → complete, valid settings."""
    out = deepcopy(DEFAULTS)
    for section, values in (stored or {}).items():
        if section in out and isinstance(values, dict):
            for k, v in values.items():
                if k in out[section] and v is not None:
                    out[section][k] = v
    c = out["captions"]
    c["enabled"] = bool(c["enabled"])
    c["preset"] = c["preset"] if c["preset"] in CAPTION_PRESETS else DEFAULTS["captions"]["preset"]
    c["position"] = c["position"] if c["position"] in ("top", "center", "bottom") else "bottom"
    c["font_size"] = int(min(160, max(20, int(c["font_size"]))))
    c["uppercase"] = bool(c["uppercase"])
    c["max_chars"] = int(min(60, max(10, int(c["max_chars"]))))
    c["max_lines"] = int(min(3, max(1, int(c["max_lines"]))))
    c["highlight_color"] = _clean_color(c["highlight_color"], DEFAULTS["captions"]["highlight_color"])
    c["text_color"] = _clean_color(c["text_color"], DEFAULTS["captions"]["text_color"])
    c["hide_fillers"] = bool(c["hide_fillers"])
    r = out["reframe"]
    r["mode"] = r["mode"] if r["mode"] in REFRAME_MODES else "smart"
    return out


def merge(stored: Optional[Dict], patch: Dict) -> Dict[str, Any]:
    base = resolve(stored)
    for section, values in (patch or {}).items():
        if section in base and isinstance(values, dict):
            base[section].update({k: v for k, v in values.items() if k in base[section]})
    return resolve(base)
