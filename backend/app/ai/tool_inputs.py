"""Alfred catalog input contract; standard library only."""


def validate_tool_arguments(value: object, schema: dict, *, path: str = "arguments") -> str | None:
    """Validate the JSON Schema subset used by the catalog, without coercing inputs.

    Error messages name fields and constraints, never supplied values. This is
    applied before every invocation; domain validation remains with its owner.
    """
    import math

    types = schema.get("type")
    allowed = [types] if isinstance(types, str) else types or []
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }
    if allowed and not any(matches.get(kind, False) for kind in allowed):
        return f"{path}: invalid type."
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: value is outside the allowed choices."
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return f"{path}: must be finite."
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: below the minimum."
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: above the maximum."
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key}: required."
        for key, item in value.items():
            child = properties.get(key, schema.get("additionalProperties", {}))
            if child is False:
                return f"{path}: unexpected field."
            if isinstance(child, dict):
                error = validate_tool_arguments(item, child, path=f"{path}.{key}")
                if error:
                    return error
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            error = validate_tool_arguments(item, schema["items"], path=f"{path}[{index}]")
            if error:
                return error
    return None


def validate_tool_schema(schema: dict) -> str | None:
    """Fail catalog assembly if a new constraint would be silently ignored."""
    supported = {
        "type",
        "description",
        "enum",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "minimum",
        "maximum",
    }
    if set(schema) - supported:
        return "Unsupported input schema keyword."
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if not isinstance(kinds, list) or any(
        kind not in {"object", "array", "string", "boolean", "integer", "number", "null"}
        for kind in kinds
    ):
        return "Unsupported input schema type."
    children = list(schema.get("properties", {}).values())
    children += [
        schema[key]
        for key in ("items", "additionalProperties")
        if isinstance(schema.get(key), dict)
    ]
    for child in children:
        if not isinstance(child, dict):
            return "Input property must have a schema."
        error = validate_tool_schema(child)
        if error:
            return error
    return None
