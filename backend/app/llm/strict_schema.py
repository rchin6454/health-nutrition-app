"""Turn a Pydantic model into a JSON Schema that Groq's strict structured-output mode accepts."""

from typing import Any

from pydantic import BaseModel

# Annotation-only keywords that carry no constraint; dropped to keep the schema minimal.
_DROP_KEYS = {"title", "description", "default", "examples"}


def to_groq_strict(model: type[BaseModel]) -> dict[str, Any]:
    """Return `model`'s JSON Schema with:

    - every `$ref` inlined (no `$defs`)
    - `additionalProperties: false` on every object
    - every property listed in `required`
    - nullable fields as `{"type": [..., "null"]}` and `None`-only fields as `{"type": "null"}`
    """
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})
    result = _convert(schema, defs)
    assert isinstance(result, dict)  # noqa: S101 - the root of a model schema is always an object
    return result


def _convert(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_convert(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _convert(defs[name], defs)

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key == "properties":  # keys here are field names, not schema keywords
            out[key] = {name: _convert(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _convert(value, defs)

    if "anyOf" in out:
        collapsed = _collapse_nullable(out["anyOf"])
        if collapsed is not None:
            del out["anyOf"]
            out.update(collapsed)

    if out.get("type") == "object":
        props: dict[str, Any] = out.get("properties", {})
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False

    return out


def _collapse_nullable(options: list[Any]) -> dict[str, Any] | None:
    """`anyOf: [{type: X}, {type: null}]` → `{type: [X, "null"]}` when X is a plain scalar type."""
    if len(options) != 2 or {"type": "null"} not in options:
        return None
    other = next(o for o in options if o != {"type": "null"})
    if set(other) != {"type"} or not isinstance(other["type"], str):
        return None
    return {"type": [other["type"], "null"]}
