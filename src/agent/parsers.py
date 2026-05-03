from src.models.blueprint import Blueprint


def serialize_content(content) -> list[dict]:
    """Convert Anthropic response content blocks to JSON-serializable dicts."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result


def build_initial_content(user_story: str) -> list[dict]:
    return [{"type": "text", "text": f"## Historia de usuario\n{user_story}"}]


def extract_blueprint(tool_input: dict) -> Blueprint | None:
    try:
        return Blueprint.model_validate(tool_input.get("blueprint", tool_input))
    except Exception:
        return None
