"""Named priority model pools with process-local availability state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any, TypeVar

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.modules.providers.models import ModelOption, ProviderModelCatalog, ResolvedChatModel
from agent.modules.providers.pool_errors import PoolFailure, classify_pool_failure

POOL_PROVIDER = "@pool"
POOL_PREFIX = "llm.model_pools."
POOL_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
T = TypeVar("T")
logger = logging.getLogger(__name__)


def normalize_member_provider(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def selected_pool_name(provider: str | None, model: str | None) -> str | None:
    """Resolve logical defaults without constructing a model client."""
    from agent.modules.providers.resolve_chat_model import get_default_llm_settings

    default_provider, default_model = get_default_llm_settings()
    uses_default = not provider or provider.strip().lower() == "default"
    target_provider = default_provider if uses_default else provider.strip()
    if target_provider != POOL_PROVIDER:
        return None
    target_model = (model or "").strip()
    if target_model.lower() in {"", "default", "provider default"}:
        target_model = default_model if uses_default else ""
    if not target_model:
        raise ValueError("A model pool name is required.")
    return target_model


def validate_pool_effort(provider: str | None, effort: str | None) -> None:
    if (provider or "").strip() == POOL_PROVIDER and effort not in (None, "auto"):
        raise ValueError("Model pools only support automatic reasoning effort.")


class PoolMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)

    @field_validator("provider", "model", mode="before")
    @classmethod
    def clean_identifier(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Provider and model must be non-empty strings.")
        return value.strip()

    @field_validator("provider")
    @classmethod
    def reject_nested_pool(cls, value: str) -> str:
        if value.lower() in {POOL_PROVIDER, "default"}:
            raise ValueError("Pool members must reference concrete providers.")
        return value


class ModelPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    members: tuple[PoolMember, ...] = Field(min_length=1)
    retry_count: int = Field(default=2, ge=0, le=5, strict=True)
    cooldown_seconds: int = Field(default=60, gt=0, strict=True)
    quota_cooldown_seconds: int = Field(default=3600, gt=0, strict=True)

    @model_validator(mode="after")
    def unique_members(self):
        identities = [(normalize_member_provider(m.provider), m.model) for m in self.members]
        if len(identities) != len(set(identities)):
            raise ValueError("Pool members must be unique provider/model pairs.")
        return self


class ModelPoolExhausted(RuntimeError):
    code = "model_pool_exhausted"

    def __init__(self, name: str, retry_at: float | None = None) -> None:
        self.pool_name = name
        self.retry_at = datetime.fromtimestamp(retry_at, timezone.utc).isoformat() if retry_at else None
        message = f"No model is available in pool '{name}'."
        if self.retry_at:
            message += f" Try again after {self.retry_at}."
        super().__init__(message)


@dataclass
class MemberState:
    retry_at: float = 0
    probing: bool = False
    error_code: str | None = None
    generation: int = 0


class OutputGuard(BaseCallbackHandler):
    """Observe actual model output, including streams started by LangGraph."""

    run_inline = True

    def __init__(self) -> None:
        self.started = False

    def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        message = getattr(chunk, "message", None)
        if token or getattr(message, "content", None) or getattr(message, "tool_call_chunks", None):
            self.started = True
        if getattr(message, "additional_kwargs", {}).get("reasoning_content"):
            self.started = True


_attempt_context: ContextVar[tuple[str, OutputGuard] | None] = ContextVar("model_pool_attempt", default=None)


def pool_call_config(config: dict | None) -> dict:
    """Attach pool identity and output observation to each physical model call."""
    current = _attempt_context.get()
    result = dict(config or {})
    if current is None:
        return result
    name, guard = current
    callbacks = result.get("callbacks")
    if callbacks is not None and hasattr(callbacks, "add_handler"):
        callbacks = callbacks.copy()
        callbacks.add_handler(guard, inherit=True)
    elif isinstance(callbacks, BaseCallbackHandler):
        callbacks = [callbacks, guard]
    else:
        callbacks = [*(callbacks or []), guard]
    result["callbacks"] = callbacks
    result["metadata"] = {**result.get("metadata", {}), "pool_name": name}
    return result


def should_propagate_pool_error(exc: Exception) -> bool:
    """Keep availability failures out of structured-output parsing fallbacks."""
    current = _attempt_context.get()
    return current is not None and (current[1].started or classify_pool_failure(exc) is not None)


class ModelPoolService:
    def __init__(self, config_service: Any, provider_service: Any, *, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        import time

        self.config = config_service
        self.providers = provider_service
        self._clock = clock or time.time
        self._sleep = sleep
        self._states: dict[tuple[str, str, str], MemberState] = {}
        self._fingerprints: dict[str, str] = {}
        self._lock = RLock()

    def list_configs(self) -> dict[str, ModelPoolConfig]:
        return {key.removeprefix(POOL_PREFIX): ModelPoolConfig.model_validate(value)
                for key, value in self.config.get_all().items()
                if key.startswith(POOL_PREFIX) and "." not in key.removeprefix(POOL_PREFIX)}

    def get(self, name: str) -> ModelPoolConfig:
        value = self.config.get(POOL_PREFIX + name)
        if value is None:
            raise ValueError(f"Unknown model pool: {name}.")
        pool = ModelPoolConfig.model_validate(value)
        # Provider changes and pool edits invalidate old cooldowns and in-flight leases.
        provider_values = {key: value for key, value in self.config.get_all().items()
                           if any(key.startswith(f"llm.providers.{member.provider}.") for member in pool.members)}
        fingerprint = repr((pool, provider_values))
        with self._lock:
            if self._fingerprints.get(name) != fingerprint:
                self.reset(name)
                self._fingerprints[name] = fingerprint
        return pool

    def reset(self, name: str) -> None:
        with self._lock:
            self._states = {key: state for key, state in self._states.items() if key[0] != name}

    def _key(self, name: str, member: PoolMember) -> tuple[str, str, str]:
        return name, normalize_member_provider(member.provider), member.model

    def _enabled(self, member: PoolMember) -> bool:
        try:
            return self.providers.get_provider(member.provider).enabled
        except (KeyError, ValueError, RuntimeError):
            return False

    def _resolve(self, name: str, member: PoolMember, temperature: float | None = None) -> ResolvedChatModel:
        from agent.modules.providers.resolve_chat_model import _resolve_chat_model_info_impl

        return replace(_resolve_chat_model_info_impl(
            self.providers, provider_name=member.provider, model=member.model, disable_retries=True,
            temperature=temperature,
        ), pool_name=name)

    def preview(self, name: str, temperature: float | None = None) -> ResolvedChatModel:
        pool = self.get(name)
        retry_at = []
        for member in pool.members:
            with self._lock:
                state = self._states.get(self._key(name, member))
                if state and (state.probing or state.retry_at > self._clock()):
                    if state.retry_at > self._clock():
                        retry_at.append(state.retry_at)
                    continue
            if not self._enabled(member):
                continue
            try:
                return self._resolve(name, member, temperature)
            except (ValueError, KeyError, RuntimeError):
                continue
        raise ModelPoolExhausted(name, min(retry_at) if retry_at else None)

    def status(self, name: str) -> list[dict[str, Any]]:
        pool = self.get(name)
        with self._lock:
            result = []
            for member in pool.members:
                state = self._states.get(self._key(name, member))
                result.append({**member.model_dump(), "available": self._enabled(member)
                               and not (state and (state.probing or state.retry_at > self._clock())),
                               "error_code": state.error_code if state else None,
                               "retry_at": datetime.fromtimestamp(state.retry_at, timezone.utc).isoformat()
                               if state and state.retry_at > self._clock() else None})
            return result

    async def execute(self, name: str, attempt: Callable[[ResolvedChatModel], Awaitable[T]],
                      *, temperature: float | None = None) -> T:
        pool = self.get(name)
        for member in pool.members:
            if not self._enabled(member):
                continue
            key = self._key(name, member)
            with self._lock:
                state = self._states.setdefault(key, MemberState())
                if state.probing or state.retry_at > self._clock():
                    continue
                # Healthy calls remain concurrent. An expired cooldown grants one probe.
                recovering = bool(state.error_code)
                if recovering:
                    state.probing = True
                generation = state.generation
            try:
                for retry in range(pool.retry_count + 1):
                    guard = OutputGuard()
                    token = _attempt_context.set((name, guard))
                    try:
                        resolved = self._resolve(name, member, temperature)
                        result = await attempt(resolved)
                        with self._lock:
                            if self._states.get(key) is state and state.generation == generation:
                                state.retry_at, state.error_code = 0, None
                        return result
                    except Exception as exc:
                        failure = classify_pool_failure(exc)
                        if failure is None:
                            raise
                        delay = failure.retry_after if failure.retry_after is not None else float(min(2 ** retry, 10))
                        if not guard.started and failure.retryable and retry < pool.retry_count and delay <= 10:
                            await self._sleep(max(0, delay))
                            continue
                        self._fail(key, state, failure, pool)
                        if guard.started:
                            raise
                        break
                    finally:
                        _attempt_context.reset(token)
            finally:
                with self._lock:
                    if self._states.get(key) is state:
                        state.probing = False
        with self._lock:
            deadlines = [s.retry_at for key, s in self._states.items()
                         if key[0] == name and s.retry_at > self._clock()]
        raise ModelPoolExhausted(name, min(deadlines) if deadlines else None)

    def _fail(self, key: tuple[str, str, str], state: MemberState, failure: PoolFailure,
              pool: ModelPoolConfig) -> None:
        cooldown = pool.quota_cooldown_seconds if failure.code == "quota" else pool.cooldown_seconds
        if failure.retry_after is not None:
            cooldown = max(0, failure.retry_after)
        with self._lock:
            if self._states.get(key) is state:
                now = self._clock()
                state.retry_at = max(state.retry_at, now + min(cooldown, 253402300799 - now))
                state.error_code = failure.code
                state.generation += 1
                logger.warning("Model pool %s member %s/%s unavailable: %s", *key, failure.code)

    def catalog(self) -> ProviderModelCatalog | None:
        configs = self.list_configs()
        if not configs:
            return None
        options = []
        for name, pool in sorted(configs.items()):
            from agent.modules.providers.profiles import get_model_profile
            from agent.modules.providers.context_window import resolve_context_window

            windows = []
            for member in pool.members:
                try:
                    provider = self.providers.get_provider(member.provider)
                    profile = get_model_profile(str(provider.provider_type), member.model, provider.model_profiles)
                    windows.append(resolve_context_window(provider.catalog_id or provider.name, member.model, profile))
                except (KeyError, ValueError, RuntimeError):
                    continue
            options.append(ModelOption(id=name, label=name, source="pool", context_window=min(windows) if windows else None,
                                       reasoning_effort_levels=()))
        return ProviderModelCatalog(provider=POOL_PROVIDER, provider_type="model_pool", default_model="",
                                    can_list_models=False, models=tuple(options))


def get_model_pool_service(container: Any = None) -> ModelPoolService:
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).model_pool_service


async def execute_model_call(resolved: ResolvedChatModel, attempt: Callable[[ResolvedChatModel], Awaitable[T]]) -> T:
    """Rebuild the complete call for each pool member; direct calls keep their behavior."""
    name = getattr(resolved, "pool_name", None)
    if name:
        return await get_model_pool_service().execute(name, attempt, temperature=getattr(resolved, "pool_temperature", None))
    # Auxiliary direct calls (for example compaction) are independent of the
    # outer pool attempt and must not mark its output guard or usage identity.
    token = _attempt_context.set(None)
    try:
        return await attempt(resolved)
    finally:
        _attempt_context.reset(token)
