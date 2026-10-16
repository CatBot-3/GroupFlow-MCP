"""Settings from the environment, with an optional `.env` file in the repo root.

Every value has a safe local default, so `python -m groupflow demo` works with
no configuration. Secrets (API keys) are only ever read here, never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments, no overriding of real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(key: str, default: int) -> int:
    value = env(key)
    return int(value) if value is not None else default


def env_list(key: str, default: str) -> list[str]:
    return [p.strip() for p in (env(key, default) or "").split(",") if p.strip()]


def chat_db_for(db_path: str) -> str:
    """The group chat's own store sits next to the GroupFlow database it belongs to, so a fresh demo database gets a fresh chat."""
    if db_path == ":memory:":
        return ":memory:"
    p = Path(db_path)
    return str(p.with_name(p.stem + ".chat" + (p.suffix or ".db")))


@dataclass(frozen=True)
class ServerSettings:
    host: str = field(default_factory=lambda: env("GROUPFLOW_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: env_int("GROUPFLOW_PORT", 8787))
    db_path: str = field(default_factory=lambda: env("GROUPFLOW_DB", str(REPO_ROOT / "data" / "groupflow.db")))
    demo_admin: bool = field(default_factory=lambda: env("GROUPFLOW_DEMO_ADMIN", "1") != "0")
    admin_key: str = field(default_factory=lambda: env("GROUPFLOW_ADMIN_KEY", "gf_demo_admin"))
    allowed_origins: list[str] = field(default_factory=lambda: env_list(
        "GROUPFLOW_ALLOWED_ORIGINS",
        "http://localhost:8788,http://127.0.0.1:8788,http://localhost:5173,http://127.0.0.1:5173",
    ))
    world_path: str = field(default_factory=lambda: env("GROUPFLOW_WORLD", str(REPO_ROOT / "demo" / "world.yaml")))
    signup: str = field(default_factory=lambda: env("GROUPFLOW_SIGNUP", "open"))  # open | invite | off
    join_url: str = field(default_factory=lambda: env("GROUPFLOW_JOIN_URL", "http://localhost:8788/chat"))
    # json: one JSON body per MCP request (simplest for proxies). sse: streamed responses, which also let the server ask the person a
    # follow-up through the host (MCP elicitation) when the host supports it.
    mcp_responses: str = field(default_factory=lambda: env("GROUPFLOW_MCP_RESPONSES", "json"))


@dataclass(frozen=True)
class LlmSettings:
    provider: str = field(default_factory=lambda: env("AGENT_PROVIDER", "deepseek"))
    deepseek_api_key: str | None = field(default_factory=lambda: env("DEEPSEEK_API_KEY"))
    deepseek_base_url: str = field(default_factory=lambda: env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = field(default_factory=lambda: env("DEEPSEEK_MODEL", "deepseek-flash"))
    deepseek_thinking: str = field(default_factory=lambda: "enabled" if env("DEEPSEEK_THINKING") == "enabled" else "disabled")
    openai_base_url: str | None = field(default_factory=lambda: env("OPENAI_COMPAT_BASE_URL"))
    openai_api_key: str | None = field(default_factory=lambda: env("OPENAI_COMPAT_API_KEY"))
    openai_model: str | None = field(default_factory=lambda: env("OPENAI_COMPAT_MODEL"))
    aws_region: str = field(default_factory=lambda: env("AWS_REGION", "us-east-1"))
    bedrock_model_id: str = field(default_factory=lambda: env("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0"))
    timeout_s: float = field(default_factory=lambda: env_int("LLM_TIMEOUT_MS", 45000) / 1000)
    max_steps: int = field(default_factory=lambda: env_int("LLM_MAX_STEPS", 10))


@dataclass(frozen=True)
class SimSettings:
    host: str = field(default_factory=lambda: env("SIMULATOR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: env_int("SIMULATOR_PORT", 8788))
    mcp_url: str = field(default_factory=lambda: env("GROUPFLOW_MCP_URL", "http://127.0.0.1:8787/mcp"))
    admin_key: str = field(default_factory=lambda: env("GROUPFLOW_ADMIN_KEY", "gf_demo_admin"))
    web_dist: str = field(default_factory=lambda: str(REPO_ROOT / "web" / "dist"))
    timezone: str = field(default_factory=lambda: env("GROUPFLOW_TIMEZONE", "America/Toronto"))
    chat_db: str = field(default_factory=lambda: env("GROUPFLOW_CHAT_DB") or chat_db_for(env("GROUPFLOW_DB", str(REPO_ROOT / "data" / "groupflow.db"))))

    @property
    def server_base(self) -> str:
        url = self.mcp_url
        return url[: -len("/mcp")] if url.rstrip("/").endswith("/mcp") else url.rstrip("/")
