"""Standalone helper functions used across validation submodules."""
from typing import Any, Dict, List, Tuple


def resolve_refs(schema: Any, defs: Dict) -> Any:
    """Recursively resolve all $ref in a schema against $defs."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref_path = schema["$ref"]
            parts = ref_path.lstrip("#/").split("/")
            if len(parts) >= 2 and parts[0] == "$defs":
                resolved = defs.get(parts[1], {})
                siblings = {k: v for k, v in schema.items() if k != "$ref"}
                if siblings:
                    merged = dict(resolved)
                    merged.update(siblings)
                    return resolve_refs(merged, defs)
                return resolve_refs(resolved, defs)
            return schema
        return {k: resolve_refs(v, defs) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [resolve_refs(item, defs) for item in schema]
    return schema


def deep_get(data: Dict, key: str) -> Any:
    """Get a value from nested dict, searching recursively."""
    if key in data:
        return data[key]
    for v in data.values():
        if isinstance(v, dict):
            result = deep_get(v, key)
            if result is not None:
                return result
    return None


def iter_string_fields(data: Any, path: str = "") -> List[Tuple[str, str]]:
    """Yield (path, string) pairs for narrative-sensitive checks."""
    out: List[Tuple[str, str]] = []
    if isinstance(data, str):
        out.append((path, data))
    elif isinstance(data, dict):
        for k, v in data.items():
            child = f"{path}.{k}" if path else k
            out.extend(iter_string_fields(v, child))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            out.extend(iter_string_fields(item, f"{path}[{i}]"))
    return out
