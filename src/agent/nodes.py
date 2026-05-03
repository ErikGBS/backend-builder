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


async def _read_one_repo(repo_name: str, repo_info: dict) -> str:
    """Lee estructura y archivos clave de un repo y devuelve el contexto como string."""
    path = Path(repo_info["path"]).expanduser()
    repo_type = repo_info.get("repo_type", "python")
    branch = repo_info.get("branch_name", "feature/cambios")

    if not path.exists():
        return f"### {repo_name}\nRuta no encontrada: {path}\n"

    structure = await execute_tool_at("list_files", {"depth": 3}, path)

    # Archivos clave según tipo de repo
    key_files = {
        "python":            ["requirements.txt", "pyproject.toml", "main.py", "app.py"],
        "dotnet":            ["*.csproj", "Program.cs", "Startup.cs"],
        "functions-python":  ["requirements.txt", "host.json", "local.settings.json"],
        "functions-dotnet":  ["host.json", "local.settings.json", "*.csproj"],
    }.get(repo_type, ["requirements.txt", "main.py"])

    snippets = []
    for pattern in key_files:
        for f in list(path.glob(f"**/{pattern}"))[:1]:
            rel = str(f.relative_to(path))
            content = await execute_tool_at("read_file", {"path": rel}, path)
            snippets.append(content)
            if len(snippets) >= 2:
                break

    return (
        f"### Repo: {repo_name} ({repo_type})\n"
        f"Ruta: {path} | Rama a crear: {branch}\n\n"
        f"**Estructura:**\n{structure}\n\n"
        f"**Archivos clave:**\n" + "\n".join(snippets[:2])
    )


async def node_read_project(state: BuilderState) -> dict:
    """Lee la estructura de proyectos existentes e inyecta el contexto en los mensajes.
    Soporta modo single (mode=existing) y multi-repo (mode=multi).
    """
    mode = state.get("mode", "existing")
    contexts = []

    if mode == "multi":
        registry = state.get("repos_registry", {})
        logger.info("node_read_project multi-repo repos=%s", list(registry.keys()))
        for repo_name, repo_info in registry.items():
            ctx = await _read_one_repo(repo_name, repo_info)
            contexts.append(ctx)
        intro = (
            f"MODO MULTI-REPO — Debes modificar {len(registry)} repositorios para esta historia.\n\n"
            "Para cada repo: usa set_active_repo(repo_name) antes de operar sobre sus archivos.\n"
            "Orden recomendado: git_pull → git_create_branch → modificar archivos → git_push.\n\n"
        )
    else:
        project_path = Path(state.get("project_path", "")).expanduser()
        repo_info = {"path": str(project_path), "repo_type": "python",
                     "branch_name": state.get("branch_name", "")}
        ctx = await _read_one_repo(project_path.name, repo_info)
        contexts.append(ctx)
        intro = "MODO B — Proyecto existente.\n\n"

    azure_functions_note = ""
    registry = state.get("repos_registry", {})
    has_functions = any("function" in v.get("repo_type", "") for v in registry.values())
    if has_functions:
        azure_functions_note = (
            "\n\n**NOTA AZURE FUNCTIONS:**\n"
            "- Estructura: una carpeta por funcion con function.json\n"
            "- Bindings van en function.json (httpTrigger, queueTrigger, timerTrigger)\n"
            "- Comando local: func start (no uvicorn)\n"
            "- Settings locales: local.settings.json (nunca .env)\n"
            "- Para nueva funcion: crear carpeta + __init__.py + function.json\n"
        )

    context_msg = (
        intro +
        "\n\n".join(contexts) +
        azure_functions_note +
        "\n\n**INSTRUCCION:** Analiza todos los repos y propone un blueprint "
        "que cubra los cambios necesarios en todos ellos (propose_blueprint)."
    )

    logger.info("node_read_project mode=%s repos=%d", mode, len(contexts))
    return {
        "existing_structure": "\n\n".join(contexts),
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
        is_existing = mode in ("existing", "multi")

        # Resolve active repo path for existing/multi mode
        active_repo = state.get("active_repo", "")
        registry = state.get("repos_registry", {})
        if mode == "multi" and active_repo and active_repo in registry:
            existing_path = Path(registry[active_repo]["path"]).expanduser()
        elif mode == "existing":
            existing_path = Path(state.get("project_path", "")).expanduser()
        else:
            existing_path = None

        if name == "set_active_repo":
            repo_name = inputs.get("repo_name", "")
            if repo_name in registry:
                active_repo = repo_name
                result = f"Repo activo cambiado a: {repo_name} ({registry[repo_name]['path']})"
                logger.info("set_active_repo repo=%s", repo_name)
            else:
                result = f"Repo '{repo_name}' no encontrado. Disponibles: {list(registry.keys())}"
            tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
            continue

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
                files_generated.append(f"{active_repo or 'repo'}:{inputs.get('path', '')}")
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
