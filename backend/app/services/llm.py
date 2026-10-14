from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings

logger = logging.getLogger(__name__)

_SESSION_ID = uuid.uuid4().hex

T = TypeVar("T", bound=BaseModel)

OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"


class LLMNotConfiguredError(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    pass


def llm_available() -> bool:
    return bool(get_settings().llm_api_key)


def _extract_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMOutputError(f"No JSON object found in model output: {content[:200]!r}")
    return json.loads(text[start : end + 1])


def _log_call(
    *,
    assessment_id: Optional[int],
    stage: str,
    model: str,
    attempt: int,
    success: bool,
    error: Optional[str],
    latency_ms: Optional[int],
    prompt_chars: Optional[int],
    completion_chars: Optional[int],
    tokens_prompt: Optional[int],
    tokens_completion: Optional[int],
) -> None:
    """Persist a structured record of one LLM call. Never raises."""
    try:
        from app.db import SessionLocal
        from app.models import LLMCallLog

        with SessionLocal() as session:
            session.add(
                LLMCallLog(
                    assessment_id=assessment_id,
                    stage=stage or "unknown",
                    model=model,
                    attempt=attempt,
                    success=success,
                    error=(error[:2000] if error else None),
                    latency_ms=latency_ms,
                    prompt_chars=prompt_chars,
                    completion_chars=completion_chars,
                    tokens_prompt=tokens_prompt,
                    tokens_completion=tokens_completion,
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 — logging must never break the pipeline
        logger.exception("failed to persist llm call log")


def _session_headers(assessment_id: Optional[int]) -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "x-opencode-session": f"gaca-{assessment_id}" if assessment_id else _SESSION_ID,
        "User-Agent": "grant-completeness-assistant/0.1",
    }


def _openai_chat(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    assessment_id: Optional[int],
) -> tuple[str, Optional[int], Optional[int]]:
    """Call an OpenAI-compatible /chat/completions endpoint via httpx.

    Used for LLM_BASE_URL gateways and for the OpenAI provider itself —
    avoids importing litellm (~190 MB RSS), which a 512 MB instance cannot
    spare. Returns (content, prompt_tokens, completion_tokens).
    """
    import httpx

    settings = get_settings()
    base = (settings.llm_base_url or OPENAI_DEFAULT_BASE).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=180) as client:
        resp = client.post(f"{base}/chat/completions", headers=_session_headers(assessment_id), json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} from {base}: {resp.text[:500]}")
        data = resp.json()
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected response shape from {base}: {exc}; body: {json.dumps(data)[:500]}")
    usage = data.get("usage") or {}
    return content, usage.get("prompt_tokens"), usage.get("completion_tokens")


def _litellm_chat(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    assessment_id: Optional[int],
) -> tuple[str, Optional[int], Optional[int]]:
    """Native vendor routing (e.g. deepseek/..., anthropic/...) without a
    gateway, via LiteLLM. Imported lazily so its ~190 MB dependency tree is
    never loaded in OpenAI-compatible (gateway) setups.
    """
    import litellm

    try:
        litellm.suppress_debug_info = True
        litellm.telemetry = False
    except Exception:  # noqa: BLE001 — best-effort quieting
        pass
    response = litellm.completion(
        model=model,
        api_key=get_settings().llm_api_key or None,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        timeout=180,
        extra_headers=_session_headers(assessment_id),
    )
    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    return content, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


def _route(model: str) -> tuple[str, bool]:
    """Resolve (model, use_litellm) from LLM_PROVIDER / LLM_BASE_URL.

    - LLM_BASE_URL set (custom OpenAI-compatible gateway) → httpx, any provider label.
    - LLM_PROVIDER openai/empty → httpx against OpenAI (or the gateway).
    - LLM_PROVIDER another vendor without a gateway → lazy LiteLLM native path.
    """
    settings = get_settings()
    provider = (settings.llm_provider or "openai").strip().lower()
    if provider not in ("", "openai") and not settings.llm_base_url:
        if "/" not in model:
            model = f"{provider}/{model}"
        return model, True
    if model.startswith("openai/"):
        model = model[len("openai/") :]
    return model, False


def complete_json(
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
    max_tokens: int = 8192,
    *,
    stage: str = "",
    assessment_id: Optional[int] = None,
) -> T:
    """Call the configured LLM and validate its JSON against `schema`.

    Retries once with a repair prompt on validation failure. Every attempt is
    recorded as a structured LLM-call log row (see the llm_call_logs table).
    """
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMNotConfiguredError(
            "LLM API key not configured. Set LLM_API_KEY in backend/.env or in "
            "the deployment environment, or create the assessment with "
            "run_llm=false for manual review."
        )

    model, use_litellm = _route(settings.llm_model)
    chat = _litellm_chat if use_litellm else _openai_chat

    schema_json = json.dumps(schema.model_json_schema())
    messages = [
        {
            "role": "system",
            "content": (
                f"{system}\n\nRespond with a single JSON object that validates "
                f"against this JSON Schema and nothing else (no prose, no markdown):\n"
                f"{schema_json}"
            ),
        },
        {"role": "user", "content": user},
    ]

    last_error: Exception | None = None
    for attempt in range(1, 3):
        prompt_chars = sum(len(m["content"]) for m in messages)
        started = time.perf_counter()
        try:
            content, tokens_prompt, tokens_completion = chat(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                assessment_id=assessment_id,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _log_call(
                assessment_id=assessment_id,
                stage=stage,
                model=settings.llm_model,
                attempt=attempt,
                success=False,
                error=f"transport error: {exc}",
                latency_ms=latency_ms,
                prompt_chars=prompt_chars,
                completion_chars=None,
                tokens_prompt=None,
                tokens_completion=None,
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            data = _extract_json(content)
            result = schema.model_validate(data)
        except (ValidationError, json.JSONDecodeError, LLMOutputError) as exc:
            last_error = exc
            _log_call(
                assessment_id=assessment_id,
                stage=stage,
                model=settings.llm_model,
                attempt=attempt,
                success=False,
                error=f"{exc} | output snippet: {content[:300]!r}",
                latency_ms=latency_ms,
                prompt_chars=prompt_chars,
                completion_chars=len(content),
                tokens_prompt=tokens_prompt,
                tokens_completion=tokens_completion,
            )
            messages = messages + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous output did not validate against the "
                        f"schema. Error: {exc}\nReturn a corrected JSON object only."
                    ),
                },
            ]
            continue

        _log_call(
            assessment_id=assessment_id,
            stage=stage,
            model=settings.llm_model,
            attempt=attempt,
            success=True,
            error=None,
            latency_ms=latency_ms,
            prompt_chars=prompt_chars,
            completion_chars=len(content),
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )
        return result
    raise LLMOutputError(f"Model output failed schema validation twice: {last_error}")
