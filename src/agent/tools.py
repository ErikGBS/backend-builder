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
        "name": "generation_complete",
        "description": (
            "Señala que todos los archivos del proyecto han sido generados. "
            "Llama esta tool SOLO cuando hayas escrito TODOS los archivos: "
            "modelos, servicios, routes, Swagger, requirements/package.json, .env.example y README. "
            "Después de esta tool, el sistema hará git init y abrirá VS Code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Resumen de lo que se generó y cómo levantar el proyecto",
                },
                "run_command": {
                    "type": "string",
                    "description": "Comando para levantar el servidor (ej: uvicorn main:app --reload)",
                },
            },
            "required": ["summary", "run_command"],
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
    # ── Git tools para proyectos existentes ──────────────────────
    {
        "name": "list_files",
        "description": (
            "Lista la estructura de archivos del proyecto existente. "
            "Usa esto al inicio para entender la arquitectura antes de modificar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa desde la raiz del proyecto (ej: src/api)"},
                "depth": {"type": "integer", "description": "Profundidad maxima (default 3)", "default": 3},
            },
            "required": [],
        },
    },
    {
        "name": "git_pull",
        "description": "Hace git pull de la rama main para traer los ultimos cambios antes de crear la rama.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Rama a actualizar (default: main)", "default": "main"},
            },
            "required": [],
        },
    },
    {
        "name": "git_create_branch",
        "description": "Crea una rama nueva desde main y hace checkout. Llama esto antes de modificar archivos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Nombre de la rama (ej: feature/HU-142-cotizaciones)"},
            },
            "required": ["branch_name"],
        },
    },
    {
        "name": "git_push",
        "description": "Hace commit de todos los cambios y pushea la rama a Azure DevOps. Llama esto al finalizar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Nombre de la rama a pushear"},
                "commit_message": {"type": "string", "description": "Mensaje del commit"},
            },
            "required": ["branch_name", "commit_message"],
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


def _list_dir(path: Path, depth: int, current: int = 0) -> list[str]:
    if current >= depth or not path.is_dir():
        return []
    lines = []
    try:
        for item in sorted(path.iterdir()):
            if item.name.startswith('.') or item.name in ('__pycache__', 'node_modules', '.git'):
                continue
            prefix = "  " * current + ("├── " if item.is_file() else "📁 ")
            lines.append(f"{prefix}{item.name}")
            if item.is_dir():
                lines.extend(_list_dir(item, depth, current + 1))
    except PermissionError:
        pass
    return lines


async def execute_tool_at(name: str, inputs: dict, project_path: Path) -> str:
    """Execute tools operating on an EXISTING project at an absolute path."""

    if name == "list_files":
        sub = inputs.get("path", "")
        depth = int(inputs.get("depth", 3))
        target = project_path / sub if sub else project_path
        if not target.exists():
            return f"Ruta no encontrada: {target}"
        lines = _list_dir(target, depth)
        return f"### Estructura de {target.name}/\n" + "\n".join(lines[:100])

    if name == "read_file":
        target = project_path / inputs["path"]
        if not target.exists():
            return f"Archivo no encontrado: {inputs['path']}"
        return f"### {inputs['path']}\n```\n{target.read_text(encoding='utf-8')[:4000]}\n```"

    if name == "write_file":
        target = project_path / inputs["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(inputs["content"], encoding="utf-8")
        logger.info("write_file_at path=%s", target)
        return f"Archivo escrito: {inputs['path']}"

    if name == "create_directory":
        target = project_path / inputs["path"]
        target.mkdir(parents=True, exist_ok=True)
        return f"Carpeta creada: {inputs['path']}"

    if name == "git_pull":
        branch = inputs.get("branch", "main")
        result = subprocess.run(
            f"git checkout {branch} && git pull origin {branch}",
            shell=True, cwd=project_path, capture_output=True, text=True, timeout=60,
        )
        return f"$ git pull origin {branch}\n{result.stdout}{result.stderr}".strip()

    if name == "git_create_branch":
        branch = inputs["branch_name"]
        result = subprocess.run(
            f"git checkout -b {branch}",
            shell=True, cwd=project_path, capture_output=True, text=True, timeout=30,
        )
        logger.info("git_create_branch branch=%s", branch)
        return f"$ git checkout -b {branch}\n{result.stdout}{result.stderr}".strip()

    if name == "git_push":
        branch = inputs["branch_name"]
        msg = inputs.get("commit_message", "feat: changes from backend-builder agent")

        # Configure PAT in remote URL if available
        remote_url = None
        if settings.azure_devops_pat and settings.azure_devops_org:
            # Get current remote URL and inject PAT
            r = subprocess.run("git remote get-url origin", shell=True,
                               cwd=project_path, capture_output=True, text=True)
            current_url = r.stdout.strip()
            if "dev.azure.com" in current_url and "@" not in current_url:
                # Inject PAT: https://dev.azure.com/org/... → https://pat@dev.azure.com/org/...
                remote_url = current_url.replace(
                    "https://", f"https://{settings.azure_devops_pat}@"
                )

        add = subprocess.run("git add .", shell=True, cwd=project_path,
                             capture_output=True, text=True, timeout=30)
        commit = subprocess.run(f'git commit -m "{msg}"', shell=True,
                                cwd=project_path, capture_output=True, text=True, timeout=30)

        push_remote = remote_url or "origin"
        push = subprocess.run(
            f"git push {push_remote} {branch}",
            shell=True, cwd=project_path, capture_output=True, text=True, timeout=60,
        )
        logger.info("git_push branch=%s returncode=%d", branch, push.returncode)
        out = f"git add . → {add.returncode}\ngit commit → {commit.stdout.strip()}\ngit push → {push.stdout}{push.stderr}"
        return out.strip()

    if name == "open_vscode":
        path = inputs.get("path", str(project_path))
        try:
            subprocess.Popen(["code", path])
            return f"VS Code abierto en: {path}"
        except FileNotFoundError:
            return "VS Code no encontrado. Abre manualmente: code " + path

    # Fallback to standard execute_tool for generate_complete, propose_blueprint
    return await execute_tool(name, inputs, project_path.name)
