from __future__ import annotations

import json
from pathlib import Path

from contracts.models import contract_schemas


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY_ROOT / "contracts/generated/analysis-manifest.schema.json"
TYPESCRIPT_PATH = REPOSITORY_ROOT / "src/contracts/generated/analysisManifestV1.ts"
RUST_PATH = REPOSITORY_ROOT / "src-tauri/src/contracts_generated.rs"


def _schema_document():
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "models": contract_schemas()}


def _definitions(document):
    definitions = {}
    for schema in document["models"].values():
        for name, definition in schema.get("$defs", {}).items():
            existing = definitions.get(name)
            if existing is not None and existing != definition:
                raise RuntimeError(f"conflicting schema definition: {name}")
            definitions[name] = definition
    return dict(sorted(definitions.items()))


def _ref_name(schema):
    return schema["$ref"].rsplit("/", maxsplit=1)[-1]


def _typescript_type(schema):
    if "$ref" in schema:
        return _ref_name(schema)
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return " | ".join(_typescript_type(item) for item in schema[key])
    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        return f"readonly {_typescript_type(schema['items'])}[]"
    if schema_type == "object":
        required = set(schema.get("required", []))
        fields = ["{"]
        for name, property_schema in sorted(schema.get("properties", {}).items()):
            optional = "" if name in required else "?"
            fields.append(f"  readonly {name}{optional}: {_typescript_type(property_schema)};")
        fields.append("}")
        return "\n".join(fields)
    raise RuntimeError(f"unsupported TypeScript schema: {schema}")


def _typescript_alias(name, schema):
    return f"export type {name} = {_typescript_type(schema)};"


def render_typescript(document) -> str:
    definitions = _definitions(document)
    lines = [_typescript_alias(name, schema) for name, schema in definitions.items()]
    lines.extend(
        _typescript_alias(name, schema)
        for name, schema in sorted(document["models"].items())
    )
    return "\n\n".join(lines) + "\n"


def _snake_case(value):
    characters = []
    for index, character in enumerate(value):
        if character.isupper() and index > 0 and not value[index - 1].isupper():
            characters.append("_")
        characters.append(character.lower())
    return "".join(characters)


def _pascal_case(value):
    return "".join(part.capitalize() for part in value.split("_"))


def _rust_type(schema):
    if "$ref" in schema:
        return _ref_name(schema)
    if "const" in schema:
        constant = schema["const"]
        if isinstance(constant, bool):
            return "bool"
        if isinstance(constant, int):
            return "u64"
        if isinstance(constant, float):
            return "f64"
        return "String"
    if "enum" in schema:
        sample = schema["enum"][0]
        if isinstance(sample, bool):
            return "bool"
        if isinstance(sample, int):
            return "u64"
        if isinstance(sample, float):
            return "f64"
        return "String"
    for key in ("oneOf", "anyOf"):
        if key in schema:
            variants = schema[key]
            if all("$ref" in variant for variant in variants):
                return "ReviewResolution"
            if len(variants) == 2 and any(item.get("type") == "null" for item in variants):
                nested = next(item for item in variants if item.get("type") != "null")
                return f"Option<{_rust_type(nested)}>"
            raise RuntimeError(f"unsupported Rust union: {schema}")
    schema_type = schema.get("type")
    if schema_type == "string":
        return "String"
    if schema_type == "integer":
        return "u64"
    if schema_type == "number":
        return "f64"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "array":
        return f"Vec<{_rust_type(schema['items'])}>"
    raise RuntimeError(f"unsupported Rust schema: {schema}")


def _rust_struct(name, schema):
    required = set(schema.get("required", []))
    lines = [
        "#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]",
        "#[serde(rename_all = \"camelCase\", deny_unknown_fields)]",
        f"pub struct {name} {{",
    ]
    for property_name, property_schema in sorted(schema.get("properties", {}).items()):
        field_type = _rust_type(property_schema)
        if property_name not in required:
            field_type = f"Option<{field_type}>"
        lines.append(f"    pub {_snake_case(property_name)}: {field_type},")
    lines.append("}")
    return "\n".join(lines)


def _rust_union(name, schema, definitions):
    lines = [
        "#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]",
        "#[serde(tag = \"kind\", rename_all = \"snake_case\", deny_unknown_fields)]",
        f"pub enum {name} {{",
    ]
    for item in schema["oneOf"]:
        variant_schema = definitions[_ref_name(item)]
        properties = variant_schema["properties"]
        variant = _pascal_case(properties["kind"]["const"])
        lines.append(f"    {variant} {{")
        for property_name, property_schema in sorted(properties.items()):
            if property_name == "kind":
                continue
            rust_name = _snake_case(property_name)
            if rust_name != property_name:
                lines.append(f'        #[serde(rename = "{property_name}")]')
            lines.append(f"        {rust_name}: {_rust_type(property_schema)},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def _rust_enum(name, schema):
    lines = [
        "#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]",
        "#[serde(rename_all = \"snake_case\")]",
        f"pub enum {name} {{",
    ]
    lines.extend(f"    {_pascal_case(value)}," for value in schema["enum"])
    lines.append("}")
    return "\n".join(lines)


def render_rust(document) -> str:
    definitions = _definitions(document)
    union_definition_names = {
        _ref_name(item)
        for schema in document["models"].values()
        for item in schema.get("oneOf", [])
        if "$ref" in item
    }
    lines = ["use serde::{Deserialize, Serialize};"]
    for name, schema in definitions.items():
        if name in union_definition_names:
            continue
        lines.append(_rust_enum(name, schema) if "enum" in schema else _rust_struct(name, schema))
    for name, schema in sorted(document["models"].items()):
        lines.append(
            _rust_union(name, schema, definitions)
            if "oneOf" in schema
            else _rust_struct(name, schema)
        )
    return "\n\n".join(lines) + "\n"


def generated_outputs() -> dict[Path, str]:
    document = _schema_document()
    return {
        SCHEMA_PATH: json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        TYPESCRIPT_PATH: render_typescript(document),
        RUST_PATH: render_rust(document),
    }


def write_outputs() -> None:
    for path, content in generated_outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def stale_outputs() -> list[Path]:
    return [
        path for path, expected in generated_outputs().items()
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
