"""The reference router: what the public host returns for each request.

The Pages Function (``hosting/dogear-site``) must behave exactly like
``resolve``: ``tools/route_vectors.py`` writes shared test vectors from this
module, and both test suites run them. Publication is decided by the registry alone: an object that
exists in storage but no revision names is never served.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Optional

from . import registry as regmod
from .store import StoreError

NO_STORE = "no-store"
IMMUTABLE = "public, max-age=31536000, immutable"
PAGE = "public, max-age=60"
_DATE_PATH = re.compile(r"^/(\d{4}-\d{2}-\d{2})/?$")
_ISSUE_PATH = re.compile(r"^/(issues/dogear-\d{4}-\d{2}-\d{2}\.[0-9a-f]{16}\.json)$")
_FONT_PATH = re.compile(r"^/(fonts/[A-Za-z0-9-]+\.(?:woff2|txt))$")


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: Optional[bytes] = None  # set for generated responses
    key: Optional[str] = None  # set when the body is a stored object


def _headers(cache: str, ctype: Optional[str] = None, noindex: bool = True, **extra) -> dict:
    h = {"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}
    if ctype:
        h["Content-Type"] = ctype
    if noindex:  # indexing of issue pages is an open editorial decision (PUBLISHING.md §14)
        h["X-Robots-Tag"] = "noindex"
    h.update(extra)
    return h


def _not_found() -> Response:
    return Response(404, _headers(NO_STORE, "text/plain; charset=utf-8"), b"not found\n")


def resolve(store, method: str, path: str, noindex: bool = True) -> Response:
    if method not in ("GET", "HEAD"):
        return Response(405, _headers(NO_STORE, Allow="GET, HEAD"))
    if _FONT_PATH.match(path):
        ctype = "font/woff2" if path.endswith(".woff2") else "text/plain; charset=utf-8"
        return Response(200, _headers(IMMUTABLE, ctype, noindex), key=path[1:])
    try:
        obj = store.get(regmod.REGISTRY_KEY)
        reg = regmod.parse(obj.data) if obj else None
    except (StoreError, regmod.CorruptRegistry):
        return Response(503, _headers(NO_STORE, "text/plain; charset=utf-8"), b"unavailable\n")
    if reg is None:
        return Response(503 if path == "/current.json" else 404,
                        _headers(NO_STORE, "text/plain; charset=utf-8"), b"nothing published\n")

    if path == "/current.json":
        return Response(200, _headers(NO_STORE, "application/json", noindex), regmod.manifest_bytes(reg))
    if path == "/":
        return Response(302, _headers(NO_STORE, Location=f"/{reg['current']}/"))
    m = _ISSUE_PATH.match(path)
    if m:
        # Only each week's ACTIVE revision is addressable (§7): a correction withdraws
        # the old issue URL, and a rollback within a week brings its revision back.
        active = {regmod.active_revision(reg, week)["issueKey"] for week in reg["weeks"]}
        if m.group(1) in active:
            return Response(200, _headers(IMMUTABLE, "application/json", noindex), key=m.group(1))
        return _not_found()
    m = _DATE_PATH.match(path)
    if m:
        try:
            day = dt.date.fromisoformat(m.group(1))
        except ValueError:
            return _not_found()
        monday = (day - dt.timedelta(days=day.weekday())).isoformat()
        if monday not in reg["weeks"]:
            return _not_found()
        if day.isoformat() != monday or not path.endswith("/"):
            return Response(301, _headers(PAGE, Location=f"/{monday}/"))
        rev = regmod.active_revision(reg, monday)
        return Response(200, _headers(PAGE, "text/html; charset=utf-8", noindex), key=rev["webKey"])
    return _not_found()


def respond(store, method: str, path: str, noindex: bool = True) -> Response:
    """The complete response, body included: what the Worker sends. A registered
    object missing from storage is a plain 404; HEAD carries no body."""
    r = resolve(store, method, path, noindex)
    if r.key is not None:
        try:
            obj = store.get(r.key)
        except StoreError:
            return Response(503, _headers(NO_STORE, "text/plain; charset=utf-8"), b"unavailable\n")
        r = Response(r.status, r.headers, obj.data) if obj else _not_found()
    if method == "HEAD":
        r = Response(r.status, r.headers, b"")
    return Response(r.status, r.headers, r.body or b"")


def fetch(store, path: str) -> tuple:
    """(status, body bytes) as a reader would receive them; used to verify what is served."""
    r = respond(store, "GET", path)
    return r.status, r.body
