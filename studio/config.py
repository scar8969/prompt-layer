"""Configuration, parsed once, validated at startup, reported honestly.

Rules this file follows on purpose:

* Nothing is required to *boot*. A deployment with no configuration still runs
  and still renders the connect-a-provider screens; it just cannot generate.
  That is the honest state of an unconfigured install, and hiding it behind a
  crash would be worse.
* Anything that must be set before a feature can work is listed by
  :meth:`Configuration.missing_for` and surfaced through /api/system/status so
  the UI can say "this part needs configuration" instead of failing mysteriously.
* Secrets are never logged. :meth:`Configuration.describe` returns a redacted
  view suitable for a health endpoint.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}

#: Development is the default because the platform ships as something you can
#: run locally with no setup. Production must be opted into explicitly, and
#: doing so turns on the strict defaults below.
ENVIRONMENTS = ("development", "test", "production")


class EnvReader:
    """Typed reads from a string mapping, with defaults that never raise.

    A separate reader rather than module-level helpers because the environment
    is a *parameter* here: tests build a configuration from an explicit dict and
    never touch the process environment, and two configurations can coexist in
    one interpreter without one leaking into the other.
    """

    def __init__(self, source: Mapping[str, str]) -> None:
        self._source = source

    def raw(self, name: str, default: str = "") -> str:
        value = self._source.get(name)
        if value is None:
            return default
        return value.strip()

    def flag(self, name: str, default: bool = False) -> bool:
        raw = self.raw(name)
        if not raw:
            return default
        lowered = raw.lower()
        if lowered in TRUE:
            return True
        if lowered in FALSE:
            return False
        return default

    def integer(self, name: str, default: int) -> int:
        raw = self.raw(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def number(self, name: str, default: float) -> float:
        raw = self.raw(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def items(self, name: str, default: Optional[List[str]] = None) -> List[str]:
        raw = self.raw(name)
        if not raw:
            return list(default or [])
        return [part.strip() for part in raw.split(",") if part.strip()]

    def secret(self, name: str) -> str:
        return self.raw(name)


@dataclass(frozen=True)
class DatabaseConfig:
    #: Reserved for a future networked database; empty means "use :attr:`path`".
    url: str = ""
    path: Path = field(default_factory=Path)
    busy_timeout_ms: int = 5000


@dataclass(frozen=True)
class StorageConfig:
    #: "local" keeps objects on disk and is the only backend with no config.
    #: "s3" targets any S3-compatible endpoint; see docs/LIMITATIONS.md for what
    #: has and has not been exercised against a live bucket.
    backend: str = "local"
    root: Path = field(default_factory=Path)
    endpoint: str = ""
    bucket: str = ""
    region: str = ""
    access_key: str = ""
    secret_key: str = ""
    #: Lifetime of a signed object URL.
    url_ttl_seconds: int = 900
    force_path_style: bool = True


@dataclass(frozen=True)
class SecurityConfig:
    #: 32 raw bytes, hex or base64, held outside the database.
    encryption_key: str = ""
    #: HMAC key for session-token hashing, CSRF tokens and IP hashing.
    auth_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    password_min_length: int = 10
    #: When true, a session cookie without Secure (i.e. plain http) is refused.
    require_https: bool = False
    #: Trust X-Forwarded-* from the reverse proxy in front of us.
    trust_proxy_headers: bool = True
    #: Per-IP request budget for the whole API, per minute.
    rate_limit_per_minute: int = 600
    #: Tighter budget for credential-touching endpoints, per minute.
    credential_rate_limit_per_minute: int = 30


@dataclass(frozen=True)
class EgressConfig:
    """Every outbound request the platform makes is filtered through this."""

    #: https is mandatory unless the host is explicitly allowlisted below.
    allow_insecure: bool = False
    #: Hostnames permitted to resolve to private, loopback, link-local or
    #: otherwise non-public addresses. This is the deliberate, auditable door for
    #: a self-hosted inference server, and it is empty in production by default.
    allow_private_hosts: List[str] = field(default_factory=list)
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 60.0
    #: Hard ceiling on a provider response body we will parse.
    max_response_bytes: int = 8 * 1024 * 1024
    #: Hard ceiling on a generated artifact we will download.
    max_download_bytes: int = 512 * 1024 * 1024
    max_redirects: int = 3
    user_agent: str = "StudioPlatform/1.0"


@dataclass(frozen=True)
class JobConfig:
    #: Worker threads inside the API process. The queue lives in the database, so
    #: a separate process runs the same loop with workers=0 in the web process.
    workers: int = 2
    poll_interval_seconds: float = 1.0
    #: A provider that has been "processing" longer than this is timed out.
    provider_timeout_seconds: int = 1800
    max_attempts: int = 3
    #: Exponential backoff base in seconds, doubled per attempt.
    retry_backoff_seconds: float = 5.0
    #: How long a worker's claim on a job is honoured before another may take it.
    lease_seconds: int = 120


@dataclass(frozen=True)
class MediaConfig:
    max_upload_bytes: int = 25 * 1024 * 1024
    max_video_upload_bytes: int = 512 * 1024 * 1024
    thumbnail_max_pixels: int = 512
    allowed_image_types: Tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    )
    allowed_video_types: Tuple[str, ...] = (
        "video/mp4",
        "video/webm",
        "video/quicktime",
    )


@dataclass(frozen=True)
class Configuration:
    environment: str
    data_dir: Path
    public_app_url: str
    api_base_url: str
    log_level: str
    log_json: bool
    database: DatabaseConfig
    storage: StorageConfig
    security: SecurityConfig
    egress: EgressConfig
    jobs: JobConfig
    media: MediaConfig
    #: Opt-in, off by default in production: the mock adapter for development and
    #: for the test suite. It is labelled a mock everywhere it can appear.
    mock_provider_enabled: bool = False
    #: True when generated keys are being held only on disk beside the data,
    #: which means credentials written now are readable only by this data dir.
    generated_keys: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def database_path(self) -> Path:
        return self.database.path

    def missing_for(self, feature: str) -> List[str]:
        """Environment variable names that must be set before `feature` works.

        This is what lets the UI be honest: instead of a disabled button with no
        explanation, an endpoint answers 503 `configuration_required` plus this
        list.
        """
        checks: Dict[str, List[Tuple[bool, str]]] = {
            "credentials": [(bool(self.security.encryption_key), "STUDIO_ENCRYPTION_KEY")],
            "auth": [(bool(self.security.auth_secret), "STUDIO_AUTH_SECRET")],
            "storage_object": [
                (
                    self.storage.backend == "local"
                    or bool(
                        self.storage.bucket
                        and self.storage.access_key
                        and self.storage.secret_key
                    ),
                    "STUDIO_STORAGE_BUCKET / STUDIO_STORAGE_ACCESS_KEY / STUDIO_STORAGE_SECRET_KEY",
                )
            ],
        }
        return [name for ok, name in checks.get(feature, []) if not ok]

    def describe(self) -> Dict[str, Any]:
        """A redacted view. Safe to return from a health or status endpoint."""
        return {
            "environment": self.environment,
            "public_app_url": self.public_app_url,
            "api_base_url": self.api_base_url,
            "log_level": self.log_level,
            "database": {"path": str(self.database.path)},
            "storage": {
                "backend": self.storage.backend,
                "root": str(self.storage.root) if self.storage.backend == "local" else None,
                "bucket": self.storage.bucket or None,
                "endpoint": self.storage.endpoint or None,
                "access_key": mask_secret(self.storage.access_key),
                "secret_key": mask_secret(self.storage.secret_key),
            },
            "security": {
                "encryption_key": mask_secret(self.security.encryption_key),
                "auth_secret": mask_secret(self.security.auth_secret),
                "require_https": self.security.require_https,
                "session_ttl_seconds": self.security.session_ttl_seconds,
                "generated_keys": self.generated_keys,
            },
            "egress": {
                "allow_insecure": self.egress.allow_insecure,
                "allow_private_hosts": list(self.egress.allow_private_hosts),
                "connect_timeout_seconds": self.egress.connect_timeout_seconds,
                "read_timeout_seconds": self.egress.read_timeout_seconds,
                "max_response_bytes": self.egress.max_response_bytes,
                "max_download_bytes": self.egress.max_download_bytes,
            },
            "jobs": {
                "workers": self.jobs.workers,
                "provider_timeout_seconds": self.jobs.provider_timeout_seconds,
                "max_attempts": self.jobs.max_attempts,
            },
            "mock_provider_enabled": self.mock_provider_enabled,
        }


def mask_secret(secret: str) -> Optional[str]:
    """At most: that something is present, and its last four characters."""
    if not secret:
        return None
    if len(secret) <= 8:
        return "*" * len(secret)
    return "*" * 8 + secret[-4:]


def _read_or_create_key(data_dir: Path, filename: str) -> str:
    """Read a key file beside the data, or create one. Used only when unset."""
    path = data_dir / filename
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
    return value


def load_config(environ: Optional[Mapping[str, str]] = None) -> Configuration:
    """Build the process configuration.

    Passing `environ` is how tests get a hermetic configuration without
    mutating the real environment.
    """
    env = EnvReader(dict(os.environ) if environ is None else environ)

    environment = env.raw("STUDIO_ENV", "development").lower()
    if environment not in ENVIRONMENTS:
        environment = "development"

    data_dir = Path(env.raw("STUDIO_DATA_DIR") or (Path.cwd() / "var" / "studio")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(env.raw("STUDIO_DATABASE_PATH") or (data_dir / "studio.db"))
    storage_root = Path(env.raw("STUDIO_STORAGE_ROOT") or (data_dir / "objects"))

    encryption_key = env.secret("STUDIO_ENCRYPTION_KEY")
    auth_secret = env.secret("STUDIO_AUTH_SECRET")
    generated_keys = False
    if not encryption_key:
        encryption_key = _read_or_create_key(data_dir, ".studio-encryption-key")
        generated_keys = True
    if not auth_secret:
        auth_secret = _read_or_create_key(data_dir, ".studio-auth-secret")
        generated_keys = True

    # Development gets loopback endpoints allowed so "point it at my own server
    # on localhost" works out of the box. Production gets an empty list unless an
    # operator names hosts explicitly -- see docs/SECURITY.md.
    allow_private_hosts = env.items("STUDIO_ALLOW_PRIVATE_HOSTS")
    if environment != "production" and not allow_private_hosts:
        allow_private_hosts = ["localhost", "127.0.0.1", "::1"]

    return Configuration(
        environment=environment,
        data_dir=data_dir,
        public_app_url=env.raw("STUDIO_PUBLIC_APP_URL", "http://127.0.0.1:8090").rstrip("/"),
        api_base_url=env.raw("STUDIO_API_BASE_URL", "http://127.0.0.1:8090/api").rstrip("/"),
        log_level=env.raw("STUDIO_LOG_LEVEL", "info").lower(),
        log_json=env.flag("STUDIO_LOG_JSON", environment == "production"),
        database=DatabaseConfig(
            url=env.raw("STUDIO_DATABASE_URL"),
            path=db_path,
            busy_timeout_ms=env.integer("STUDIO_DB_BUSY_TIMEOUT_MS", 5000),
        ),
        storage=StorageConfig(
            backend=env.raw("STUDIO_STORAGE_BACKEND", "local").lower(),
            root=storage_root,
            endpoint=env.raw("STUDIO_STORAGE_ENDPOINT"),
            bucket=env.raw("STUDIO_STORAGE_BUCKET"),
            region=env.raw("STUDIO_STORAGE_REGION", "us-east-1"),
            access_key=env.secret("STUDIO_STORAGE_ACCESS_KEY"),
            secret_key=env.secret("STUDIO_STORAGE_SECRET_KEY"),
            url_ttl_seconds=env.integer("STUDIO_STORAGE_URL_TTL_SECONDS", 900),
            force_path_style=env.flag("STUDIO_STORAGE_FORCE_PATH_STYLE", True),
        ),
        security=SecurityConfig(
            encryption_key=encryption_key,
            auth_secret=auth_secret,
            session_ttl_seconds=env.integer("STUDIO_SESSION_TTL_SECONDS", 60 * 60 * 24 * 14),
            password_min_length=env.integer("STUDIO_PASSWORD_MIN_LENGTH", 10),
            require_https=env.flag("STUDIO_REQUIRE_HTTPS", environment == "production"),
            trust_proxy_headers=env.flag("STUDIO_TRUST_PROXY_HEADERS", True),
            rate_limit_per_minute=env.integer("STUDIO_RATE_LIMIT_PER_MINUTE", 600),
            credential_rate_limit_per_minute=env.integer(
                "STUDIO_CREDENTIAL_RATE_LIMIT_PER_MINUTE", 30
            ),
        ),
        egress=EgressConfig(
            # Plain http is off everywhere by default, development included. An
            # API key sent over http is readable by anything on the network path,
            # and a laptop on a cafe network is not a different threat model from a
            # server. A developer who really is talking to a local provider sets
            # STUDIO_ALLOW_INSECURE_ENDPOINTS=1 and knows they did.
            allow_insecure=env.flag("STUDIO_ALLOW_INSECURE_ENDPOINTS", False),
            allow_private_hosts=allow_private_hosts,
            connect_timeout_seconds=env.number("STUDIO_EGRESS_CONNECT_TIMEOUT", 10.0),
            read_timeout_seconds=env.number("STUDIO_EGRESS_READ_TIMEOUT", 60.0),
            max_response_bytes=env.integer("STUDIO_EGRESS_MAX_RESPONSE_BYTES", 8 * 1024 * 1024),
            max_download_bytes=env.integer("STUDIO_EGRESS_MAX_DOWNLOAD_BYTES", 512 * 1024 * 1024),
            max_redirects=env.integer("STUDIO_EGRESS_MAX_REDIRECTS", 3),
        ),
        jobs=JobConfig(
            workers=env.integer("STUDIO_JOB_WORKERS", 2),
            poll_interval_seconds=env.number("STUDIO_JOB_POLL_INTERVAL", 1.0),
            provider_timeout_seconds=env.integer("STUDIO_JOB_PROVIDER_TIMEOUT", 1800),
            max_attempts=env.integer("STUDIO_JOB_MAX_ATTEMPTS", 3),
            retry_backoff_seconds=env.number("STUDIO_JOB_RETRY_BACKOFF", 5.0),
            lease_seconds=env.integer("STUDIO_JOB_LEASE_SECONDS", 120),
        ),
        media=MediaConfig(
            max_upload_bytes=env.integer("STUDIO_MAX_UPLOAD_BYTES", 25 * 1024 * 1024),
            max_video_upload_bytes=env.integer(
                "STUDIO_MAX_VIDEO_UPLOAD_BYTES", 512 * 1024 * 1024
            ),
            thumbnail_max_pixels=env.integer("STUDIO_THUMBNAIL_MAX_PIXELS", 512),
        ),
        mock_provider_enabled=env.flag(
            "STUDIO_ENABLE_MOCK_PROVIDER", environment != "production"
        ),
        generated_keys=generated_keys,
    )
