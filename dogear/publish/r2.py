"""R2 through its S3-compatible API: the production ``Store`` (Python stdlib only).

    R2Store(account_id, bucket, access_key_id, secret_access_key)

Path-style requests to ``https://<account>.r2.cloudflarestorage.com/<bucket>/<key>``,
signed with AWS Signature Version 4 (region ``auto``, service ``s3``); the payload
hash is signed, so storage rejects a body altered in transit. Conditional writes
use ``If-Match: "<etag>"`` and ``If-None-Match: *``, which R2 supports on PutObject.

The endpoint is always ``<account>.r2.cloudflarestorage.com``, except for a
local test server named explicitly (never with ``publish --target``).

Outcomes follow ``store.py``: a 412 is ``PreconditionFailed`` (definitely not
applied); every other failure of a write, including a timeout or a dropped
connection after the request was sent, is ``StoreError`` and its outcome is
unknown (the publisher reads back). Credentials travel only in the signature;
errors name the status and S3 error code, never a header or a secret.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.client
import os
import re
import socket
import ssl
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

from .store import Obj, PreconditionFailed, StoreError, check_key

ENV = ("DOGEAR_R2_ACCOUNT_ID", "DOGEAR_R2_ACCESS_KEY_ID", "DOGEAR_R2_SECRET_ACCESS_KEY")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_authorization(method: str, path: str, query: str, headers: dict, payload_sha: str, amz_date: str,
                        access_key_id: str, secret: str, region: str, service: str = "s3") -> str:
    """The Authorization header for a request whose ``headers`` (names any case)
    are all signed. ``path`` is already URI-encoded; ``query`` canonical (or "")."""
    canon = {k.lower().strip(): " ".join(str(v).strip().split()) for k, v in headers.items()}
    names = sorted(canon)
    canonical = "\n".join([method, path, query, "".join(f"{n}:{canon[n]}\n" for n in names), ";".join(names),
                           payload_sha])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode("utf-8"), amz_date[:8]), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"


def _error_code(body: bytes) -> str:
    m = re.search(rb"<Code>([A-Za-z]{1,64})</Code>", body[:2048])
    return m.group(1).decode("ascii") if m else "no error code"


class R2Store:
    def __init__(self, account_id: str, bucket: str, access_key_id: str, secret_access_key: str, *,
                 endpoint: Optional[str] = None, timeout: float = 20.0,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)) -> None:
        if not _BUCKET.match(bucket):
            raise ValueError(f"bad bucket name {bucket!r}")
        if endpoint is None:
            if not _ACCOUNT.match(account_id):
                raise ValueError("bad R2 account id")
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        else:  # only a local test server: unpublished uploads never go to another host
            if urlsplit(endpoint).hostname not in LOOPBACK:
                raise ValueError("an R2 endpoint override must be a local test server (127.0.0.1, localhost, ::1)")
        parts = urlsplit(endpoint)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.path not in ("", "/"):
            raise ValueError("bad R2 endpoint")
        if not access_key_id or not secret_access_key:
            raise ValueError("R2 credentials missing")
        self.scheme, self.host, self.port = parts.scheme, parts.hostname, parts.port
        self.netloc = parts.netloc
        self.bucket, self.timeout, self.clock = bucket, timeout, clock
        self._key_id, self._secret = access_key_id, secret_access_key

    @classmethod
    def from_env(cls, bucket: str, environ: Optional[dict] = None, *, allow_endpoint_override: bool = False,
                 **kw) -> "R2Store":
        """Credentials from the environment. DOGEAR_R2_ENDPOINT (a local test server)
        is honoured only when the caller allows it; otherwise its presence is an error,
        never silently ignored."""
        env = os.environ if environ is None else environ
        missing = [name for name in ENV if not env.get(name)]
        if missing:
            raise ValueError("set " + ", ".join(missing))
        endpoint = env.get("DOGEAR_R2_ENDPOINT") or None
        if endpoint is not None and not allow_endpoint_override:
            raise ValueError("DOGEAR_R2_ENDPOINT is set, but this run's store is fixed; unset it")
        return cls(env[ENV[0]], bucket, env[ENV[1]], env[ENV[2]], endpoint=endpoint, **kw)

    def __repr__(self) -> str:  # never the credentials
        return f"R2Store({self.netloc}/{self.bucket})"

    def _request(self, method: str, key: str, body: bytes = b"", extra: Optional[dict] = None) -> tuple:
        path = "/" + quote(self.bucket) + "/" + quote(check_key(key), safe="/-_.~")
        amz_date = self.clock().astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload_sha = hashlib.sha256(body).hexdigest()
        headers = {"host": self.netloc, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha}
        headers.update(extra or {})
        headers["authorization"] = sigv4_authorization(method, path, "", headers, payload_sha, amz_date,
                                                       self._key_id, self._secret, "auto")
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": ssl.create_default_context()} if self.scheme == "https" else {}
        conn = cls(self.host, self.port, timeout=self.timeout, **kwargs)
        try:
            conn.request(method, path, body=body if method == "PUT" else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        except (OSError, http.client.HTTPException, socket.timeout) as err:
            raise StoreError(f"{method} {key}: {type(err).__name__}") from None
        finally:
            conn.close()

    def get(self, key: str) -> Optional[Obj]:
        status, headers, data = self._request("GET", key)
        if status == 404:
            return None
        if status != 200:
            raise StoreError(f"GET {key}: HTTP {status} ({_error_code(data)})")
        length = headers.get("content-length")
        if length is not None and length.isdigit() and int(length) != len(data):
            raise StoreError(f"GET {key}: truncated")
        etag = headers.get("etag", "").strip('"')
        if not etag:
            raise StoreError(f"GET {key}: no ETag")
        return Obj(data, etag)

    def put(self, key: str, data: bytes, *, content_type: str, if_match: Optional[str] = None,
            if_none_match: bool = False) -> str:
        extra = {"content-type": content_type, "content-length": str(len(data))}
        if if_match is not None:
            extra["if-match"] = f'"{if_match}"'
        if if_none_match:
            extra["if-none-match"] = "*"
        status, headers, body = self._request("PUT", key, bytes(data), extra)
        if status == 412:
            raise PreconditionFailed(f"PUT {key}: precondition failed")
        if status != 200:
            raise StoreError(f"PUT {key}: HTTP {status} ({_error_code(body)})")  # outcome unknown
        return headers.get("etag", "").strip('"')
