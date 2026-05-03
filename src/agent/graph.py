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
    node_prepare_decision,
    node_setup,
)
from src.agent.state import BuilderState
from src.models.request import BuildRequest, BuildResponse

logger = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_graph_cache: dict = {}


# ── Edge conditions ──────────────────────────────────────────────

def _after_model(state: BuilderState) -> str:
    stop_reason = state.get("_stop_reason", "")
    if stop_reason == "tool_use":
        return "execute_tools"
    # end_turn — check if blueprint was proposed but not yet approved
    if state.get("blueprint") and not state.get("blueprint_approved"):
        return "blueprint_approval"
    # end_turn — blueprint approved, check if generation is done
    if state.get("blueprint_approved"):
        return "setup"
    # end_turn — still in discovery, keep conversing
    return "call_model"


def _after_execute_tools(state: BuilderState) -> str:
    # If propose_blueprint was just called → go to approval
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

    graph.add_node("call_model",          partial(node_call_model, client=client))
    graph.add_node("execute_tools",       node_execute_tools)
    graph.add_node("blueprint_approval",  node_blueprint_approval)
    graph.add_node("prepare_decision",    node_prepare_decision)
    graph.add_node("setup",               node_setup)

    graph.set_entry_point("call_model")

    graph.add_conditional_edges(
        "call_model", _after_model,
        {
            "execute_tools":      "execute_tools",
            "blueprint_approval": "blueprint_approval",
            "setup":              "setup",
            "call_model":         "call_model",
        },
    )
    graph.add_conditional_edges(
        "execute_tools", _after_execute_tools,
        {"blueprint_approval": "blueprint_approval", "call_model": "call_model"},
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
        "messages": [{"role": "user", "content": build_initial_content(request.user_story)}],
        "request": request,
        "project_name": request.project_name or "",
        "framework": "",
        "database": "",
        "auth": "",
        "extra_context": "",
        "blueprint": None,
        "blueprint_approved": False,
        "files_generated": [],
        "generation_round": 0,
        "_stop_reason": "",
        "human_decision": None,
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
