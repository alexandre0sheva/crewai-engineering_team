"""``[openrouter]``: how requests to ``openrouter/<vendor>/<model>`` models are routed.

OpenRouter forwards each request to one of several third-party hosts serving the model
(``docs/SAFETY.md``). These settings become the request's ``provider`` object
(https://openrouter.ai/docs/features/provider-routing) and its attribution headers.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Quantization = Literal[
    "int4",
    "int8",
    "fp4",
    "mxfp4",
    "nvfp4",
    "fp6",
    "fp8",
    "mxfp8",
    "fp16",
    "bf16",
    "fp32",
    "unknown",
]
# fp8 or better ("unknown" covers most model makers' own hosts). Provisional until plan T33.
FULL_PRECISION: tuple[Quantization, ...] = ("fp8", "mxfp8", "fp16", "bf16", "fp32", "unknown")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._/-]*")


class OpenRouterSettings(BaseModel):
    """Provider routing and attribution for OpenRouter (``docs/CONFIGURATION.md``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str | None = None  # default: OPENROUTER_BASE_URL, else https://openrouter.ai/api/v1
    # Attribution (off by default): your app's URL and name in OpenRouter's rankings.
    app_url: str | None = None
    app_title: str | None = None
    # Routing: host slugs to try first, the only ones allowed, or ones never used.
    order: list[str] = []
    only: list[str] = []
    ignore: list[str] = []
    allow_fallbacks: bool = True  # other hosts may serve a request when the first ones fail
    require_parameters: bool = True  # only hosts that support every parameter sent (tools)
    data_collection: Literal["allow", "deny"] = "deny"  # deny: no host that stores or trains
    zdr: bool = False  # only zero-data-retention endpoints
    quantizations: list[Quantization] = list(FULL_PRECISION)  # empty: any precision
    sort: Literal["price", "throughput", "latency"] | None = None  # default: OpenRouter's

    @field_validator("order", "only", "ignore")
    @classmethod
    def _slugs(cls, value: list[str]) -> list[str]:
        bad = [slug for slug in value if not _SLUG.fullmatch(slug)]
        if bad:
            raise ValueError(f"{bad} are not host slugs (lowercase, for example 'deepinfra')")
        return value

    @field_validator("base_url", "app_url")
    @classmethod
    def _http(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"https?://\S+$", value):
            raise ValueError("must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def _title_needs_url(self) -> OpenRouterSettings:
        if self.app_title and not self.app_url:
            raise ValueError("openrouter.app_title needs openrouter.app_url (attribution)")
        return self

    def provider_routing(self) -> dict[str, Any]:
        """The request body's ``provider`` object (only the fields that are set)."""

        routing: dict[str, Any] = {
            "allow_fallbacks": self.allow_fallbacks,
            "require_parameters": self.require_parameters,
            "data_collection": self.data_collection,
        }
        for name in ("order", "only", "ignore", "quantizations"):
            if value := getattr(self, name):
                routing[name] = list(value)
        if self.zdr:
            routing["zdr"] = True
        if self.sort:
            routing["sort"] = self.sort
        return routing

    def headers(self) -> dict[str, str]:
        """Attribution headers: none unless ``app_url`` is set (CrewAI's own are replaced)."""

        if not self.app_url:
            return {}
        headers = {"HTTP-Referer": self.app_url}
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        return headers
