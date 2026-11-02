"""Validation for caller-supplied values that become controller request paths.

Managers build controller paths by interpolating a caller's id into a fixed
prefix - ``f"/rest/portforward/{rule_id}"``. aiounifi appends that path to the
controller origin and yarl normalizes dot segments, including percent-encoded
ones, before the request is sent. A ``rule_id`` of ``../firewallrule/<id>``
therefore reaches ``/rest/firewallrule/<id>``: a different resource family,
past a policy gate that only authorized the first one.

Two layers close that:

* :func:`object_id` at each interpolation site, so an id is exactly one path
  segment of the characters controller ids are made of.
* :func:`check_request_path` at ``ConnectionManager.request``, so a path that
  would be re-targeted is refused even when a call site forgets the first.

Neither echoes the rejected value: it is arbitrary caller input, and the
resulting message reaches logs and MCP clients.
"""

import re
from typing import Any

# Mongo ObjectIDs (hex), UUIDs (hex and hyphens), MAC addresses (hex and
# colons) and settings keys (underscores). Dots, slashes, percent signs and
# query/fragment delimiters are deliberately absent.
_OBJECT_ID = re.compile(r"[A-Za-z0-9_:-]+")

# Characters that are never legitimate in the path part of a controller
# request. ``%`` is refused rather than decoded because yarl collapses
# ``%2e%2e`` exactly as it collapses ``..``. ``;`` is refused because the
# Network application runs on Tomcat, which strips path parameters before
# resolving dot segments, so ``..;`` acts as ``..``.
_FORBIDDEN_PATH_CHARS = re.compile(r"[%;\\#\s\x00-\x1f\x7f]")


class InvalidRequestPathError(ValueError):
    """A caller-supplied id or request path would not address the intended resource."""


def object_id(value: Any, kind: str = "object") -> str:
    """Return ``value`` if it is safe to interpolate as one path segment.

    Raises:
        InvalidRequestPathError: If ``value`` is not a non-empty string made of
            letters, digits, ``_``, ``-`` and ``:``.
    """
    if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value):
        raise InvalidRequestPathError(f"Invalid {kind} id")
    return value


def is_safe_request_path(path: Any) -> bool:
    """Whether a URL parser would send ``path`` to the resource it names.

    A query string is allowed - several managers send one - but the path part
    before it may not contain dot segments, percent-encoding, path parameters
    (``;``), backslashes, whitespace or a fragment.
    """
    if not isinstance(path, str) or "#" in path:
        return False
    path_part = path.split("?", 1)[0]
    if _FORBIDDEN_PATH_CHARS.search(path_part):
        return False
    return not any(segment in (".", "..") for segment in path_part.split("/"))


def check_request_path(path: Any) -> None:
    """Refuse a controller request path that :func:`is_safe_request_path` rejects.

    Raises:
        InvalidRequestPathError: If the path is unsafe.
    """
    if not is_safe_request_path(path):
        raise InvalidRequestPathError("Invalid controller request path")
