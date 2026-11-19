"""Cliente de Anthropic con salida estructurada (`messages.parse` + Pydantic)."""

from __future__ import annotations

import time

import anthropic
from pydantic import ValidationError

from marioqa.llm.base import LLMError, LLMResult, LLMUsage, T


class AnthropicLLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 2048,
        effort: str = "low",
        timeout_s: float = 30.0,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )

    def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        start = time.perf_counter()
        try:
            resp = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
                output_format=schema,
            )
        except anthropic.APIConnectionError as exc:  # incluye timeouts
            raise LLMError(f"Sin conexión con el LLM: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Límite de uso del LLM alcanzado") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Error del LLM ({exc.status_code}): {exc.message}") from exc
        except ValidationError as exc:
            raise LLMError(f"Salida del LLM fuera de esquema: {exc}") from exc

        latency = time.perf_counter() - start
        if resp.stop_reason == "refusal":
            raise LLMError("El LLM rechazó la petición")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Respuesta truncada por max_tokens")
        if resp.parsed_output is None:
            raise LLMError("El LLM no devolvió una salida estructurada")
        usage = LLMUsage(resp.usage.input_tokens, resp.usage.output_tokens)
        return LLMResult(resp.parsed_output, usage, self.model, latency)
