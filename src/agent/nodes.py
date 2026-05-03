import logging

from langgraph.types import interrupt

from pathlib import Path

from src.agent.parsers import extract_blueprint, serialize_content
from src.agent.prompt import SYSTEM_PROMPT
from src.agent.state import BuilderState
from src.agent.tools import TOOLS, execute_tool, execute_tool_at
from src.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


async def node_read_project(state: BuilderState) -> dict:
    """Modo B: lee la estructura del proyecto existente e inyecta el contexto en los mensajes."""
    project_path = Path(state.get("project_path", "")).expanduser()
    if not project_path.exists():
        logger.warning("node_read_project path not found: %s", project_path)
        return {"existing_structure": f"Ruta no encontrada: {project_path}"}

    # Lee la estructura de carpetas
    structure = await execute_tool_at("list_files", {"depth": 4}, project_path)

    # Lee archivos clave para entender los patrones del proyecto
    key_patterns = []
    for pattern in ["**/main.py", "**/app.py", "**/config.py", "requirements.txt", "pyproject.toml"]:
        for f in list(project_path.glob(pattern))[:2]:
            rel = str(f.relative_to(project_path))
            content = await execute_tool_at("read_file", {"path": rel}, project_path)
            key_patterns.append(content)

    context_msg = (
        f"MODO B — Proyecto existente detectado en: {project_path}\n\n"
        f"## Estructura del proyecto\n{structure}\n\n"
        f"## Archivos clave\n" + "\n".join(key_patterns[:3]) +
        f"\n\n## Instruccion\nAnaliza la estructura y patrones existentes. "
        f"Luego propone el blueprint de los cambios a realizar (propose_blueprint). "
        f"Recuerda crear la rama con git_create_branch antes de modificar archivos."
    )

    logger.info("node_read_project path=%s", project_path)
    return {
        "existing_structure": structure,
        "messages": state["messages"] + [{"role": "user", "content": [{"type": "text", "text": context_msg}]}],
    }


async def node_call_model(state: BuilderState, client) -> dict:
    """Call Claude. Handles both discovery conversation and code generation."""
    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=_SYSTEM,
        tools=TOOLS,
        messages=state["messages"],
    )
    logger.info("node_call_model stop_reason=%s", response.stop_reason)
    serialized = serialize_content(response.content)
    return {
        "messages": state["messages"] + [{"role": "assistant", "content": serialized}],
        "_stop_reason": response.stop_reason,
    }


async def node_execute_tools(state: BuilderState) -> dict:
    """Execute filesystem tools or propose_blueprint from the last assistant message."""
    last_content = state["messages"][-1]["content"]
    tool_results = []
    files_generated = list(state.get("files_generated", []))
    blueprint = state.get("blueprint")

    for block in last_content:
        if block["type"] != "tool_use":
            continue

        name = block["name"]
        inputs = block["input"]
        logger.info("node_execute_tools tool=%s", name)

        generation_done = state.get("generation_complete", False)

        mode = state.get("mode", "new")
        is_existing = mode == "existing"
        existing_path = Path(state.get("project_path", "")).expanduser() if is_existing else None

        if name == "propose_blueprint":
            blueprint = extract_blueprint(inputs)
            result = (
                f"Blueprint registrado: {blueprint.project_name if blueprint else 'inválido'}. "
                "Esperando aprobación del developer."
            )
        elif name == "generation_complete":
            generation_done = True
            result = f"Generación completada. Archivos: {len(files_generated)}. Resumen: {inputs.get('summary', '')}"
        elif is_existing and existing_path:
            result = await execute_tool_at(name, inputs, existing_path)
            if name == "write_file":
                files_generated.append(inputs.get("path", ""))
        else:
            result = await execute_tool(name, inputs, state["request"].project_name or "proyecto")
            if name == "write_file":
                files_generated.append(inputs.get("path", ""))

        tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})

    return {
        "messages": state["messages"] + [{"role": "user", "content": tool_results}],
        "blueprint": blueprint,
        "files_generated": files_generated,
        "generation_complete": generation_done,
    }


def node_blueprint_approval(state: BuilderState) -> dict:
    """Pause the graph and wait for developer approval of the blueprint."""
    blueprint = state.get("blueprint")

    if blueprint:
        entities_str = "\n".join(
            f"  - {e.name}: {', '.join(e.fields[:3])}{'...' if len(e.fields) > 3 else ''}"
            for e in blueprint.entities
        )
        endpoints_str = "\n".join(
            f"  - [{ep.method}] {ep.path} — {ep.description}"
            for ep in blueprint.endpoints[:5]
        )
    else:
        entities_str = endpoints_str = "(sin blueprint)"

    payload = {
        "message": "Blueprint propuesto. ¿Apruebas o tienes cambios?",
        "project_name": blueprint.project_name if blueprint else "",
        "framework": blueprint.framework if blueprint else "",
        "database": blueprint.database if blueprint else "",
        "entities": entities_str,
        "endpoints": endpoints_str,
        "tradeoffs": blueprint.tradeoffs if blueprint else "",
        "options": {
            "approve": "Aprobado, genera el código",
            "feedback": "Escribe tus cambios y el agente ajusta el blueprint",
        },
    }

    decision = interrupt(payload)
    logger.info("node_blueprint_approval decision=%r", str(decision)[:80])
    return {"human_decision": decision}


def node_prepare_decision(state: BuilderState) -> dict:
    """Inject developer decision into messages before resuming call_model."""
    decision = state.get("human_decision", "approve")

    if str(decision).strip().lower() in ("approve", "aprobado", "si", "sí", "ok", "yes"):
        msg = (
            "El developer aprobó el blueprint. Procede a generar el código completo del proyecto "
            "usando las herramientas disponibles (create_directory, write_file, run_command). "
            "Genera todos los archivos en el orden definido en las reglas críticas."
        )
        return {
            "blueprint_approved": True,
            "messages": state["messages"] + [{"role": "user", "content": [{"type": "text", "text": msg}]}],
        }
    else:
        msg = (
            f"El developer tiene estos cambios al blueprint: {decision}\n\n"
            "Ajusta el blueprint según el feedback y vuelve a proponer con propose_blueprint."
        )
        return {
            "blueprint_approved": False,
            "messages": state["messages"] + [{"role": "user", "content": [{"type": "text", "text": msg}]}],
        }


async def node_setup(state: BuilderState) -> dict:
    """Final setup: git init and open VS Code."""
    from pathlib import Path
    from src.core.config import settings

    project_name = state["request"].project_name or "proyecto"
    full_path = str(Path(settings.projects_workspace).expanduser() / project_name)

    await execute_tool("run_command", {"command": "git init"}, project_name)
    await execute_tool("run_command", {"command": "git add ."}, project_name)
    await execute_tool("run_command", {
        "command": 'git commit -m "feat: initial project generated by backend-builder"'
    }, project_name)
    await execute_tool("open_vscode", {"path": full_path}, project_name)

    logger.info("node_setup done project=%s files=%d path=%s", project_name, len(state.get("files_generated", [])), full_path)
    return {}
