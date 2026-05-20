"""LLM client factory. Routes through OpenRouter when OPENROUTER_API_KEY is set,
otherwise uses direct Anthropic via ANTHROPIC_API_KEY.

Both paths use the `anthropic` Python SDK — OpenRouter exposes an Anthropic-compatible
/v1/messages endpoint that preserves the request/response shape, including
`cache_control: ephemeral` passthrough for Anthropic models.

API keys can come from:
- the process environment (preferred), or
- a `.env` file in the repo root or the user's $ORC_HOME (auto-loaded; never overrides
  an already-set process env var).

Tests can inject a fake by calling `set_client_factory(lambda: my_fake)`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# The Anthropic SDK appends `/v1/messages` to base_url. OpenRouter exposes its
# Anthropic-compatible endpoint at /api/v1/messages, so the base_url is `/api`.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"

_client: Any = None
_factory: Callable[[], Any] | None = None
_provider: str | None = None  # "openrouter" or "anthropic" once initialized
_dotenv_loaded = False


def _ensure_dotenv() -> None:
    """Load .env from the project root and from $ORC_HOME (~/.orc by default).

    Existing process env vars take precedence (`override=False`), so a shell-exported key
    always wins over the file.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    candidates = []
    repo_env = Path(__file__).resolve().parents[3] / ".env"
    if repo_env.exists():
        candidates.append(repo_env)
    orc_home = os.environ.get("ORC_HOME")
    home_env = (Path(orc_home) if orc_home else Path.home() / ".orc") / ".env"
    if home_env.exists():
        candidates.append(home_env)
    for path in candidates:
        load_dotenv(path, override=False)
    _dotenv_loaded = True


def provider() -> str | None:
    """Return the active provider name, or None if no client has been built yet."""
    return _provider


def get_client() -> Any:
    global _client, _provider
    if _client is not None:
        return _client
    if _factory is not None:
        _client = _factory()
        return _client

    _ensure_dotenv()
    from anthropic import Anthropic

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    explicit = os.environ.get("ORC_PROVIDER", "").strip().lower() or None

    if explicit == "anthropic":
        if not anthropic_key:
            raise RuntimeError("ORC_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        _client = Anthropic(api_key=anthropic_key)
        _provider = "anthropic"
        return _client
    if explicit == "openrouter":
        if not openrouter_key:
            raise RuntimeError("ORC_PROVIDER=openrouter but OPENROUTER_API_KEY is not set.")
        _client = _build_openrouter_client(Anthropic, openrouter_key)
        _provider = "openrouter"
        return _client

    if openrouter_key:
        _client = _build_openrouter_client(Anthropic, openrouter_key)
        _provider = "openrouter"
        return _client
    if anthropic_key:
        _client = Anthropic(api_key=anthropic_key)
        _provider = "anthropic"
        return _client

    raise RuntimeError(
        "No LLM API key found. Set OPENROUTER_API_KEY (recommended) or ANTHROPIC_API_KEY, "
        "or use a test fixture that injects a fake client via set_client_factory()."
    )


def _build_openrouter_client(anthropic_cls: Any, api_key: str) -> Any:
    return anthropic_cls(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://github.com/thormatthiasson/orc",
            "X-Title": "orc",
        },
    )


def messages_create(client: Any, **kwargs: Any) -> Any:
    """Wrapper around `client.messages.create(...)` that adds OpenRouter-specific routing.

    When we're routed through OpenRouter and the target model is Anthropic-family, pin
    the upstream to Anthropic-direct so prompt caching works (other upstreams like
    Bedrock don't expose Anthropic's cache API). For non-Anthropic models (Llama, GPT,
    Qwen, Mistral, etc.) we let OpenRouter pick the upstream — pinning Anthropic would
    route the wrong way and fail.
    """
    if _provider == "openrouter":
        model = kwargs.get("model", "")
        is_anthropic_model = model.startswith("anthropic/") or "claude" in model.lower()
        if is_anthropic_model:
            extra_body = kwargs.get("extra_body") or {}
            if "provider" not in extra_body:
                extra_body = {
                    **extra_body,
                    "provider": {"order": ["Anthropic"], "allow_fallbacks": False},
                }
            kwargs["extra_body"] = extra_body
    return client.messages.create(**kwargs)


def resolve_model_for_provider(model: str) -> str:
    """Adapt a bare model name to the provider's expected form.

    OpenRouter expects 'anthropic/claude-sonnet-4-6'; direct Anthropic expects bare names.
    Already-prefixed names (containing '/') pass through unchanged.
    """
    if "/" in model:
        return model
    if _provider is None:
        # Force a provider decision by attempting client construction; cheap, lazy.
        try:
            get_client()
        except RuntimeError:
            return model
    if _provider == "openrouter":
        return f"anthropic/{model}"
    return model


def set_client_factory(factory: Callable[[], Any] | None) -> None:
    """Test hook. Pass None to clear; pass a callable that returns an Anthropic-shaped client."""
    global _factory, _client, _provider
    _factory = factory
    _client = None
    _provider = None


def reset_client() -> None:
    """Force the next get_client() call to re-construct."""
    global _client, _provider
    _client = None
    _provider = None
