"""Object storage behind one small interface, with a local backend that always works.

Two backends, one contract
--------------------------
LocalObjectStore writes ordinary files under a root directory. It is the default,
it needs no configuration, and it is the only backend here that has been run.

S3ObjectStore speaks the S3 REST protocol to any S3-compatible endpoint. Requests
are signed inside this module with hashlib and hmac following AWS Signature
Version 4, and the only HTTP client is urllib.request. Nothing here imports
botocore or boto3.

An honest note on the S3 backend
--------------------------------
S3ObjectStore has never been run against a live bucket. The signature
construction, the request shapes and the error mapping are written from the
published protocol. Both signature paths do reproduce the worked examples in the
AWS S3 documentation byte for byte, and the local backend's whole surface is
exercised, but no PUT, HEAD, listing or presigned GET here has ever reached a
real bucket: the code has been reviewed and the signing has been matched, it has
not been exercised against the service. The local backend is the one to use until
somebody has pointed this backend at a bucket and watched those four calls
succeed.

What this module does not do
----------------------------
No retries, no multipart uploads, no object ACLs, no lifecycle rules, no
server-side encryption negotiation. A body larger than MAX_OBJECT_BYTES is
refused rather than streamed in pieces, because a half-written object that looks
stored is worse than a clear refusal.

Keys and paths
--------------
A key is a relative, forward-slash path such as
workspaces/<workspace>/assets/<id>.png. Keys come from build_key, which sanitises
every component, and every backend validates a key again before touching a file
or a URL. A key that escapes the root is either a bug or an attack, and both get
the same answer.

Logging
-------
Nothing in this module writes to a log. Every key, path or URL that appears in an
exception message or in error details has been through studio.redact.scrub or
studio.redact.redact_url first, so a credential that found its way into a key
cannot be echoed back from here.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from .errors import (
    ApiError,
    CONFIGURATION_REQUIRED,
    NOT_FOUND,
    STORAGE_FAILED,
    VALIDATION_FAILED,
)
from .redact import redact_url, scrub
from .util import iso, now_iso, utcnow

__all__ = [
    "MAX_OBJECT_BYTES",
    "REQUEST_TIMEOUT_SECONDS",
    "StoredObject",
    "ObjectStore",
    "LocalObjectStore",
    "S3ObjectStore",
    "store_from_config",
    "build_key",
]

#: Hard ceiling on one object. A generated video is the largest thing the
#: platform stores, and this matches the media and egress ceilings so the three
#: cannot disagree about what "too big" means.
MAX_OBJECT_BYTES = 512 * 1024 * 1024

#: Ceiling on any single storage request. Storage that hangs must not hang a
#: worker, so no request here may wait longer than this.
REQUEST_TIMEOUT_SECONDS = 30.0

#: The one sentence every backend uses for a key that is no longer present. The
#: wording is "gone", not "never existed", because the file was there when the
#: record referencing it was written.
_MISSING_MESSAGE = "This file is not stored here any more."

#: Characters a key component may contain after build_key has run.
_UNSAFE_COMPONENT = re.compile(r"[^a-z0-9._-]+")

#: An extension keeps its dot and nothing else unusual.
_UNSAFE_EXTENSION = re.compile(r"[^a-z0-9.]+")

#: A leading drive letter such as "c:" must never be treated as a key.
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


# ---------------------------------------------------------------------------
# The value type and the interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredObject:
    """What one stored object is, without carrying the bytes.

    The fields are the ones a client actually needs: where it is, how big it is,
    what it is, which backend holds it, and when it was written. The url field is
    filled in only by a backend that can hand out an address; for the local
    backend it stays empty and the caller asks ObjectStore.signed_url instead.
    """

    key: str
    byte_size: int
    content_type: str
    backend: str
    url: str = ""
    etag: str = ""
    created_at: str = ""

    def to_public(self) -> Dict[str, Any]:
        """Every field as a plain dictionary, ready to serialise into a response."""
        return {
            "key": self.key,
            "byte_size": self.byte_size,
            "content_type": self.content_type,
            "backend": self.backend,
            "url": self.url,
            "etag": self.etag,
            "created_at": self.created_at,
        }


class ObjectStore:
    """The storage contract. Every method here raises NotImplementedError.

    The interface is deliberately small and deliberately synchronous. Store,
    fetch, describe, list and remove are the only operations the platform
    performs, and a smaller surface is one that can actually be implemented twice
    correctly.
    """

    @property
    def backend_name(self) -> str:
        """Short identifier for this backend, used in stored rows and responses."""
        raise NotImplementedError("backend_name is not implemented by the base class")

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        """Store data under key and describe what was stored."""
        raise NotImplementedError("put is not implemented by the base class")

    def get(self, key: str) -> bytes:
        """Return the stored bytes for key."""
        raise NotImplementedError("get is not implemented by the base class")

    def open_stream(self, key: str) -> Any:
        """Return a binary file-like object for key; the caller closes it."""
        raise NotImplementedError("open_stream is not implemented by the base class")

    def exists(self, key: str) -> bool:
        """True when something is stored under key."""
        raise NotImplementedError("exists is not implemented by the base class")

    def delete(self, key: str) -> bool:
        """Remove key. Returns True when this call removed something."""
        raise NotImplementedError("delete is not implemented by the base class")

    def stat(self, key: str) -> StoredObject:
        """Describe key without reading it. Raises ApiError(NOT_FOUND) if absent."""
        raise NotImplementedError("stat is not implemented by the base class")

    def signed_url(self, key: str, expires_seconds: int = 900) -> str:
        """A time-limited URL for key."""
        raise NotImplementedError("signed_url is not implemented by the base class")

    def list_prefix(self, prefix: str, limit: int = 200) -> List[StoredObject]:
        """Objects whose key starts with prefix, at most limit of them."""
        raise NotImplementedError("list_prefix is not implemented by the base class")


# ---------------------------------------------------------------------------
# Key construction and validation
# ---------------------------------------------------------------------------


def _is_inside(root: str, target: str) -> bool:
    """True when target is root itself or lives below it."""
    try:
        common = os.path.commonpath([os.path.normcase(root), os.path.normcase(target)])
    except ValueError:
        # Different drives on Windows: nothing is shared, so nothing is inside.
        return False
    return common == os.path.normcase(root)


def _clean_component(value: Any, fallback: str) -> str:
    """One key segment, lowered and reduced to characters safe inside a key."""
    text = "" if value is None else str(value)
    text = _UNSAFE_COMPONENT.sub("-", text.strip().lower()).strip("-.")
    return text or fallback


def _clean_extension(value: Any) -> str:
    """An extension with a leading dot, or the empty string when there is none."""
    text = "" if value is None else str(value).strip().lower()
    text = _UNSAFE_EXTENSION.sub("", text).strip(".")
    return "." + text if text else ""


def build_key(workspace_id: str, kind: str, object_id: str, extension: str) -> str:
    """Build a key shaped workspaces/<ws>/<kind>/<shard>/<id><ext>.

    Every component is lowercased and reduced to [a-z0-9._-], so a workspace name
    or an id cannot introduce a separator, a traversal segment or a control
    character. The two-character shard directory keeps any one directory from
    holding every object a busy workspace ever made.

    Unusable components fall back to fixed words (workspace, objects, item) rather
    than being dropped, so the key always has its full shape and a caller can
    always parse it back.
    """
    workspace = _clean_component(workspace_id, "workspace")
    kind_part = _clean_component(kind, "objects")
    object_part = _clean_component(object_id, "item")
    extension_part = _clean_extension(extension)
    shard = object_part[:2] or "00"
    return "workspaces/%s/%s/%s/%s%s" % (workspace, kind_part, shard, object_part, extension_part)


# ---------------------------------------------------------------------------
# Local disk
# ---------------------------------------------------------------------------


class LocalObjectStore(ObjectStore):
    """Files under one root directory. The default backend and the tested one.

    Writes go to <target>.part and are moved into place with os.replace, so a
    reader never sees a half-written object and a crash mid-write leaves only a
    part file behind.

    Every key is validated before use. A key that is absolute, empty, contains a
    backslash, contains a ".." segment, contains a drive letter, or that resolves
    outside the root is rejected with ApiError(VALIDATION_FAILED) naming the key.
    """

    def __init__(self, root: str) -> None:
        """Create the root if needed and remember its resolved path."""
        if not isinstance(root, str) or not root.strip():
            raise ApiError(
                VALIDATION_FAILED,
                "A local object store needs a root directory.",
                details={"root": ""},
            )
        self._root = os.path.realpath(os.path.abspath(root))
        try:
            os.makedirs(self._root, exist_ok=True)
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The storage directory could not be created.",
                details={"root": scrub(self._root)},
                provider_message=scrub(str(error)),
            ) from error

    @property
    def backend_name(self) -> str:
        """Always "local"."""
        return "local"

    @property
    def root(self) -> str:
        """The absolute, resolved root directory."""
        return self._root

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """A one-line description with the root path scrubbed."""
        return "<LocalObjectStore root=%s>" % scrub(self._root)

    # -- validation ---------------------------------------------------------
    def _reject(self, key: Any, reason: str) -> ApiError:
        """The one validation error shape this backend emits."""
        shown = scrub(key) if isinstance(key, str) else ""
        return ApiError(
            VALIDATION_FAILED,
            "That object key is not usable: %s." % reason,
            details={"key": shown},
        )

    def _checked_path(self, key: str) -> str:
        """The absolute path for key, after proving the key stays inside the root."""
        if not isinstance(key, str) or not key.strip():
            raise self._reject(key, "it was empty")
        if key.startswith("/") or key.startswith("\\") or os.path.isabs(key):
            raise self._reject(key, "it must be relative to the storage root")
        if _DRIVE_LETTER.match(key):
            raise self._reject(key, "it must not start with a drive letter")
        if "\\" in key:
            raise self._reject(key, "it must use forward slashes, not backslashes")
        if ":" in key:
            raise self._reject(key, "it must not contain a colon")
        segments = key.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise self._reject(key, "it must not contain empty, single-dot or double-dot segments")
        target = os.path.realpath(os.path.join(self._root, key))
        if not _is_inside(self._root, target):
            raise self._reject(key, "it resolves outside the storage root")
        return target

    def _missing(self) -> ApiError:
        """The one not-found error this backend emits."""
        return ApiError(NOT_FOUND, _MISSING_MESSAGE)

    # -- operations ---------------------------------------------------------
    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        """Write the body atomically, refusing anything over the size ceiling."""
        target = self._checked_path(key)
        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise self._reject(key, "the body must be bytes")
        if len(data) > MAX_OBJECT_BYTES:
            raise ApiError(
                VALIDATION_FAILED,
                "That file is larger than this deployment will store.",
                details={
                    "key": scrub(key),
                    "byte_size": len(data),
                    "limit": MAX_OBJECT_BYTES,
                },
                hint="Raise the storage limit or store a smaller file.",
            )
        declared = (content_type or "").strip() or "application/octet-stream"
        part = target + ".part"
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(part, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, target)
        except OSError as error:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:  # pragma: no cover - best effort cleanup only
                pass
            raise ApiError(
                STORAGE_FAILED,
                "The file could not be written to storage.",
                details={"key": scrub(key)},
                provider_message=scrub(str(error)),
            ) from error
        return StoredObject(
            key=key,
            byte_size=len(data),
            content_type=declared,
            backend=self.backend_name,
            etag=hashlib.sha256(data).hexdigest(),
            created_at=now_iso(),
        )

    def get(self, key: str) -> bytes:
        """Read the whole object. Raises ApiError(NOT_FOUND) when it is absent."""
        target = self._checked_path(key)
        try:
            with open(target, "rb") as handle:
                return handle.read()
        except FileNotFoundError as error:
            raise self._missing() from error
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The stored file could not be read.",
                details={"key": scrub(key)},
                provider_message=scrub(str(error)),
            ) from error

    def open_stream(self, key: str) -> Any:
        """Open the object for reading. The caller is responsible for closing it."""
        target = self._checked_path(key)
        try:
            return open(target, "rb")
        except FileNotFoundError as error:
            raise self._missing() from error
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The stored file could not be opened.",
                details={"key": scrub(key)},
                provider_message=scrub(str(error)),
            ) from error

    def exists(self, key: str) -> bool:
        """True when a regular file is present at key."""
        target = self._checked_path(key)
        return os.path.isfile(target)

    def delete(self, key: str) -> bool:
        """Remove the object. Returns False when there was nothing to remove."""
        target = self._checked_path(key)
        try:
            os.remove(target)
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The stored file could not be removed.",
                details={"key": scrub(key)},
                provider_message=scrub(str(error)),
            ) from error

    def stat(self, key: str) -> StoredObject:
        """Describe the file on disk. Raises ApiError(NOT_FOUND) when it is absent."""
        target = self._checked_path(key)
        try:
            info = os.stat(target)
        except FileNotFoundError as error:
            raise self._missing() from error
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The stored file could not be inspected.",
                details={"key": scrub(key)},
                provider_message=scrub(str(error)),
            ) from error
        return StoredObject(
            key=key,
            byte_size=info.st_size,
            content_type=mimetypes.guess_type(target)[0] or "application/octet-stream",
            backend=self.backend_name,
            created_at=iso(datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)),
        )

    def signed_url(self, key: str, expires_seconds: int = 900) -> str:
        """A file:// URL for an object on this server's own disk.

        A local file URL is only meaningful to the server process that holds the
        path. It cannot be handed to a browser, and expires_seconds is accepted
        and ignored so the method matches the interface. Configuring an
        S3-compatible bucket is what gives real, time-limited signed URLs.
        """
        target = self._checked_path(key)
        if not os.path.isfile(target):
            raise self._missing()
        return "file://" + target

    def list_prefix(self, prefix: str, limit: int = 200) -> List[StoredObject]:
        """Walk the root and return matching objects, sorted by key and capped."""
        wanted = prefix if isinstance(prefix, str) else ""
        wanted = wanted.replace("\\", "/").lstrip("/")
        cap = max(0, int(limit))
        if cap == 0:
            return []
        found: List[Tuple[str, str]] = []
        for directory, _subdirs, filenames in os.walk(self._root):
            for filename in filenames:
                if filename.endswith(".part"):
                    continue
                absolute = os.path.join(directory, filename)
                relative = os.path.relpath(absolute, self._root).replace(os.sep, "/")
                if wanted and not relative.startswith(wanted):
                    continue
                found.append((relative, absolute))
        found.sort(key=lambda pair: pair[0])
        results: List[StoredObject] = []
        for relative, absolute in found[:cap]:
            try:
                info = os.stat(absolute)
            except OSError:
                continue
            results.append(
                StoredObject(
                    key=relative,
                    byte_size=info.st_size,
                    content_type=mimetypes.guess_type(absolute)[0] or "application/octet-stream",
                    backend=self.backend_name,
                    created_at=iso(datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)),
                )
            )
        return results


# ---------------------------------------------------------------------------
# S3-compatible
# ---------------------------------------------------------------------------

#: The only signature algorithm this module implements.
_SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"

#: Payload hash marker used in a presigned URL, where the body is not signed.
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"

#: The longest a presigned URL may live: seven days, the protocol maximum.
_MAX_PRESIGN_SECONDS = 604800


def _payload_hash(data: Optional[bytes]) -> str:
    """SHA-256 of the body, hex encoded. An absent body hashes as empty."""
    return hashlib.sha256(data or b"").hexdigest()


def _hmac_sha256(key: bytes, message: str) -> bytes:
    """One HMAC-SHA256 step of the signing-key derivation."""
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _encode_path(path: str) -> str:
    """Percent-encode a path the way SigV4 canonicalisation expects."""
    return urllib.parse.quote(path or "/", safe="/-_.~")


def _encode_query(pairs: List[Tuple[str, str]]) -> str:
    """Sort and percent-encode query parameters into a canonical query string."""
    encoded = sorted(
        (
            urllib.parse.quote(str(name), safe="-_.~"),
            urllib.parse.quote(str(value), safe="-_.~"),
        )
        for name, value in pairs
    )
    return "&".join("%s=%s" % (name, value) for name, value in encoded)


def _canonical_headers(headers: Dict[str, str]) -> Tuple[str, str]:
    """The canonical-headers block and the signed-headers list for a request."""
    lowered = {
        str(name).strip().lower(): " ".join(str(value).split())
        for name, value in headers.items()
    }
    names = sorted(lowered)
    block = "".join("%s:%s\n" % (name, lowered[name]) for name in names)
    return block, ";".join(names)


def _xml_text(element: Any, local_name: str) -> str:
    """The text of the first child whose tag ends with local_name, or an empty string."""
    for child in element:
        if str(child.tag).endswith(local_name):
            return (child.text or "").strip()
    return ""


class S3ObjectStore(ObjectStore):
    """An S3-compatible bucket, signed in this module rather than through botocore.

    Requests use path-style addressing by default (<endpoint>/<bucket>/<key>),
    which is what every S3-compatible server except AWS itself expects. Pass
    path_style=False for virtual-hosted addressing (<bucket>.<endpoint host>/<key>).

    Every request carries a timeout of at most REQUEST_TIMEOUT_SECONDS. The secret
    key is never placed in a URL, a log line or an exception message; it exists
    only inside the signature computation.

    This class is written but has never been run against a live bucket, so treat
    it as reviewed code rather than exercised code.
    """

    def __init__(
        self,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        verify_tls: bool = True,
        *,
        path_style: bool = True,
    ) -> None:
        """Validate the settings and remember them. No network call happens here."""
        missing = [
            name
            for name, value in (
                ("endpoint", endpoint),
                ("bucket", bucket),
                ("access_key", access_key),
                ("secret_key", secret_key),
            )
            if not (isinstance(value, str) and value.strip())
        ]
        if missing:
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "The S3-compatible storage backend is missing settings.",
                details={"missing": missing},
                hint="Set the storage endpoint, bucket, access key and secret key.",
            )
        raw = endpoint.strip().rstrip("/")
        if "://" not in raw:
            raw = "https://" + raw
        parts = urllib.parse.urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ApiError(
                VALIDATION_FAILED,
                "The storage endpoint is not a usable URL.",
                details={"endpoint": scrub(endpoint)},
            )
        self._scheme = parts.scheme
        self._host = parts.netloc
        self._base_path = parts.path.rstrip("/")
        self._region = (region or "us-east-1").strip() or "us-east-1"
        self._bucket = bucket.strip()
        self._access_key = access_key.strip()
        self._secret_key = secret_key.strip()
        self._path_style = bool(path_style)
        self._verify_tls = bool(verify_tls)
        self._timeout = min(float(REQUEST_TIMEOUT_SECONDS), 30.0)

    @property
    def backend_name(self) -> str:
        """Always "s3"."""
        return "s3"

    @property
    def endpoint(self) -> str:
        """The endpoint as configured, with no credentials in it."""
        return "%s://%s%s" % (self._scheme, self._host, self._base_path)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """A one-line description with bucket and endpoint scrubbed."""
        return "<S3ObjectStore bucket=%s endpoint=%s>" % (
            scrub(self._bucket),
            scrub(self.endpoint),
        )

    # -- addressing ---------------------------------------------------------
    def _object_path(self, key: str) -> str:
        """The unencoded path of an object, in this store's addressing style."""
        clean = (key or "").lstrip("/")
        if not clean:
            raise ApiError(
                VALIDATION_FAILED,
                "An object key was empty.",
                details={"key": ""},
            )
        if ".." in clean.split("/"):
            raise ApiError(
                VALIDATION_FAILED,
                "That object key is not usable: it must not contain double-dot segments.",
                details={"key": scrub(key)},
            )
        if self._path_style:
            return "%s/%s/%s" % (self._base_path, self._bucket, clean)
        return "%s/%s" % (self._base_path, clean)

    def _bucket_path(self) -> str:
        """The unencoded path of the bucket itself, for listing."""
        if self._path_style:
            return "%s/%s/" % (self._base_path, self._bucket)
        return self._base_path + "/"

    def _host_for(self, key: str) -> str:
        """The Host header value for a request about key."""
        if self._path_style:
            return self._host
        return "%s.%s" % (self._bucket, self._host)

    def _url_for(self, key: str, canonical_query: str = "") -> str:
        """The full URL for an object, with an optional canonical query string."""
        url = "%s://%s%s" % (
            self._scheme,
            self._host_for(key),
            _encode_path(self._object_path(key)),
        )
        return url + ("?" + canonical_query if canonical_query else "")

    # -- signing ------------------------------------------------------------
    def _timestamps(self) -> Tuple[str, str]:
        """The x-amz-date value and the credential date it belongs to."""
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        return stamp, stamp[:8]

    def _scope(self, date_stamp: str) -> str:
        """The credential scope string for the configured region."""
        return "%s/%s/s3/aws4_request" % (date_stamp, self._region)

    def _signature(
        self,
        method: str,
        canonical_uri: str,
        canonical_query: str,
        canonical_headers: str,
        signed_headers: str,
        payload_hash: str,
        amz_date: str,
        date_stamp: str,
        scope: str,
    ) -> str:
        """The hex signature for one canonical request.

        This is the SigV4 string-to-sign built from the canonical request, hashed
        and then HMAC'd with the key derived as
        AWS4+secret -> date -> region -> "s3" -> "aws4_request".
        """
        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        string_to_sign = "\n".join(
            [
                _SIGV4_ALGORITHM,
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key = _hmac_sha256(("AWS4" + self._secret_key).encode("utf-8"), date_stamp)
        key = _hmac_sha256(key, self._region)
        key = _hmac_sha256(key, "s3")
        key = _hmac_sha256(key, "aws4_request")
        return hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    def _signed_headers(
        self,
        method: str,
        url: str,
        body: Optional[bytes],
        extra: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        """Headers carrying a fresh SigV4 Authorization for one request."""
        parts = urllib.parse.urlsplit(url)
        amz_date, date_stamp = self._timestamps()
        payload = _payload_hash(body)
        headers: Dict[str, str] = {
            "host": parts.netloc,
            "x-amz-content-sha256": payload,
            "x-amz-date": amz_date,
        }
        for name, value in (extra or {}).items():
            headers[name] = value
        block, signed_names = _canonical_headers(headers)
        scope = self._scope(date_stamp)
        signature = self._signature(
            method,
            _encode_path(parts.path or "/"),
            _encode_query(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)),
            block,
            signed_names,
            payload,
            amz_date,
            date_stamp,
            scope,
        )
        headers["Authorization"] = "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
            _SIGV4_ALGORITHM,
            self._access_key,
            scope,
            signed_names,
            signature,
        )
        return headers

    # -- transport ----------------------------------------------------------
    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        """A verifying context by default, an unverified one only when asked."""
        if self._verify_tls:
            return ssl.create_default_context()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _error_for(self, status: int, action: str, target: str, body: bytes = b"") -> ApiError:
        """The single place an HTTP status becomes an ApiError.

        Every failure the backend reports passes through here, so the wording for
        "the bucket rejected this credential" exists exactly once. The target is
        redacted by the caller and the response body is scrubbed before it is
        quoted; the secret key never appears in any of these messages because it
        is never part of a request.
        """
        excerpt = scrub(body[:400].decode("utf-8", "replace")).strip() if body else ""
        if status in (401, 403):
            return ApiError(
                STORAGE_FAILED,
                "The bucket rejected this credential.",
                details={"status": status, "action": action, "target": target},
                hint=(
                    "The bucket credentials were rejected, or this key is not "
                    "allowed to perform this action on the bucket. Check the "
                    "access key, the secret key and the bucket policy."
                ),
                provider_message=excerpt or None,
            )
        if status == 404:
            return ApiError(
                NOT_FOUND,
                _MISSING_MESSAGE,
                details={"status": status, "action": action, "target": target},
            )
        if 500 <= status < 600:
            return ApiError(
                STORAGE_FAILED,
                "The storage service reported a server error.",
                details={"status": status, "action": action, "target": target},
                hint="Retry the request. If it keeps failing, the bucket or its endpoint is unhealthy.",
                provider_message=excerpt or None,
            )
        return ApiError(
            STORAGE_FAILED,
            "The storage service refused the request.",
            details={"status": status, "action": action, "target": target},
            provider_message=excerpt or None,
        )

    def _open(self, request: urllib.request.Request) -> Any:
        """Send one request, mapping every transport failure onto an ApiError."""
        context = self._ssl_context() if self._scheme == "https" else None
        method = request.get_method()
        # redact_url strips X-Amz-Signature, so a presigned URL never reaches an
        # exception payload with its credential intact.
        target = redact_url(request.get_full_url())
        try:
            return urllib.request.urlopen(request, timeout=self._timeout, context=context)
        except urllib.error.HTTPError as error:
            body = b""
            try:
                body = error.read()
            except Exception:  # pragma: no cover - a broken stream is not fatal here
                body = b""
            raise self._error_for(error.code, method, target, body) from error
        except urllib.error.URLError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The storage endpoint could not be reached.",
                details={"endpoint": scrub(self.endpoint), "action": method},
                provider_message=scrub(str(error.reason)) if error.reason else None,
            ) from error
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The storage request failed.",
                details={"endpoint": scrub(self.endpoint), "action": method},
                provider_message=scrub(str(error)),
            ) from error

    def _request(self, method: str, url: str, body: Optional[bytes], extra: Dict[str, str]):
        """Build, sign and send one request, returning the open response."""
        headers = self._signed_headers(method, url, body, extra)
        request = urllib.request.Request(url, data=body, method=method)
        for name, value in headers.items():
            if name.lower() == "host":
                # urllib sets Host from the URL; sending it again would duplicate it.
                continue
            request.add_header(name, value)
        return self._open(request)

    def _send(
        self,
        method: str,
        key: str,
        *,
        body: Optional[bytes] = None,
        content_type: str = "",
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Perform a signed object request and return status, headers and body."""
        url = self._url_for(key)
        extra: Dict[str, str] = {}
        if content_type:
            extra["content-type"] = content_type
        response = self._request(method, url, body, extra)
        try:
            status = response.getcode()
            received = {str(name): str(value) for name, value in response.info().items()}
            payload = b"" if method == "HEAD" else response.read()
        finally:
            response.close()
        return status, received, payload

    def _header(self, headers: Dict[str, str], name: str) -> str:
        """A header value looked up case-insensitively, or an empty string."""
        wanted = name.lower()
        for header_name, value in headers.items():
            if header_name.lower() == wanted:
                return value
        return ""

    # -- operations ---------------------------------------------------------
    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        """PUT the body with its content type and describe the stored object."""
        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise ApiError(
                VALIDATION_FAILED,
                "The body must be bytes.",
                details={"key": scrub(key)},
            )
        if len(data) > MAX_OBJECT_BYTES:
            raise ApiError(
                VALIDATION_FAILED,
                "That file is larger than this deployment will store.",
                details={"key": scrub(key), "byte_size": len(data), "limit": MAX_OBJECT_BYTES},
            )
        declared = (content_type or "").strip() or "application/octet-stream"
        _status, headers, _body = self._send("PUT", key, body=data, content_type=declared)
        return StoredObject(
            key=key,
            byte_size=len(data),
            content_type=declared,
            backend=self.backend_name,
            etag=self._header(headers, "ETag").strip('"'),
            created_at=now_iso(),
        )

    def get(self, key: str) -> bytes:
        """GET the object and return its bytes."""
        _status, _headers, body = self._send("GET", key)
        return body

    def open_stream(self, key: str) -> Any:
        """GET the object and hand back the response as a readable binary stream.

        The response is not buffered here: the caller reads it and must close it,
        which closes the connection.
        """
        return self._request("GET", self._url_for(key), None, {})

    def exists(self, key: str) -> bool:
        """True when a HEAD request succeeds; a 404 is simply False."""
        try:
            self._send("HEAD", key)
            return True
        except ApiError as error:
            if error.code == NOT_FOUND:
                return False
            raise

    def delete(self, key: str) -> bool:
        """DELETE the object. A 404 means there was nothing to remove."""
        try:
            self._send("DELETE", key)
            return True
        except ApiError as error:
            if error.code == NOT_FOUND:
                return False
            raise

    def stat(self, key: str) -> StoredObject:
        """HEAD the object and describe it. Raises ApiError(NOT_FOUND) if absent."""
        _status, headers, _body = self._send("HEAD", key)
        size_text = self._header(headers, "Content-Length") or "0"
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        created = ""
        stamp = self._header(headers, "Last-Modified")
        if stamp:
            try:
                created = iso(parsedate_to_datetime(stamp))
            except (TypeError, ValueError):
                created = ""
        return StoredObject(
            key=key,
            byte_size=size,
            content_type=self._header(headers, "Content-Type") or "application/octet-stream",
            backend=self.backend_name,
            etag=self._header(headers, "ETag").strip('"'),
            created_at=created,
        )

    def signed_url(self, key: str, expires_seconds: int = 900) -> str:
        """A presigned GET URL, signed in the query string rather than a header.

        The signature covers the method, the path, the query, the host header and
        the expiry, so the URL stops working when expires_seconds runs out.
        """
        try:
            expires = int(expires_seconds)
        except (TypeError, ValueError):
            expires = 900
        expires = max(1, min(expires, _MAX_PRESIGN_SECONDS))
        amz_date, date_stamp = self._timestamps()
        scope = self._scope(date_stamp)
        canonical_query = _encode_query(
            [
                ("X-Amz-Algorithm", _SIGV4_ALGORITHM),
                ("X-Amz-Credential", "%s/%s" % (self._access_key, scope)),
                ("X-Amz-Date", amz_date),
                ("X-Amz-Expires", str(expires)),
                ("X-Amz-SignedHeaders", "host"),
            ]
        )
        block, signed_names = _canonical_headers({"host": self._host_for(key)})
        signature = self._signature(
            "GET",
            _encode_path(self._object_path(key)),
            canonical_query,
            block,
            signed_names,
            _UNSIGNED_PAYLOAD,
            amz_date,
            date_stamp,
            scope,
        )
        return "%s://%s%s?%s&X-Amz-Signature=%s" % (
            self._scheme,
            self._host_for(key),
            _encode_path(self._object_path(key)),
            canonical_query,
            signature,
        )

    def list_prefix(self, prefix: str, limit: int = 200) -> List[StoredObject]:
        """List up to limit objects under prefix, using list-type=2."""
        cap = max(0, int(limit))
        if cap == 0:
            return []
        canonical_query = _encode_query(
            [
                ("list-type", "2"),
                ("prefix", prefix or ""),
                ("max-keys", str(min(cap, 1000))),
            ]
        )
        host = self._host_for("")
        url = "%s://%s%s?%s" % (
            self._scheme,
            host,
            _encode_path(self._bucket_path()),
            canonical_query,
        )
        response = self._request("GET", url, None, {})
        try:
            payload = response.read()
        finally:
            response.close()
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The bucket listing could not be read.",
                details={"prefix": scrub(prefix or "")},
                provider_message=scrub(str(error)),
            ) from error
        results: List[StoredObject] = []
        for element in root.iter():
            if not str(element.tag).endswith("Contents"):
                continue
            key = _xml_text(element, "Key")
            if not key:
                continue
            created = ""
            stamp = _xml_text(element, "LastModified")
            if stamp:
                try:
                    created = iso(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
                except ValueError:
                    created = ""
            try:
                size = int(_xml_text(element, "Size") or "0")
            except ValueError:
                size = 0
            results.append(
                StoredObject(
                    key=key,
                    byte_size=size,
                    content_type="application/octet-stream",
                    backend=self.backend_name,
                    etag=_xml_text(element, "ETag").strip('"'),
                    created_at=created,
                )
            )
            if len(results) >= cap:
                break
        results.sort(key=lambda item: item.key)
        return results


# ---------------------------------------------------------------------------
# Choosing a backend
# ---------------------------------------------------------------------------


def store_from_config(config: Any, *, data_dir: str = "") -> ObjectStore:
    """Build the object store this deployment is configured to use, and say why.

    The rule is short and lives in one place: the S3-compatible backend is chosen
    only when the configuration names an endpoint and carries both an access key
    and a secret key. Anything less is not a usable bucket, so the local backend
    is used instead of one that would fail on the first write.

    The local backend is rooted at the configured local directory. When that is
    unset it falls back to os.path.join(data_dir or ".", "storage"), so a caller
    that knows its data directory can keep objects inside it.

    config may be a Configuration or a StorageConfig; when it is a Configuration
    the settings are read from its storage attribute.
    """
    storage = getattr(config, "storage", config)
    endpoint = getattr(storage, "endpoint", "") or ""
    bucket = getattr(storage, "bucket", "") or ""
    access_key = getattr(storage, "access_key", "") or ""
    secret_key = getattr(storage, "secret_key", "") or ""
    if (
        isinstance(endpoint, str)
        and endpoint.strip()
        and isinstance(bucket, str)
        and bucket.strip()
        and isinstance(access_key, str)
        and access_key.strip()
        and isinstance(secret_key, str)
        and secret_key.strip()
    ):
        return S3ObjectStore(
            endpoint=endpoint.strip(),
            region=str(getattr(storage, "region", "") or "us-east-1"),
            bucket=bucket.strip(),
            access_key=access_key.strip(),
            secret_key=secret_key.strip(),
            verify_tls=True,
            path_style=bool(getattr(storage, "force_path_style", True)),
        )
    configured = getattr(storage, "root", "") or ""
    root = str(configured).strip()
    if not root or root in (".", "./"):
        root = os.path.join(data_dir or ".", "storage")
    return LocalObjectStore(root)
