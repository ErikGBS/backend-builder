import logging
import os
import subprocess
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

# Safe commands allowed for execution
_ALLOWED_COMMANDS = {
    "pip install", "pip3 install",
    "npm install", "npm init",
    "git init", "git add", "git commit", "git checkout", "git push",
    "uvicorn", "node",
}

TOOLS = [
    {
        "name": "propose_blueprint",
        "description": (
            "Propone el blueprint del proyecto al desarrollador para su aprobación. "
            "Llama esta tool ANTES de generar cualquier archivo. "
            "El flujo se pausará hasta que el developer apruebe o dé feedback."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "blueprint": {
                    "type": "object",
                    "description": "Blueprint completo con project_name, framework, database, auth, entities, endpoints, folder_structure, tradeoffs",
                    "properties": {
                        "project_name": {"type": "string"},
                        "framework": {"type": "string"},
                        "database": {"type": "string"},
                        "auth": {"type": "string"},
                        "entities": {"type": "array"},
                        "endpoints": {"type": "array"},
                        "folder_structure": {"type": "array"},
                        "tradeoffs": {"type": "string"},
                        "open_questions": {"type": "array"},
                    },
                    "required": ["project_name", "framework", "database", "auth", "entities", "endpoints", "folder_structure", "tradeoffs"],
                },
            },
            "required": ["blueprint"],
        },
    },
    {
        "name": "create_directory",
        "description": "Crea una carpeta en el proyecto. Usa rutas relativas desde la raíz del proyecto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa de la carpeta a crear (ej: src/models)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Escribe o sobreescribe un archivo en el proyecto con el contenido dado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo (ej: src/models/user.py)"},
                "content": {"type": "string", "description": "Contenido completo del archivo"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Lee el contenido de un archivo ya generado para verificar coherencia.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "Ejecuta un comando seguro en el proyecto (pip install, npm install, git init, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando a ejecutar"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "open_vscode",
        "description": "Abre el proyecto en Visual Studio Code al finalizar la generación.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del proyecto a abrir"},
            },
            "required": ["path"],
        },
    },
]


def _resolve_project_path(project_name: str) -> Path:
    workspace = Path(settings.projects_workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace / project_name


def _is_safe_command(command: str) -> bool:
    cmd_lower = command.strip().lower()
    return any(cmd_lower.startswith(allowed) for allowed in _ALLOWED_COMMANDS)


async def execute_tool(name: str, inputs: dict, project_name: str) -> str:
    project_path = _resolve_project_path(project_name)

    if name == "create_directory":
        target = project_path / inputs["path"]
        target.mkdir(parents=True, exist_ok=True)
        logger.info("create_directory path=%s", target)
        return f"Carpeta creada: {inputs['path']}"

    if name == "write_file":
        target = project_path / inputs["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(inputs["content"], encoding="utf-8")
        logger.info("write_file path=%s bytes=%d", target, len(inputs["content"]))
        return f"Archivo escrito: {inputs['path']} ({len(inputs['content'])} chars)"

    if name == "read_file":
        target = project_path / inputs["path"]
        if not target.exists():
            return f"Archivo no encontrado: {inputs['path']}"
        content = target.read_text(encoding="utf-8")
        return f"### {inputs['path']}\n```\n{content[:3000]}\n```"

    if name == "run_command":
        cmd = inputs["command"]
        if not _is_safe_command(cmd):
            logger.warning("run_command blocked unsafe command=%s", cmd)
            return f"Comando bloqueado por seguridad: '{cmd}'. Solo se permiten: pip install, npm install, git, uvicorn."
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=project_path,
                capture_output=True, text=True, timeout=60,
            )
            output = result.stdout[-1000:] if result.stdout else ""
            error = result.stderr[-500:] if result.stderr else ""
            logger.info("run_command cmd=%s returncode=%d", cmd, result.returncode)
            return f"$ {cmd}\n{output}{error}".strip()
        except subprocess.TimeoutExpired:
            return f"Timeout al ejecutar: {cmd}"
        except Exception as exc:
            return f"Error al ejecutar '{cmd}': {exc}"

    if name == "open_vscode":
        path = inputs.get("path", str(project_path))
        try:
            subprocess.Popen(["code", path])
            logger.info("open_vscode path=%s", path)
            return f"VS Code abierto en: {path}"
        except FileNotFoundError:
            return "VS Code no encontrado. Abre manualmente: code " + path

    return f"Tool '{name}' no reconocida."
