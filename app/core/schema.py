from typing import Any

from pydantic import BaseModel


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema autocontenido (sin $ref) con additionalProperties: false en cada objeto.

    Es el formato que exigen `strict: true` en tools y `output_config.format` en la Messages API.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            node = {k: resolve(v) for k, v in node.items() if k != "title"}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node.setdefault("required", list(node.get("properties", {})))
            return node
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
