"""Schema validation mixin for PSURValidator."""
from typing import Any, Dict, List

from validation._helpers import resolve_refs


class SchemaValidationMixin:
    """JSON Schema validation, required-field checks, key fidelity."""

    def _build_resolved_section_schemas(self) -> Dict[str, Dict]:
        """Build fully resolved schemas for each section."""
        sections_schema = self.schema.get("properties", {}).get("sections", {})
        if "$ref" in sections_schema:
            ref_path = sections_schema["$ref"]
            parts = ref_path.lstrip("#/").split("/")
            resolved = self.schema
            for part in parts:
                resolved = resolved.get(part, {})
            sections_schema = resolved

        result = {}
        for section_key, section_def in sections_schema.get("properties", {}).items():
            result[section_key] = resolve_refs(section_def, self._defs)
        return result

    def validate_section(self, section_key: str, section_data: Dict[str, Any]) -> List[str]:
        """Validate a single section against its resolved JSON Schema.

        Returns list of error strings (empty if valid).
        Used by the agent for auto-retry.
        """
        return self._validate_section_schema(section_key, section_data)

    def _validate_section_schema(self, section_key: str, section_data: Dict[str, Any]) -> List[str]:
        """Validate section data against its resolved template schema using jsonschema."""
        errors = []
        section_schema = self._resolved_section_schemas.get(section_key)
        if not section_schema:
            return errors

        try:
            import jsonschema
            clean_schema = self._strip_ui_hints(section_schema)
            validator = jsonschema.Draft202012Validator(clean_schema)
            for error in sorted(validator.iter_errors(section_data), key=lambda e: list(e.path)):
                path = ".".join(str(p) for p in error.absolute_path)
                if not path:
                    path = "(root)"
                msg = error.message
                if len(msg) > 200:
                    msg = msg[:200] + "..."
                errors.append(f"SCHEMA [{section_key}] {path}: {msg}")
        except ImportError:
            errors.extend(self._basic_required_check(section_key, section_data, section_schema))
        except Exception as e:
            errors.append(f"SCHEMA [{section_key}]: validation error: {e}")

        return errors

    @staticmethod
    def _strip_ui_hints(schema: Any) -> Any:
        """Remove non-standard 'ui' keys from schema so jsonschema doesn't choke."""
        if isinstance(schema, dict):
            return {k: SchemaValidationMixin._strip_ui_hints(v) for k, v in schema.items() if k != "ui"}
        elif isinstance(schema, list):
            return [SchemaValidationMixin._strip_ui_hints(item) for item in schema]
        return schema

    def _basic_required_check(self, section_key: str, data: Dict, schema: Dict, path: str = "") -> List[str]:
        """Fallback required-field check without jsonschema library."""
        errors = []
        if not isinstance(schema, dict) or not isinstance(data, dict):
            return errors

        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field in required:
            if field not in data:
                errors.append(f"SCHEMA [{section_key}] {path}.{field}: missing required field")

        for field, field_schema in properties.items():
            if field in data and isinstance(field_schema, dict) and field_schema.get("type") == "object":
                errors.extend(self._basic_required_check(
                    section_key, data.get(field, {}), field_schema, f"{path}.{field}"
                ))

        return errors

    def _check_key_fidelity(self, psur: Dict[str, Any]) -> List[str]:
        """Ensure section payload contains only schema-defined keys."""
        errors = []
        sections = psur.get("sections", {})

        for section_key, section_data in sections.items():
            if not isinstance(section_data, dict):
                continue
            if isinstance(section_data, dict) and "error" in section_data:
                continue

            schema = self._resolved_section_schemas.get(section_key)
            if not schema:
                continue

            errors.extend(self._find_unexpected_keys(
                section_data,
                schema,
                path=f"sections.{section_key}"
            ))

        return errors

    def _find_unexpected_keys(self, data: Any, schema: Dict[str, Any], path: str) -> List[str]:
        """Recursively detect keys not present in schema properties."""
        errors: List[str] = []

        if not isinstance(data, dict) or not isinstance(schema, dict):
            return errors

        properties = schema.get("properties", {})
        additional_properties = schema.get("additionalProperties", True)

        for key, value in data.items():
            if key not in properties:
                if additional_properties is False or path.startswith("sections."):
                    errors.append(
                        f"KEY_FIDELITY: Unexpected key '{path}.{key}' not defined in template schema"
                    )
                continue

            child_schema = properties.get(key, {})
            if isinstance(value, dict):
                errors.extend(self._find_unexpected_keys(value, child_schema, f"{path}.{key}"))
            elif isinstance(value, list):
                items_schema = child_schema.get("items", {}) if isinstance(child_schema, dict) else {}
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        errors.extend(self._find_unexpected_keys(item, items_schema, f"{path}.{key}[{idx}]"))

        return errors
