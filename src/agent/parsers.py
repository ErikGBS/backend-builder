from __future__ import annotations
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


def build_initial_content(user_story: str, refinement_context=None) -> list[dict]:
    """Build the initial user message, optionally including refinement context."""
    text = f"## Historia de usuario\n{user_story}"

    if refinement_context is not None:
        repos = "\n".join(
            f"  - [{r.touch_type}] {r.repo} ({r.project}): {r.reason}"
            for r in refinement_context.repos_impacted
        )
        endpoints = "\n".join(f"  - {ep}" for ep in refinement_context.endpoints_affected)
        schemas = "\n".join(f"  - {s}" for s in refinement_context.schemas_affected)
        db = "\n".join(f"  - {d}" for d in refinement_context.db_changes)

        text += f"""

## Contexto del agente de refinamiento
El agente de análisis ya examinó el codebase. Usa esta información para generar
código que siga los patrones existentes del proyecto:

**Análisis:** {refinement_context.summary}

**Repos impactados:**
{repos or '  (ninguno identificado)'}

**Endpoints afectados:**
{endpoints or '  (ninguno)'}

**Schemas afectados:**
{schemas or '  (ninguno)'}

**Cambios de BD necesarios:**
{db or '  (sin cambios de esquema)'}

**Señales de complejidad:**
{chr(10).join(f'  - {s}' for s in refinement_context.complexity_signals) or '  (ninguna)'}

**INSTRUCCIÓN CRÍTICA:** Genera código que extienda y siga los patrones del proyecto
existente. NO crees abstracciones nuevas si ya existen en los schemas/repos afectados.
Respeta la arquitectura y convenciones del codebase identificado."""

    return [{"type": "text", "text": text}]


def extract_blueprint(tool_input: dict) -> Blueprint | None:
    try:
        return Blueprint.model_validate(tool_input.get("blueprint", tool_input))
    except Exception:
        return None
