"""Preview tokens: an opt-in mode that makes ``confirm=true`` follow a preview.

By default a client can pass ``confirm=true`` on its first call and never see a
preview (see SECURITY.md, "Preview-Before-Confirm"). With preview tokens
enabled, every preview response carries a ``preview_token`` and the executing
call must send it back:

    UNIFI_REQUIRE_PREVIEW_TOKEN=true             - global
    UNIFI_<SERVER>_REQUIRE_PREVIEW_TOKEN=true    - per-server (wins over global)
    UNIFI_PREVIEW_TOKEN_TTL_SECONDS=300          - token lifetime

A token is single use, expires after the TTL, and is bound to the tool name and
the exact arguments of the preview, so it cannot execute a different request
from the one that was previewed. Tokens live in process memory only and are
lost on restart.

A token proves that the server produced a preview for this request. It does not
prove that a human read it: an agent can request a preview and then execute
with its token. Human approval still belongs to the MCP client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

PREVIEW_TOKEN_ARGUMENT = "preview_token"
DEFAULT_TTL_SECONDS = 300
MIN_TTL_SECONDS = 10
MAX_TTL_SECONDS = 3600
# Bounds memory if a caller requests previews and never executes them.
MAX_OUTSTANDING_TOKENS = 1024

PREVIEW_TOKEN_DESCRIPTION = (
    "Token from this tool's preview response. Required with confirm=true; the arguments must match the preview."
)

_TRUTHY = frozenset(("true", "1", "yes", "on"))
_FALSY = frozenset(("false", "0", "no", "off"))

# Whether this process's server registered its tools with preview tokens on.
# Set by ``create_permissioned_tool``; read by the lazy-mode tool index, whose
# committed manifest is generated with the mode off.
_mode_enabled = False


def set_preview_token_mode(enabled: bool) -> None:
    """Record whether tools in this process are registered with preview tokens on."""
    global _mode_enabled
    _mode_enabled = enabled


def preview_token_mode_enabled() -> bool:
    """Whether tools in this process are registered with preview tokens on."""
    return _mode_enabled


def takes_preview_token(action: str | None, parameter_names: Any) -> bool:
    """Whether a tool takes ``preview_token`` when the mode is on: a mutating tool with ``confirm``."""
    return isinstance(action, str) and action.lower() != "read" and "confirm" in parameter_names


def add_preview_token_property(input_schema: Any) -> Any:
    """Return a copy of *input_schema* that lists the optional ``preview_token`` argument."""
    if not isinstance(input_schema, dict) or not isinstance(input_schema.get("properties"), dict):
        return input_schema
    properties = {
        **input_schema["properties"],
        PREVIEW_TOKEN_ARGUMENT: {"type": "string", "description": PREVIEW_TOKEN_DESCRIPTION},
    }
    return {**input_schema, "properties": properties}


def preview_token_required(server_prefix: str) -> bool:
    """Whether preview tokens are required for *server_prefix*.

    Priority: ``UNIFI_<SERVER>_REQUIRE_PREVIEW_TOKEN`` > ``UNIFI_REQUIRE_PREVIEW_TOKEN``
    > off. An unrecognized value counts as enabled: this is a hardening switch,
    so a typo should fail closed.
    """
    for var in (f"UNIFI_{server_prefix.upper()}_REQUIRE_PREVIEW_TOKEN", "UNIFI_REQUIRE_PREVIEW_TOKEN"):
        value = os.environ.get(var)
        if value is None or not value.strip():
            continue
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
        logger.warning("[preview-token] Unrecognized value for %s, treating as enabled", var)
        return True
    return False


def preview_token_ttl_seconds() -> int:
    """Token lifetime from ``UNIFI_PREVIEW_TOKEN_TTL_SECONDS``, clamped to a sane range."""
    raw = os.environ.get("UNIFI_PREVIEW_TOKEN_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        ttl = int(raw.strip())
    except ValueError:
        logger.warning(
            "[preview-token] UNIFI_PREVIEW_TOKEN_TTL_SECONDS is not an integer, using %ds", DEFAULT_TTL_SECONDS
        )
        return DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, ttl))


def request_digest(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Digest of a tool call, ignoring ``confirm`` and ``preview_token``.

    Callers pass arguments with defaults applied, so leaving an argument out and
    sending its default value produce the same digest.
    """
    payload = {k: v for k, v in arguments.items() if k not in ("confirm", PREVIEW_TOKEN_ARGUMENT)}
    canonical = json.dumps([tool_name, payload], sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PreviewTokenStore:
    """In-memory, single-use, expiring tokens bound to a request digest."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        max_tokens: int = MAX_OUTSTANDING_TOKENS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._max_tokens = max_tokens
        self._clock = clock
        self._tokens: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def issue(self, digest: str) -> str:
        """Mint a token for *digest*. The oldest token is dropped when the store is full."""
        now = self._clock()
        self._prune(now)
        while len(self._tokens) >= self._max_tokens:
            self._tokens.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (digest, now + self.ttl_seconds)
        return token

    def redeem(self, token: str, digest: str) -> str | None:
        """Consume *token*. Returns None when it is valid for *digest*, else the reason it is not.

        A presented token is spent whatever the outcome, so a failed attempt
        needs a fresh preview.
        """
        entry = self._tokens.pop(token, None)
        if entry is None:
            return "it is unknown or was already used"
        bound_digest, expires_at = entry
        if self._clock() >= expires_at:
            return "it has expired"
        if not secrets.compare_digest(bound_digest, digest):
            return "it was issued for different arguments"
        return None

    def __len__(self) -> int:
        return len(self._tokens)

    def _prune(self, now: float) -> None:
        expired = [token for token, (_, expires_at) in self._tokens.items() if now >= expires_at]
        for token in expired:
            del self._tokens[token]


def check_preview_token_settings(server_prefix: str, logger) -> None:
    """Log the preview-token mode at startup, and warn when bypass mode makes it moot."""
    from unifi_core.policy_gate import resolve_permission_mode

    if not preview_token_required(server_prefix):
        return
    if resolve_permission_mode(server_prefix) == "bypass":
        logger.warning(
            "[preview-token] Preview tokens are enabled but %s runs in bypass mode, which executes mutations "
            "without a preview, so no token is required. Unset the bypass permission mode to enforce tokens.",
            server_prefix,
        )
        return
    logger.info(
        "[preview-token] confirm=true requires a preview_token for %s (TTL %ds)",
        server_prefix,
        preview_token_ttl_seconds(),
    )
