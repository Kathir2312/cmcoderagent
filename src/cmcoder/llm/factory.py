"""Builds the Phase 8 chat model from cmcoder's settings, as cli/factory.py
builds today's provider: the gateway's address (Open WebUI's `/api`), the
key from the environment or the keychain, cmcoder's TLS clients (the OS
certificate store, or `caCertPath`; never unverified) and the model's
profile, which also tells LangChain the context window (the spike's E4:
LangChain doesn't know Qwen's)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models import ModelProfile as LangChainProfile
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel, SecretStr

from ..config.settings import Settings, SettingsError, env_api_key_source
from ..providers.auth import AuthError, load_stored_api_key
from ..providers.openwebui import api_base
from ..providers.profiles import ModelProfile
from ..providers.transport import TransportOptions, build_client, build_sync_client
from .chat_openai import CmcoderChatOpenAI

# A provider with `auth: none`: ChatOpenAI needs some key, the server ignores it.
NO_KEY = "no-key"


def langchain_profile(profile: ModelProfile) -> LangChainProfile:
    """What LangChain's middleware reads about the model (e.g. summarising at
    a fraction of the window needs `max_input_tokens`)."""
    return LangChainProfile(
        max_input_tokens=profile.context_window,
        max_output_tokens=profile.max_output,
        tool_calling=profile.tool_calling != "prompted",
        image_inputs=bool(profile.vision),
        reasoning_output=profile.reasoning != "none",
    )


def chat_model(
    model: str,
    profile: ModelProfile,
    *,
    base_url: str,
    api_key: str | None,
    transport: TransportOptions | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
) -> CmcoderChatOpenAI:
    opts = transport or TransportOptions()
    return CmcoderChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key or NO_KEY),
        streaming=True,
        stream_usage=profile.stream_usage,
        max_retries=max_retries,
        temperature=profile.temperature,
        top_p=profile.top_p,
        max_completion_tokens=profile.max_output,  # sent as the profile names it
        use_responses_api=False,
        http_client=build_sync_client(opts, headers),
        http_async_client=build_client(opts, headers),
        # cmcoder's clients as they are: no transport of langchain-openai's, which
        # would turn off httpx's use of HTTPS_PROXY / NO_PROXY (company proxies).
        http_socket_options=(),
        profile=langchain_profile(profile),
        cm_profile=profile,
    )


def _connection(settings: Settings, provider: str) -> dict[str, Any]:
    """Where and how to reach `provider`, as chat_model and embeddings take it."""
    cfg = settings.providers.get(provider)
    if cfg is None:
        raise SettingsError(
            f"Unknown provider {provider!r}. Configured: {', '.join(settings.providers) or 'none'}"
        )
    key: str | None = None
    if cfg.auth.type != "none":
        key = env_api_key_source()[0] or load_stored_api_key(provider)
        if not key:
            raise AuthError(
                f"No API key for provider '{provider}'. Run `cmcoder login` or set CMCODER_API_KEY."
            )
    return {
        "base_url": api_base(cfg.base_url) if cfg.type == "openwebui" else cfg.base_url,
        "api_key": key,
        "transport": TransportOptions(
            ca_cert_path=cfg.ca_cert_path,
            connect_timeout=cfg.connect_timeout,
            read_timeout=cfg.read_timeout,
        ),
        "headers": cfg.headers,
        "max_retries": cfg.max_retries,
    }


def chat_model_for(
    settings: Settings, provider: str, model: str, profile: ModelProfile
) -> CmcoderChatOpenAI:
    """The chat model for `model` on the configured `provider`."""
    return chat_model(model, profile, **_connection(settings, provider))


def with_fallback(fast: BaseChatModel, main: BaseChatModel) -> Runnable[Any, BaseMessage]:
    """The small fast model for side jobs (titles, summaries), and the main
    model when it fails."""
    return fast.with_fallbacks([main])


def structured(model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
    """Replies as `schema`, through a tool call: that also works on a server
    without a tool parser, whose call comes back as text and is parsed."""
    return model.with_structured_output(schema, method="function_calling")


def embeddings(
    model: str,
    *,
    base_url: str,
    api_key: str | None,
    transport: TransportOptions | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
) -> OpenAIEmbeddings:
    """Embeddings from `<base_url>/embeddings` (LiteLLM's /v1, Open WebUI's /api)."""
    opts = transport or TransportOptions()
    return OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key or NO_KEY),
        # Send the texts, not tiktoken's tokens: tiktoken knows OpenAI's models
        # only, and downloads its tables from the internet the first time.
        check_embedding_ctx_length=False,
        max_retries=max_retries,
        http_client=build_sync_client(opts, headers),
        http_async_client=build_client(opts, headers),
    )


def embeddings_for(settings: Settings, provider: str, model: str) -> OpenAIEmbeddings:
    """The embeddings model `model` on the configured `provider`."""
    return embeddings(model, **_connection(settings, provider))
