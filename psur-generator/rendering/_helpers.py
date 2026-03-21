"""Shared helpers and constants for the PSUR DOCX renderer."""
import re

# ── Checkbox characters ──────────────────────────────────────────────
CHECK_YES = "\u2611"  # ☑
CHECK_NO  = "\u2610"  # ☐

# ── Font constants ───────────────────────────────────────────────────
CB_FONT   = "Segoe UI Symbol"
BODY_FONT = "Arial"

# ── Placeholder regex ────────────────────────────────────────────────
PH_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


# ── Utility functions ────────────────────────────────────────────────

def deep_get(obj, path: str, default=None):
    """Walk nested dicts: 'a.b.c' → obj['a']['b']['c']."""
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
    return obj if obj is not None else default


def cb(condition) -> str:
    """Return checked/unchecked checkbox character."""
    return CHECK_YES if condition else CHECK_NO


def stringify(val) -> str:
    """Safely stringify a value."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, list):
        return "\n".join(str(v) for v in val)
    return str(val)
