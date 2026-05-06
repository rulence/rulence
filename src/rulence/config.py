from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MempalaceConfig:
    command: str | None = "mempalace-mcp"
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    timeout_seconds: float = 5.0
    default_limit: int = 5
    url: str | None = None
    framing: str = "jsonl"


@dataclass(frozen=True)
class HonchoConfig:
    url: str = "http://localhost:8000"
    api_key_env: str = "HONCHO_API_KEY"
    timeout_seconds: float = 5.0
    retries: int = 2


@dataclass(frozen=True)
class LocalMemoryConfig:
    path: str | None = "~/.hermes/active-checkpoints"


@dataclass(frozen=True)
class MemoryConfig:
    mempalace: MempalaceConfig = MempalaceConfig()
    honcho: HonchoConfig = HonchoConfig()
    local: LocalMemoryConfig = LocalMemoryConfig()
    priority: tuple[str, ...] = ("mempalace", "honcho", "local")


def default_memory_config_path() -> Path:
    configured = os.environ.get("RULENCE_MEMORY_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "memory.toml"


def load_memory_config(path: str | Path | None = None) -> MemoryConfig:
    config_path = Path(path).expanduser() if path else default_memory_config_path()
    data: dict = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    mempalace_data = data.get("mempalace", {})
    honcho_data = data.get("honcho", {})
    local_data = data.get("local", {})
    priority_data = data.get("priority", {})

    mempalace_url = os.environ.get("MEMPALACE_URL") or mempalace_data.get("url")
    mempalace_command = mempalace_data.get("command", "mempalace-mcp")
    if mempalace_url and "command" not in mempalace_data:
        mempalace_command = None

    mempalace = MempalaceConfig(
        command=mempalace_command,
        args=tuple(_expand_path_string(arg) for arg in mempalace_data.get("args", ["--palace", "~/.mempalace/library"])),
        env=_expand_env(mempalace_data.get("env")),
        timeout_seconds=float(mempalace_data.get("timeout_seconds", 5.0)),
        default_limit=int(mempalace_data.get("default_limit", 5)),
        url=mempalace_url,
        framing=str(mempalace_data.get("framing", "jsonl")),
    )
    honcho = HonchoConfig(
        url=os.environ.get("HONCHO_URL") or str(honcho_data.get("url", "http://localhost:8000")),
        api_key_env=str(honcho_data.get("api_key_env", "HONCHO_API_KEY")),
        timeout_seconds=float(honcho_data.get("timeout_seconds", 5.0)),
        retries=int(honcho_data.get("retries", 2)),
    )
    local = LocalMemoryConfig(
        path=os.environ.get("RULENCE_LOCAL_MEMORY_PATH") or local_data.get("path", "~/.hermes/active-checkpoints"),
    )
    priority = tuple(str(item) for item in priority_data.get("order", ["mempalace", "honcho", "local"]))
    return MemoryConfig(mempalace=mempalace, honcho=honcho, local=local, priority=priority)


def default_memory_config_text() -> str:
    return """# Rulence memory backends. Local-first; cloud optional.

[mempalace]
command = "mempalace-mcp"
args = ["--palace", "~/.mempalace/library"]
env = {}
timeout_seconds = 5.0
default_limit = 5
framing = "jsonl"

[honcho]
url = "http://localhost:8000"
api_key_env = "HONCHO_API_KEY"
timeout_seconds = 5.0
retries = 2

[local]
path = "~/.hermes/active-checkpoints"

[priority]
order = ["mempalace", "honcho", "local"]
"""


def write_default_memory_config(path: str | Path | None = None) -> Path | None:
    config_path = Path(path).expanduser() if path else default_memory_config_path()
    if config_path.exists():
        return None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(default_memory_config_text(), encoding="utf-8")
    return config_path


def _expand_path_string(raw: object) -> str:
    value = str(raw)
    return str(Path(value).expanduser()) if value.startswith("~") else value


def _expand_env(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): _expand_path_string(raw) for key, raw in value.items()}
