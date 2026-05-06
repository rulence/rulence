from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import error, parse
from urllib import request

from ..config import HonchoConfig, MempalaceConfig, MemoryConfig, load_memory_config
from ..mcp_client import McpClient, McpError, McpServerHandle, get_client

@dataclass(frozen=True)
class MemoryItem:
    source: str
    text: str
    score: int = 0
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "text": self.text,
            "score": self.score,
            "metadata": self.metadata or {},
        }


class MemoryProvider:
    name = "memory"

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        raise NotImplementedError

    def health(self) -> dict:
        return {"provider": self.name, "reachable": True, "error": None}


class MemoryProviderError(RuntimeError):
    pass


def _load_memory_config() -> MemoryConfig:
    """Load memory config through the public package hook when patched by callers."""
    public_memory_module = sys.modules.get("rulence.memory")
    public_loader = getattr(public_memory_module, "load_memory_config", None) if public_memory_module else None
    if public_loader is not None and public_loader is not load_memory_config:
        return public_loader()
    return load_memory_config()


class HonchoError(MemoryProviderError):
    pass


class LocalFileMemoryProvider(MemoryProvider):
    name = "local_file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        if not self.path.exists():
            return []
        if self.path.is_file():
            return _rank_chunks(self.path.read_text(encoding="utf-8"), query, str(self.path), limit)

        items: list[MemoryItem] = []
        for path in sorted(self.path.rglob("*")):
            if path.suffix.lower() not in {".txt", ".md", ".json", ".toml", ".yaml", ".yml"}:
                continue
            try:
                items.extend(_rank_chunks(path.read_text(encoding="utf-8"), query, str(path), limit))
            except UnicodeDecodeError:
                continue
        return sorted(items, key=lambda item: item.score, reverse=True)[:limit]


class HttpMemoryProvider(MemoryProvider):
    name = "http"

    def __init__(
        self,
        base_url: str,
        provider_name: str = "http",
        token: str | None = None,
        timeout_seconds: float = 5.0,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = provider_name
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        payload = self._get_json(f"/search?q={parse.quote(query)}&limit={limit}")

        if not isinstance(payload, (list, dict)):
            raise HonchoError(f"{self.name} returned malformed search response from {self.base_url}")
        rows = payload if isinstance(payload, list) else payload.get("results", [])
        if not isinstance(rows, list):
            raise HonchoError(f"{self.name} returned malformed search response from {self.base_url}")
        items = []
        for row in rows[:limit]:
            if isinstance(row, dict):
                text = str(row.get("text", row.get("content", row)))
                score = int(row.get("score", 0))
                metadata = row
            else:
                text = str(row)
                score = 0
                metadata = {}
            items.append(MemoryItem(source=self.name, text=text, score=score, metadata=metadata))
        return items

    def health(self) -> dict:
        try:
            self._open("/health")
            return {"provider": self.name, "reachable": True, "transport": "rest", "url": self.base_url, "error": None}
        except error.HTTPError as exc:
            if exc.code == 404:
                return {"provider": self.name, "reachable": True, "transport": "rest", "url": self.base_url, "error": "health endpoint returned 404"}
            return {"provider": self.name, "reachable": False, "transport": "rest", "url": self.base_url, "error": str(exc)}
        except Exception as exc:
            try:
                self._open("/")
                return {"provider": self.name, "reachable": True, "transport": "rest", "url": self.base_url, "error": None}
            except error.HTTPError as fallback:
                if fallback.code == 404:
                    return {"provider": self.name, "reachable": True, "transport": "rest", "url": self.base_url, "error": "root endpoint returned 404"}
                return {"provider": self.name, "reachable": False, "transport": "rest", "url": self.base_url, "error": str(fallback)}
            except Exception:
                return {"provider": self.name, "reachable": False, "transport": "rest", "url": self.base_url, "error": str(exc)}

    def _get_json(self, path: str) -> object:
        last_error: Exception | None = None
        for _ in range(max(1, self.retries)):
            try:
                with self._open(path) as response:
                    try:
                        return json.loads(response.read().decode("utf-8"))
                    except json.JSONDecodeError as exc:
                        raise HonchoError(f"{self.name} returned non-JSON response from {self.base_url}") from exc
            except Exception as exc:
                last_error = exc
        raise HonchoError(
            f"{self.name} memory request failed at {self.base_url}: {last_error}. "
            f"Verify {self.name.upper()}_URL is correct and the server is running."
        ) from last_error

    def _open(self, path: str):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        req = request.Request(f"{self.base_url}{path}", headers=headers)
        return request.urlopen(req, timeout=self.timeout_seconds)


class HonchoMemoryProvider(HttpMemoryProvider):
    def __init__(self, base_url: str | None = None, config: HonchoConfig | None = None) -> None:
        config = config or _load_memory_config().honcho
        super().__init__(
            base_url or config.url,
            "honcho",
            os.environ.get(config.api_key_env),
            timeout_seconds=config.timeout_seconds,
            retries=config.retries,
        )


class MemPalaceMemoryProvider(HttpMemoryProvider):
    def __init__(self, base_url: str | None = None, config: MempalaceConfig | None = None) -> None:
        config = config or _load_memory_config().mempalace
        super().__init__(
            base_url or config.url or os.environ.get("MEMPALACE_URL", "http://127.0.0.1:8788"),
            "mempalace",
            os.environ.get("MEMPALACE_API_KEY"),
            timeout_seconds=config.timeout_seconds,
        )


class MempalaceMcpProvider(MemoryProvider):
    name = "mempalace"

    def __init__(self, config: MempalaceConfig | None = None, client: McpClient | None = None) -> None:
        self.config = config or _load_memory_config().mempalace
        self._client = client
        self._wings: list[str] | None = None

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        payload = self._call("mempalace_search", {"query": query, "limit": limit})
        return _items_from_mcp_payload(payload, "mempalace")[:limit]

    def list_wings(self) -> list[str]:
        if self._wings is not None:
            return self._wings
        payload = self._call("mempalace_list_wings", {})
        self._wings = _strings_from_mcp_payload(payload)
        return self._wings

    def list_rooms(self, wing: str) -> list[str]:
        payload = self._call("mempalace_list_rooms", {"wing": wing})
        return _strings_from_mcp_payload(payload)

    def traverse(self, wing: str, room: str | None = None) -> list[MemoryItem]:
        arguments = {"wing": wing}
        if room:
            arguments["room"] = room
        payload = self._call("mempalace_traverse", arguments)
        return _items_from_mcp_payload(payload, "mempalace")

    def health(self) -> dict:
        try:
            tools = self._client_for_call().list_tools()
            tool_names = sorted(str(tool.get("name", "")) for tool in tools)
            return {
                "provider": "mempalace",
                "reachable": True,
                "transport": "mcp",
                "tool_count": len(tools),
                "tools": tool_names,
                "error": None,
            }
        except Exception as exc:
            return {
                "provider": "mempalace",
                "reachable": False,
                "transport": "mcp",
                "tool_count": 0,
                "tools": [],
                "error": str(exc),
            }

    def _call(self, tool: str, arguments: dict) -> dict:
        return self._client_for_call().call_tool(tool, arguments)

    def _client_for_call(self) -> McpClient:
        if self._client:
            return self._client
        if not self.config.command:
            raise MemoryProviderError("MemPalace MCP command is not configured")
        handle = McpServerHandle(
            name="mempalace",
            command=self.config.command,
            args=self.config.args,
            env=self.config.env,
            timeout_seconds=self.config.timeout_seconds,
            framing=self.config.framing,
        )
        return get_client(handle)


def retrieve_memory(
    provider: str | None,
    query: str,
    *,
    path: str | None = None,
    url: str | None = None,
    limit: int = 5,
    transport: str = "auto",
    config: MemoryConfig | None = None,
) -> list[MemoryItem]:
    if not provider:
        return []
    config = config or _load_memory_config()
    if provider == "local":
        local_path = path or config.local.path
        if not local_path:
            raise ValueError("--memory-path is required for local memory provider")
        return LocalFileMemoryProvider(local_path).retrieve(query, limit)
    if provider == "honcho":
        return HonchoMemoryProvider(url, config.honcho).retrieve(query, limit)
    if provider == "mempalace":
        if transport == "rest" or url or not config.mempalace.command:
            return MemPalaceMemoryProvider(url, config.mempalace).retrieve(query, limit)
        return MempalaceMcpProvider(config.mempalace).retrieve(query, limit)
    raise ValueError(f"unknown memory provider: {provider}")


def retrieve_combined(
    providers: list[str],
    query: str,
    *,
    paths: dict[str, str] | None = None,
    urls: dict[str, str] | None = None,
    limit: int = 5,
    transport: str = "auto",
    config: MemoryConfig | None = None,
) -> list[MemoryItem]:
    paths = paths or {}
    urls = urls or {}
    config = config or _load_memory_config()
    items: list[MemoryItem] = []
    for provider in providers:
        provider = provider.strip()
        if not provider:
            continue
        try:
            items.extend(
                retrieve_memory(
                    provider,
                    query,
                    path=paths.get(provider),
                    url=urls.get(provider),
                    limit=limit,
                    transport=transport,
                    config=config,
                )
            )
        except Exception:
            continue
    return _sort_memory_items(_dedup_by_text(items, config.priority))[:limit]


def memory_health(provider: str = "all", *, path: str | None = None, config: MemoryConfig | None = None) -> dict:
    config = config or _load_memory_config()
    providers = ("local", "honcho", "mempalace") if provider == "all" else (provider,)
    results: dict[str, dict] = {}
    for name in providers:
        if name == "local":
            configured_path = path or config.local.path
            target = Path(configured_path).expanduser() if configured_path else None
            results[name] = {
                "provider": "local",
                "reachable": bool(target and target.exists()),
                "transport": "file",
                "path": str(target) if target else None,
                "error": None if target and target.exists() else "memory path not configured or not found",
            }
        elif name == "honcho":
            results[name] = HonchoMemoryProvider(config=config.honcho).health()
        elif name == "mempalace":
            if config.mempalace.command:
                results[name] = MempalaceMcpProvider(config.mempalace).health()
            else:
                results[name] = MemPalaceMemoryProvider(config=config.mempalace).health()
        else:
            results[name] = {"provider": name, "reachable": False, "error": f"unknown memory provider: {name}"}
    return results[provider] if provider != "all" else results


def mempalace_wings(config: MemoryConfig | None = None) -> list[str]:
    config = config or _load_memory_config()
    return MempalaceMcpProvider(config.mempalace).list_wings()


def mempalace_rooms(wing: str, config: MemoryConfig | None = None) -> list[str]:
    config = config or _load_memory_config()
    return MempalaceMcpProvider(config.mempalace).list_rooms(wing)


def memory_items_to_context(items: list[MemoryItem]) -> str:
    return "\n\n".join(f"[{item.source} score={item.score}]\n{item.text}" for item in items)


def _items_from_mcp_payload(payload: dict, source_prefix: str) -> list[MemoryItem]:
    structured = payload.get("structuredContent")
    if structured is not None:
        return _items_from_rows(structured, source_prefix)

    items: list[MemoryItem] = []
    for block in payload.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text", ""))
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            items.append(MemoryItem(source=source_prefix, text=text, score=0, metadata={}))
        else:
            items.extend(_items_from_rows(parsed, source_prefix))
    return items


def _items_from_rows(data: object, source_prefix: str) -> list[MemoryItem]:
    if isinstance(data, dict):
        for key in ("results", "items", "drawers", "memories"):
            value = data.get(key)
            if isinstance(value, list):
                return _items_from_rows(value, source_prefix)
        return [MemoryItem(source=source_prefix, text=json.dumps(data, sort_keys=True), score=int(data.get("score", 0) or 0), metadata=data)]
    if not isinstance(data, list):
        return [MemoryItem(source=source_prefix, text=str(data), score=0, metadata={})]

    items: list[MemoryItem] = []
    for row in data:
        if isinstance(row, dict):
            text = str(row.get("text") or row.get("content") or row.get("summary") or row)
            wing = row.get("wing")
            room = row.get("room")
            source = source_prefix
            if wing and room:
                source = f"{source_prefix}:{wing}/{room}"
            elif wing:
                source = f"{source_prefix}:{wing}"
            score = int(float(row.get("score", row.get("relevance", 0)) or 0))
            items.append(MemoryItem(source=source, text=text, score=score, metadata=row))
        else:
            items.append(MemoryItem(source=source_prefix, text=str(row), score=0, metadata={}))
    return items


def _strings_from_mcp_payload(payload: dict) -> list[str]:
    items = _items_from_mcp_payload(payload, "mempalace")
    if items:
        values: list[str] = []
        for item in items:
            try:
                parsed = json.loads(item.text)
            except json.JSONDecodeError:
                values.append(item.text)
            else:
                if isinstance(parsed, dict):
                    values.extend(str(value) for value in parsed.values() if isinstance(value, str))
                elif isinstance(parsed, list):
                    values.extend(str(value) for value in parsed)
        return sorted(dict.fromkeys(value.strip() for value in values if value.strip()))
    return []


def _dedup_by_text(items: list[MemoryItem], priority: tuple[str, ...]) -> list[MemoryItem]:
    priority_index = {name: index for index, name in enumerate(priority)}
    deduped: dict[str, MemoryItem] = {}
    for item in items:
        key = re.sub(r"\s+", " ", item.text.strip().lower())
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None or _memory_priority(item, priority_index) < _memory_priority(existing, priority_index):
            deduped[key] = item
        elif existing is not None and _memory_priority(item, priority_index) == _memory_priority(existing, priority_index) and item.score > existing.score:
            deduped[key] = item
    return list(deduped.values())


def _memory_priority(item: MemoryItem, priority_index: dict[str, int]) -> int:
    provider = item.source.split(":", 1)[0]
    return priority_index.get(provider, 99)


def _sort_memory_items(items: list[MemoryItem]) -> list[MemoryItem]:
    return sorted(items, key=lambda item: item.score, reverse=True)


def _rank_chunks(text: str, query: str, source: str, limit: int) -> list[MemoryItem]:
    terms = set(re.findall(r"[a-zA-Z0-9_'-]+", query.lower()))
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    scored = []
    for index, chunk in enumerate(chunks):
        chunk_terms = set(re.findall(r"[a-zA-Z0-9_'-]+", chunk.lower()))
        score = len(terms & chunk_terms)
        if score:
            scored.append(MemoryItem(source=source, text=chunk, score=score, metadata={"chunk": index}))
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
