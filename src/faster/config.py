"""Configuration models (pydantic) for endpoint and workload."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

DEFAULT_PROMPTS: tuple[str, ...] = ("Explain how transformer attention works, step by step.",)


class EndpointConfig(BaseModel):
    """Target endpoint to benchmark. Any OpenAI-compatible server works."""

    model_config = {"frozen": True}

    base_url: str = Field(
        description="OpenAI-compatible base URL, e.g. http://localhost:11434/v1",
    )
    model: str = Field(description="Model name sent to the server")
    api_key: str | None = Field(default=None, description="Bearer token, if required")
    timeout_s: float = Field(
        default=120.0,
        gt=0,
        description="Per-request timeout; timed-out requests are recorded and isolated",
    )
    extra_body: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra JSON fields merged into the request body",
    )


class WorkloadConfig(BaseModel):
    """Load shape for one benchmark run.

    Exactly one of ``num_requests`` (count mode) or ``duration_s`` (duration
    mode) must be set. See docs/metrics.md section 3 for load semantics.
    """

    model_config = {"frozen": True}

    num_requests: int | None = Field(default=None, ge=1)
    duration_s: float | None = Field(default=None, gt=0)
    concurrency: int = Field(default=1, ge=1, description="Max in-flight requests")
    request_rate: float | None = Field(
        default=None,
        gt=0,
        description="Poisson arrival rate (req/s); None = send as fast as allowed",
    )
    warmup_requests: int = Field(default=1, ge=0)
    prompts: tuple[str, ...] = Field(default=DEFAULT_PROMPTS)
    max_tokens: int = Field(default=256, ge=1)
    temperature: float | None = Field(default=None)

    @model_validator(mode="after")
    def _check_mode(self) -> WorkloadConfig:
        if (self.num_requests is None) == (self.duration_s is None):
            raise ValueError("exactly one of num_requests or duration_s must be set")
        if not self.prompts:
            raise ValueError("prompts must not be empty")
        return self

    def prompt_at(self, index: int) -> str:
        return self.prompts[index % len(self.prompts)]
