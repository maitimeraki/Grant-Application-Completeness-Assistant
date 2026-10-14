from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

import litellm

from app.config import get_settings

logger = logging.getLogger(__name__)

_SESSION_ID = uuid.uuid4().hex

T = TypeVar("T", bound=BaseModel)


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


def complete_json(
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
    max_tokens: int = 4096,
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
            "LLM API key not configured. Set LLM_API_KEY in backend/.env, or "
            "create the assessment with run_llm=false for manual review."
        )

    model = settings.llm_model
    if settings.llm_base_url and "/" not in model:
        model = f"openai/{model}"

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
            response = litellm.completion(
                model=model,
                api_key=settings.llm_api_key or None,
                api_base=settings.llm_base_url or None,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout=180,
                extra_headers={
                    "x-opencode-session": f"gaca-{assessment_id}" if assessment_id else _SESSION_ID,
                    "User-Agent": "grant-completeness-assistant/0.1",
                },
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
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        tokens_prompt = getattr(usage, "prompt_tokens", None)
        tokens_completion = getattr(usage, "completion_tokens", None)

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
                error=str(exc),
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
