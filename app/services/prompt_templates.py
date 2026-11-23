"""Expand shared criteria so generation and review receive the same rules."""
import json
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(path: Path) -> str:
    prompt = path.read_text(encoding="utf-8")
    for token, filename in (
        ("{{CONTEXT_FACTS}}", "context_facts.md"),
        ("{{INTAKE_SUFFICIENCY}}", "intake_sufficiency.md"),
        ("{{SCENARIO_QUALITY_CRITERIA}}", "scenario_quality_criteria.md"),
    ):
        if token in prompt:
            prompt = prompt.replace(token, (PROMPTS / filename).read_text(encoding="utf-8"))
    return prompt


# Strip annotations only at schema nodes. Field names and default/enum values
# can themselves contain "title" or "description" and must not be rewritten.
_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_CHILDREN = {"items", "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
                    "contains", "propertyNames", "not", "if", "then", "else", "contentSchema"}


def _without_annotations(schema):
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in {"title", "description", "examples", "$comment"}:
            continue
        if key in _SCHEMA_MAPS:
            result[key] = {name: _without_annotations(child) for name, child in value.items()}
        elif key in _SCHEMA_LISTS:
            result[key] = [_without_annotations(child) for child in value]
        elif key in _SCHEMA_CHILDREN:
            result[key] = _without_annotations(value)
        else:
            result[key] = value
    return result


def response_schema(model):
    """Compact LLM guidance; the original Pydantic validation stays unchanged."""
    return json.dumps(_without_annotations(model.model_json_schema()), ensure_ascii=False, separators=(",", ":"))
