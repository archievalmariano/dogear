"""Production implementations of the stage checks (tests use stubs).

``http_link_checker`` GETs each external reading link: 404/410, DNS and TLS
failures, and a redirect off HTTPS block; timeouts and 5xx are warnings,
re-checked at promotion. It never
fetches DOGEAR's own edition URL (that page is unpublished until promotion).
``mock_fit_checker`` runs the device-font fit check on only the rendered records.
``http_served`` reads what the public host serves, for the publisher's
served-output verification once storage is R2.
"""

from __future__ import annotations

import json
import secrets
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .staging import LinkReport

USER_AGENT = "DOGEAR link check (+https://dogear.archievalmariano.com)"
MOCK = Path(__file__).resolve().parents[2] / "mock"


class LeftHttps(Exception):
    pass


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while they stay on HTTPS: a reader tapping the link
    must never be sent to plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise LeftHttps(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_LINK_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects)
_LINK_READ = 64 * 1024  # enough to know the page answers; never the whole thing


def _probe(url: str, timeout: float) -> tuple:
    """('ok' | 'block' | 'warn', detail), from a GET as a reader's browser makes it.
    A HEAD answer is not trusted: some hosts answer HEAD 200 and GET 404."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with _LINK_OPENER.open(req, timeout=timeout) as resp:
            resp.read(_LINK_READ)
            return "ok", str(resp.status)
    except LeftHttps as err:
        return "block", f"redirects off HTTPS to {err}"
    except urllib.error.HTTPError as err:
        err.close()
        if err.code in (404, 410):
            return "block", f"HTTP {err.code}"
        return "warn", f"HTTP {err.code}"  # 5xx, 429, 403 and the like: re-checked at promotion
    except urllib.error.URLError as err:
        reason = err.reason
        if isinstance(reason, LeftHttps):
            return "block", f"redirects off HTTPS to {reason}"
        if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
            return "block", f"TLS: {reason}"
        if isinstance(reason, socket.gaierror):
            return "block", f"DNS: {reason}"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "warn", "timeout"
        return "warn", str(reason)
    except (socket.timeout, TimeoutError):
        return "warn", "timeout"


def http_link_checker(timeout: float = 15.0):
    def check(urls: list) -> LinkReport:
        report = LinkReport()
        for url in urls:
            verdict, detail = _probe(url, timeout)
            if verdict == "block":
                report.blocking.append(f"{url}: {detail}")
            elif verdict == "warn":
                report.warnings.append(f"{url}: {detail}")
        return report

    return check


def mock_fit_checker(python: Path = MOCK / ".venv" / "bin" / "python"):
    def check(dataset: bytes, record_ids: list) -> list:
        if not Path(python).exists():
            return [f"fit checker unavailable: {python} (python3 -m venv mock/.venv && mock/.venv/bin/pip install Pillow segno)"]
        raw = json.loads(dataset.decode("utf-8"))
        wanted = set(record_ids)
        subset = dict(raw, records=[r for r in raw["records"] if r.get("id") in wanted])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rendered.json"
            path.write_text(json.dumps(subset), encoding="utf-8")
            run = subprocess.run([str(python), str(MOCK / "render_dogear.py"), "--fitcheck", str(path)],
                                 capture_output=True, text=True)
        if run.returncode != 0:
            tail = (run.stdout + run.stderr).strip().splitlines()[-5:]
            return ["device fit check failed: " + " | ".join(tail)]
        return []

    return check


MAX_SERVED_BYTES = 1024 * 1024


def http_served(base_url: str, timeout: float = 20.0):
    """``served(path) -> (status, body)`` over HTTPS at ``base_url``, as a reader gets
    it. Redirects are not followed (every verification target is served directly).
    A unique query string keeps any cache between us and the Worker out of the
    answer; the Worker routes on the path alone."""
    base = base_url.rstrip("/")
    local = base.startswith(("http://127.0.0.1:", "http://localhost:"))  # tests only
    if not base.startswith("https://") and not local:
        raise ValueError("served-output verification needs an https:// base URL")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def served(path: str) -> tuple:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError(f"bad path {path!r}")
        req = urllib.request.Request(f"{base}{path}?verify={secrets.token_hex(8)}", headers={
            "User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Accept-Encoding": "identity"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(MAX_SERVED_BYTES + 1)  # longer cannot match
        except urllib.error.HTTPError as err:
            err.close()
            return err.code, b""

    return served
