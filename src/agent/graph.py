from __future__ import annotations
import logging
from dataclasses import dataclass
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from src.agent.nodes import (
    node_blueprint_approval,
    node_call_model,
    node_execute_tools,
    node_nudge_to_tools,
    node_prepare_decision,
    node_read_project,
    node_setup,
)
from src.agent.state import BuilderState
from src.models.request import BuildRequest, BuildResponse

logger = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_graph_cache: dict = {}


# ── Edge conditions ──────────────────────────────────────────────

_MAX_NUDGES = 2


def _after_model(state: BuilderState) -> str:
    stop_reason = state.get("_stop_reason", "")
    if stop_reason == "tool_use":
        return "execute_tools"
    if state.get("nudge_count", 0) < _MAX_NUDGES:
        return "nudge"
    logger.warning("call_model: modelo no usó tools después de %d nudges, terminando", _MAX_NUDGES)
    return "end"


def _after_execute_tools(state: BuilderState) -> str:
    # generation_complete tool was called → go to setup
    if state.get("generation_complete"):
        return "setup"
    # propose_blueprint was called → go to approval
    if state.get("blueprint") and not state.get("blueprint_approved"):
        return "blueprint_approval"
    return "call_model"


def _after_blueprint_approval(state: BuilderState) -> str:
    # Always go through prepare_decision to inject message
    return "prepare_decision"


def _after_prepare_decision(state: BuilderState) -> str:
    return "call_model"


# ── Graph builder ────────────────────────────────────────────────

def build_graph(client):
    key = id(client)
    if key in _graph_cache:
        return _graph_cache[key]

    graph = StateGraph(BuilderState)

    graph.add_node("read_project",        node_read_project)   # Modo B — lee proyecto existente
    graph.add_node("call_model",          partial(node_call_model, client=client))
    graph.add_node("execute_tools",       node_execute_tools)
    graph.add_node("nudge",               node_nudge_to_tools)
    graph.add_node("blueprint_approval",  node_blueprint_approval)
    graph.add_node("prepare_decision",    node_prepare_decision)
    graph.add_node("setup",               node_setup)

    # Entry point: modos existing y multi leen repos primero, nuevo va directo a call_model
    graph.set_conditional_entry_point(
        lambda s: "read_project" if s.get("mode") in ("existing", "multi") else "call_model"
    )
    graph.add_edge("read_project", "call_model")

    graph.add_conditional_edges(
        "call_model", _after_model,
        {
            "execute_tools": "execute_tools",
            "nudge":         "nudge",
            "end":           END,
        },
    )
    graph.add_edge("nudge", "call_model")
    graph.add_conditional_edges(
        "execute_tools", _after_execute_tools,
        {
            "blueprint_approval": "blueprint_approval",
            "call_model":         "call_model",
            "setup":              "setup",    # bug fix: faltaba este mapping
        },
    )
    graph.add_edge("blueprint_approval", "prepare_decision")
    graph.add_edge("prepare_decision",   "call_model")
    graph.add_edge("setup",              END)

    compiled = graph.compile(checkpointer=_checkpointer)
    _graph_cache[key] = compiled
    return compiled


# ── Helpers ──────────────────────────────────────────────────────

def _make_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ── Public runners ───────────────────────────────────────────────

@dataclass
class GraphRunResult:
    thread_id: str
    interrupted: bool
    interrupt_payload: dict | None
    response: BuildResponse | None


async def run_builder(
    request: BuildRequest,
    client,
    thread_id: str,
) -> GraphRunResult:
    from src.agent.parsers import build_initial_content
    from src.core.config import settings
    from pathlib import Path

    graph = build_graph(client)
    config = _make_config(thread_id)

    initial_state: BuilderState = {
        "messages": [{"role": "user", "content": build_initial_content(
            request.user_story, request.refinement_context
        )}],
        "request": request,
        "mode": "new",
        "project_path": "",
        "branch_name": request.branch_name or "",
        "existing_structure": "",
        "project_name": request.project_name or "",
        "framework": "",
        "database": "",
        "auth": "",
        "extra_context": "",
        "blueprint": None,
        "blueprint_approved": False,
        "files_generated": [],
        "files_modified": [],
        "generation_round": 0,
        "generation_complete": False,
        "_stop_reason": "",
        "human_decision": None,
        "nudge_count": 0,
    }

    final = await graph.ainvoke(initial_state, config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    if interrupted:
        return GraphRunResult(
            thread_id=thread_id,
            interrupted=True,
            interrupt_payload=interrupt_payload,
            response=None,
        )

    project_name = request.project_name or "proyecto"
    project_path = str(Path(settings.projects_workspace).expanduser() / project_name)
    files = final.get("files_generated", []) if isinstance(final, dict) else []
    blueprint = final.get("blueprint") if isinstance(final, dict) else None

    return GraphRunResult(
        thread_id=thread_id,
        interrupted=False,
        interrupt_payload=None,
        response=BuildResponse(
            project_path=project_path,
            files_generated=files,
            framework=blueprint.framework if blueprint else "",
            database=blueprint.database if blueprint else "",
            branch_name=request.branch_name,
            message=f"Proyecto generado con {len(files)} archivos. VS Code abierto.",
        ),
    )


async def run_builder_multi(
    user_story: str,
    repos: list,
    refinement_context,
    client,
    thread_id: str,
) -> GraphRunResult:
    """Modo multi-repo — modifica múltiples repositorios para una historia de usuario."""
    from src.models.request import BuildRequest

    # Build registry: {name → {path, branch_name, repo_type}}
    registry = {
        r.name: {
            "path": str(Path(r.project_path).expanduser()),
            "branch_name": r.branch_name,
            "repo_type": r.repo_type,
        }
        for r in repos
    }

    first_repo = repos[0]
    request = BuildRequest(
        user_story=user_story,
        refinement_context=refinement_context,
    )

    graph = build_graph(client)
    config = _make_config(thread_id)

    initial_state: BuilderState = {
        "messages": [{"role": "user", "content": build_initial_content(user_story, refinement_context)}],
        "request": request,
        "mode": "multi",
        "project_path": "",
        "branch_name": first_repo.branch_name,
        "existing_structure": "",
        "repos_registry": registry,
        "active_repo": first_repo.name,
        "project_name": "",
        "framework": "",
        "database": "",
        "auth": "",
        "extra_context": "",
        "blueprint": None,
        "blueprint_approved": False,
        "files_generated": [],
        "files_modified": [],
        "generation_round": 0,
        "generation_complete": False,
        "_stop_reason": "",
        "human_decision": None,
        "nudge_count": 0,
    }

    final = await graph.ainvoke(initial_state, config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    if interrupted:
        return GraphRunResult(
            thread_id=thread_id, interrupted=True,
            interrupt_payload=interrupt_payload, response=None,
        )

    files = final.get("files_generated", []) if isinstance(final, dict) else []
    repo_names = list(registry.keys())

    return GraphRunResult(
        thread_id=thread_id, interrupted=False, interrupt_payload=None,
        response=BuildResponse(
            project_path=", ".join(repo_names),
            files_generated=files,
            framework="multi-repo",
            database="",
            message=f"Modificados {len(repo_names)} repos: {', '.join(repo_names)}. {len(files)} archivos.",
        ),
    )


async def run_builder_existing(
    request: BuildRequest,
    project_path: str,
    branch_name: str,
    client,
    thread_id: str,
) -> GraphRunResult:
    """Modo B — modifica un proyecto existente en disco."""
    graph = build_graph(client)
    config = _make_config(thread_id)

    initial_state: BuilderState = {
        "messages": [{"role": "user", "content": build_initial_content(
            request.user_story, request.refinement_context
        )}],
        "request": request,
        "mode": "existing",
        "project_path": str(Path(project_path).expanduser()),
        "branch_name": branch_name,
        "existing_structure": "",
        "project_name": Path(project_path).name,
        "framework": "",
        "database": "",
        "auth": "",
        "extra_context": "",
        "blueprint": None,
        "blueprint_approved": False,
        "files_generated": [],
        "files_modified": [],
        "generation_round": 0,
        "generation_complete": False,
        "_stop_reason": "",
        "human_decision": None,
        "nudge_count": 0,
    }

    final = await graph.ainvoke(initial_state, config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    if interrupted:
        return GraphRunResult(
            thread_id=thread_id, interrupted=True,
            interrupt_payload=interrupt_payload, response=None,
        )

    files = final.get("files_generated", []) if isinstance(final, dict) else []
    blueprint = final.get("blueprint") if isinstance(final, dict) else None

    return GraphRunResult(
        thread_id=thread_id, interrupted=False, interrupt_payload=None,
        response=BuildResponse(
            project_path=project_path,
            files_generated=files,
            framework=blueprint.framework if blueprint else "",
            database=blueprint.database if blueprint else "",
            branch_name=branch_name,
            message=f"Modificacion completada. {len(files)} archivos. Branch: {branch_name}",
        ),
    )


async def resume_builder(
    thread_id: str,
    decision: str,
    client,
) -> GraphRunResult:
    from src.core.config import settings
    from pathlib import Path

    graph = build_graph(client)
    config = _make_config(thread_id)

    final = await graph.ainvoke(Command(resume=decision), config)

    snapshot = await graph.aget_state(config)
    interrupted = bool(snapshot and snapshot.next)
    interrupt_payload = None
    if interrupted and snapshot.tasks:
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break

    if interrupted:
        return GraphRunResult(
            thread_id=thread_id,
            interrupted=True,
            interrupt_payload=interrupt_payload,
            response=None,
        )

    files = final.get("files_generated", []) if isinstance(final, dict) else []
    blueprint = final.get("blueprint") if isinstance(final, dict) else None
    request = final.get("request") if isinstance(final, dict) else None
    project_name = request.project_name if request else "proyecto"
    project_path = str(Path(settings.projects_workspace).expanduser() / project_name)

    return GraphRunResult(
        thread_id=thread_id,
        interrupted=False,
        interrupt_payload=None,
        response=BuildResponse(
            project_path=project_path,
            files_generated=files,
            framework=blueprint.framework if blueprint else "",
            database=blueprint.database if blueprint else "",
            message=f"Proyecto generado con {len(files)} archivos. VS Code abierto.",
        ),
    )
