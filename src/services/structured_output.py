"""Strict transport structure is separate from authoritative server validation."""

import json
from dataclasses import replace

from pydantic import BaseModel, ValidationError

from src.services.invocation_types import InvocationError


def strict_schema(model):
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise InvocationError("configuration_unavailable")
    schema = model.model_json_schema()
    if schema.get("type") != "object" or "anyOf" in schema:
        raise InvocationError("configuration_unavailable")

    def convert(node):
        allowed = {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "$defs",
            "$ref",
            "items",
            "anyOf",
            "enum",
            "const",
            "title",
            "description",
            "default",
            "minimum",
            "maximum",
            "multipleOf",
            "minLength",
            "maxLength",
            "pattern",
            "minItems",
            "maxItems",
        }
        if not isinstance(node, dict) or node.keys() - allowed:
            raise InvocationError("configuration_unavailable")
        node.pop("default", None)
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if node.get("type") == "object":
            if node.get("additionalProperties", False) is not False:
                raise InvocationError("configuration_unavailable")
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        elif not any(k in node for k in ("type", "$ref", "anyOf")):
            raise InvocationError("configuration_unavailable")
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                convert(child)
        if "items" in node:
            convert(node["items"])
        for child in node.get("anyOf", []):
            convert(child)

    convert(schema)
    return schema


def reject_non_json_number(value):
    raise ValueError("Non-JSON number")


def validate_output(request, content):
    try:
        value = json.loads(content, parse_constant=reject_non_json_number)
    except (ValueError, TypeError):
        raise InvocationError("invalid_json") from None
    try:
        return request.output_schema.model_validate(
            value, strict=True, context=request.validation_context
        ).model_dump()
    except ValidationError:
        raise InvocationError("invalid_output") from None


def structured_event(request, event):
    if event.type != "completed":
        return event
    try:
        value = validate_output(request, event.text)
    except InvocationError as error:
        return replace(event, type="error", text="", error_code=error.code)
    return replace(event, text="", structured=value)
