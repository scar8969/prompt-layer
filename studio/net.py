"""The only place this platform is allowed to make an outbound HTTP request.

Everything that talks to a user-supplied URL -- connection tests, model
discovery, generation submission, status polling, output download -- goes through
:class:`HttpClient`. There is no second path, because the moment there is a
second path, it is the one that forgets to check something.

What is being defended against
------------------------------
A user types a base URL into the connection form. That URL is, by construction,
an instruction for our server to fetch something from wherever they point it. So
the interesting case is not the honest user -- it is the honest user's account
being used to make *our* server issue requests it should not:

* @http://169.254.169.254/latest/meta-data/iam/security-credentials/@ -- cloud
  instance metadata, which on most providers hands out credentials to whoever
  asks from inside the instance.
* @http://127.0.0.1:8500/v1/kv/@ or @http://10.0.0.5/admin@ -- internal services
  that assume a trusted network, reachable only because we are inside it.
* @http://[::ffff:169.254.169.254]/@ -- the same metadata service, wearing a
  different address family.
* A hostname that resolves to a public address when we check it and to 127.0.0.1
  when we connect, a second later.

The layered defences, in order
------------------------------
1. The URL must parse, must have a host, must carry no embedded credentials, and
   its scheme must be http or https. Anything else is refused before DNS.
2. http is refused unless the deployment explicitly allows insecure endpoints,
   because an API key sent over cleartext is an API key handed to the network.
3. Known metadata and loopback hostnames are refused by name, so a hostname never
   gets a chance to resolve into something we would then have to reason about.
4. Every resolved address is checked against the blocked ranges. Loopback,
   link-local, private, multicast, reserved, unspecified and CGNAT space are all
   refused unless the deployment has turned on private hosts -- which is what a
   self-hosted inference server on the same network legitimately needs.
5. We connect to the validated address directly, so a hostile DNS answer that
   arrives between the check and the connect has nothing left to change.
6. Redirects are followed manually, and every hop goes back through step 1. A
   public URL that 302s to the metadata service is refused at the hop, not before.
7. Connect and read have separate timeouts, the response body is capped, and
   @Accept-Encoding: identity@ is sent so a compressed response cannot expand
   past the cap.
8. Environment proxies are ignored. urllib honours @HTTPS_PROXY@, which would
   silently move the connection somewhere this module cannot reason about.

What is *not* claimed
---------------------
This is not a sandbox. Anyone who can run code inside this process has already
won. What is claimed, and what the tests check, is that no URL a user can type
causes an outbound request into address space that is not theirs.
"""

from __future__ import annotations

import contextlib
import ipaddress
import http.client
import json
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

from .errors import (
    INVALID_RESPONSE,
    MALFORMED_URL,
    NETWORK_RESTRICTED,
    OUTPUT_DOWNLOAD_FAILED,
    TIMEOUT,
    ApiError,
)
from .logging_setup import get_logger
from .redact import redact_url, scrub_headers

LOGGER = get_logger("studio.net")

ALLOWED_SCHEMES = ("http", "https")

#: Names that mean "the machine we are running on" or "the cloud's credential
#: service", refused before any DNS lookup happens.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.com",
        "metadata.azure.internal",
        "metadata.oraclecloud.com",
        "metadata.packet.net",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)

#: Suffixes that are internal-only by definition.
BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
)

#: Address space a request may never reach, expressed as networks rather than as
#: a list of examples, so the check is complete rather than illustrative.
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",            # "this network"
        "10.0.0.0/8",           # private
        "100.64.0.0/10",        # carrier-grade NAT
        "127.0.0.0/8",          # loopback
        "169.254.0.0/16",       # link-local, and cloud metadata
        "172.16.0.0/12",        # private
        "192.0.0.0/24",         # IETF protocol assignments
        "192.0.2.0/24",         # TEST-NET-1
        "192.88.99.0/24",       # 6to4 relay anycast
        "192.168.0.0/16",       # private
        "198.18.0.0/15",        # benchmarking
        "198.51.100.0/24",      # TEST-NET-2
        "203.0.113.0/24",       # TEST-NET-3
        "224.0.0.0/4",          # multicast
        "240.0.0.0/4",          # reserved, includes 255.255.255.255
        "::/128",               # unspecified
        "::1/128",              # loopback
        "fc00::/7",             # unique local
        "fe80::/10",            # link-local
        "ff00::/8",             # multicast
        "64:ff9b::/96",         # NAT64
        "2001:db8::/32",        # documentation
        "2002::/16",            # 6to4
    )
)

#: Addresses worth naming individually, because they are the ones that actually
#: hand out credentials.
NAMED_METADATA_ADDRESSES = {
    "169.254.169.254": "AWS / Azure / GCP / DigitalOcean instance metadata",
    "169.254.170.2": "AWS ECS task metadata (container credentials)",
    "169.254.169.123": "AWS time sync",
    "100.100.100.200": "Alibaba Cloud metadata",
    "192.0.0.192": "Oracle Cloud metadata",
    "fd00:ec2::254": "AWS IMDS over IPv6",
    "fd00:ec2::23": "AWS IMDS over IPv6 (alternate)",
}


def _is_blocked_address(address: Any) -> Optional[str]:
    """Return a human reason if this address may not be contacted, else None."""
    named = NAMED_METADATA_ADDRESSES.get(str(address))
    if named:
        return named
    # An IPv4-mapped IPv6 address is an IPv4 address wearing a hat. Checking the
    # mapped address is what stops ::ffff:169.254.169.254 from slipping past a
    # set of IPv6 ranges that do not describe the metadata service at all.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        inner = _is_blocked_address(mapped)
        if inner:
            return inner
    for network in BLOCKED_NETWORKS:
        if address.version == network.version and address in network:
            if network.is_private or network.is_loopback or network.is_link_local:
                return "private or internal address space (%s)" % network
            return "reserved address space (%s)" % network
    return None


@dataclass(frozen=True)
class ResolvedTarget:
    """A URL that has passed every check, plus the address we will connect to."""

    scheme: str
    host: str
    port: int
    path: str
    address: str
    addresses: Tuple[str, ...]
    is_private: bool

    @property
    def url(self) -> str:
        default_port = 443 if self.scheme == "https" else 80
        netloc = self.host if self.port == default_port else "%s:%d" % (self.host, self.port)
        return urlunsplit((self.scheme, netloc, self.path, "", ""))

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.url


@dataclass
class HttpResponse:
    """A bounded, already-read response.

    The body is fully read before this object exists, so a caller cannot forget to
    close the socket and so the size cap is enforced exactly once, in one place.
    A body over the cap raises instead of arriving here partially read, so there is
    no truncated state for a caller to misread as a complete response.
    """

    status: int
    headers: Dict[str, str]
    body: bytes
    url: str
    elapsed_ms: int
    redirects: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        """Parse the body as JSON, or explain why that failed.

        A provider that answers with an HTML error page instead of JSON is a
        common and confusing failure, so the error carries a snippet of what was
        actually received.
        """
        if not self.body:
            raise ApiError(
                INVALID_RESPONSE,
                "The endpoint returned an empty response where JSON was expected.",
                details={"status": self.status, "url": redact_url(self.url)},
            )
        try:
            return json.loads(self.body.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError) as error:
            snippet = self.body[:400].decode("utf-8", errors="replace")
            raise ApiError(
                INVALID_RESPONSE,
                "The endpoint did not return valid JSON.",
                details={
                    "status": self.status,
                    "url": redact_url(self.url),
                    "content_type": self.headers.get("content-type", ""),
                    "received": snippet,
                },
                provider_message="%s" % error,
            ) from error

    def error_summary(self) -> str:
        """The most useful one-liner a caller can put in a diagnostic."""
        if self.headers.get("content-type", "").startswith("application/json"):
            try:
                payload = self.json()
            except ApiError:
                payload = None
            if isinstance(payload, Mapping):
                for key in ("error", "message", "detail", "msg", "error_description"):
                    value = payload.get(key)
                    if isinstance(value, str):
                        return value[:400]
                    if isinstance(value, Mapping):
                        inner = value.get("message") or value.get("detail")
                        if isinstance(inner, str):
                            return inner[:400]
        return " ".join(self.body[:400].decode("utf-8", errors="replace").split())


class EgressPolicy:
    """The rules, separated from the transport that enforces them."""

    def __init__(
        self,
        *,
        allow_insecure: bool = False,
        allow_private_hosts: "Sequence[str] | bool" = (),
        max_response_bytes: int = 8 * 1024 * 1024,
        max_download_bytes: int = 512 * 1024 * 1024,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 60.0,
        max_redirects: int = 3,
        user_agent: str = "StudioPlatform/1.0",
    ) -> None:
        self.allow_insecure = allow_insecure
        #: A set of hostnames, not a switch. `True` was accepted here once and
        #: meant "any host may be private", so one allowlisted name silently
        #: opened loopback and the cloud metadata service to every connection.
        #: It is now a per-host permit; True is read as an empty set and logged,
        #: because a caller that passes it wants nothing private and would
        #: otherwise get everything.
        if allow_private_hosts is True:
            LOGGER.warning(
                "allow_private_hosts=True is not a valid permit; use a host list",
                extra={"effect": "no host is permitted to resolve private"},
            )
            self.allow_private_hosts = frozenset()
        elif isinstance(allow_private_hosts, str):
            self.allow_private_hosts = frozenset([allow_private_hosts.lower()])
        else:
            self.allow_private_hosts = frozenset(
                str(name).strip().lower().rstrip(".")
                for name in (allow_private_hosts or ())
                if str(name).strip()
            )
        self.max_response_bytes = max_response_bytes
        self.max_download_bytes = max_download_bytes
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    @classmethod
    def from_config(cls, config: Any) -> "EgressPolicy":
        egress = config.egress
        return cls(
            allow_insecure=egress.allow_insecure,
            allow_private_hosts=egress.allow_private_hosts,
            max_response_bytes=egress.max_response_bytes,
            max_download_bytes=egress.max_download_bytes,
            connect_timeout_seconds=egress.connect_timeout_seconds,
            read_timeout_seconds=egress.read_timeout_seconds,
            max_redirects=egress.max_redirects,
            user_agent=egress.user_agent,
        )

    def permits_private(self, host: str) -> bool:
        """True when this exact host was named as permitted to resolve private.

        Exact match only. Suffix or wildcard matching would mean one entry could
        permit a domain somebody else controls, which is the whole risk this
        list exists to bound.
        """
        return host.lower().rstrip(".") in self.allow_private_hosts

    def describe(self) -> Dict[str, Any]:
        """What this policy permits, for the system-status endpoint and docs."""
        return {
            "allow_insecure_http": self.allow_insecure,
            "allow_private_hosts": sorted(self.allow_private_hosts),
            "max_response_bytes": self.max_response_bytes,
            "max_download_bytes": self.max_download_bytes,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "max_redirects": self.max_redirects,
            "blocked_hostnames": sorted(BLOCKED_HOSTNAMES),
            "blocked_networks": [str(n) for n in BLOCKED_NETWORKS],
            "named_metadata_addresses": dict(NAMED_METADATA_ADDRESSES),
        }

    # -- individual checks --------------------------------------------------
    def check_scheme(self, scheme: str, *, allow_insecure: Optional[bool] = None) -> None:
        if scheme not in ALLOWED_SCHEMES:
            raise ApiError(
                MALFORMED_URL,
                "Only http and https endpoints can be connected.",
                field="base_url",
                details={"scheme": scheme or "(none)"},
                hint="Enter a full URL such as https://api.example.com/v1.",
            )
        insecure_ok = self.allow_insecure if allow_insecure is None else allow_insecure
        if scheme == "http" and not insecure_ok:
            raise ApiError(
                NETWORK_RESTRICTED,
                "Plain http endpoints are disabled on this deployment.",
                field="base_url",
                hint=(
                    "Use an https URL. An API key sent over http is readable by "
                    "anything on the network path."
                ),
            )

    def check_hostname(self, host: str) -> None:
        """Refuse internal-looking names before they get a chance to resolve.

        A name the operator allowlisted bypasses this check entirely: that is
        the point of the list, which exists so somebody running a model server on
        `vllm.internal` can connect to it without disabling the policy.
        """
        lowered = host.lower().rstrip(".")
        if self.permits_private(lowered):
            return
        if lowered in BLOCKED_HOSTNAMES:
            raise ApiError(
                NETWORK_RESTRICTED,
                "That host is not reachable from this platform.",
                field="base_url",
                details={"host": lowered},
                hint="Point the connection at a public API endpoint.",
            )
        for suffix in BLOCKED_SUFFIXES:
            if lowered.endswith(suffix):
                raise ApiError(
                    NETWORK_RESTRICTED,
                    "Internal hostnames cannot be reached from this platform.",
                    field="base_url",
                    details={"host": lowered, "matched_suffix": suffix},
                    hint=(
                        "If this is a self-hosted model server, enable private "
                        "hosts for this deployment instead of using a .local name."
                    ),
                )

    def check_address(self, address: str, host: str) -> None:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise ApiError(
                NETWORK_RESTRICTED,
                "The host resolved to an address this platform cannot use.",
                details={"host": host, "address": address},
            ) from error
        named = NAMED_METADATA_ADDRESSES.get(str(parsed))
        if named:
            # Not negotiable, allowlist or not. These addresses hand out cloud
            # credentials to whoever asks; no self-hosted model server lives at
            # one, so permitting them would be a foot-gun with no legitimate use.
            raise ApiError(
                NETWORK_RESTRICTED,
                "That address serves cloud instance metadata and is never contacted.",
                field="base_url",
                details={"host": host, "address": address, "reason": named},
                hint="Use the public endpoint of the provider you actually want.",
            )
        reason = _is_blocked_address(parsed)
        if reason and not self.permits_private(host):
            raise ApiError(
                NETWORK_RESTRICTED,
                "That host resolves into private or reserved network space.",
                field="base_url",
                details={"host": host, "address": address, "reason": reason},
                hint=(
                    "Public API endpoints only. A self-hosted server on your own "
                    "network requires STUDIO_ALLOW_PRIVATE_HOSTS to be enabled "
                    "for the deployment."
                ),
            )
        if reason:
            # Allowed by configuration, but this is exactly the kind of thing an
            # operator needs to be able to find afterwards.
            LOGGER.warning(
                "connection to restricted address permitted by configuration",
                extra={"host": host, "address": address, "reason": reason},
            )

    def check_port(self, port: int, *, host: str) -> None:
        if not (1 <= port <= 65535):
            raise ApiError(
                MALFORMED_URL,
                "The port in that URL is out of range.",
                field="base_url",
                details={"host": host, "port": port},
            )

    # -- resolution ---------------------------------------------------------
    def resolve(self, host: str, port: int) -> Tuple[str, ...]:
        """Resolve a hostname to addresses, refusing the whole answer if any part
        of it is unacceptable.

        All-or-nothing is deliberate. If a hostname resolves to one public address
        and one loopback address, honouring only the public one would still let the
        operator of that hostname land a request in private space on a later
        request. The refusal is the honest answer.
        """
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            # A literal has no name to look up, so the permit has to name the
            # address itself. That is what makes "127.0.0.1" in the development
            # allowlist work while a bare 10.0.0.5 stays refused.
            self.check_address(str(literal), host)
            return (str(literal),)
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as error:
            raise ApiError(
                NETWORK_RESTRICTED,
                "That hostname could not be resolved.",
                field="base_url",
                details={"host": host},
                provider_message=str(error),
                hint="Check the URL for a typo and confirm the host is publicly resolvable.",
            ) from error
        addresses: List[str] = []
        for info in infos:
            candidate = info[4][0]
            if candidate in addresses:
                continue
            self.check_address(candidate, host)
            addresses.append(candidate)
        if not addresses:
            raise ApiError(
                NETWORK_RESTRICTED,
                "That hostname did not resolve to any usable address.",
                field="base_url",
                details={"host": host},
            )
        return tuple(addresses)


def parse_target(
    url: str,
    policy: EgressPolicy,
    *,
    allow_insecure: Optional[bool] = None,
    resolve: bool = True,
) -> ResolvedTarget:
    """Validate a URL and pick the address to connect to.

    Exposed separately from :class:`HttpClient` because the connection form needs
    to validate a base URL *before* anything is stored -- refusing a bad URL at the
    point the user typed it is a much better experience than refusing it later,
    when a generation has already been queued.
    """
    if not url or not isinstance(url, str):
        raise ApiError(MALFORMED_URL, "Enter the API endpoint URL.", field="base_url")
    candidate = url.strip()
    if len(candidate) > 2048:
        raise ApiError(MALFORMED_URL, "That URL is too long.", field="base_url")
    parts = urlsplit(candidate)
    policy.check_scheme(parts.scheme.lower(), allow_insecure=allow_insecure)
    if parts.username or parts.password:
        # A credential in a URL ends up in logs, in error messages and in browser
        # history. It is also not how any provider wants to receive it.
        raise ApiError(
            MALFORMED_URL,
            "Remove the username and password from the URL.",
            field="base_url",
            hint="Enter the API key in the key field instead.",
        )
    host = (parts.hostname or "").strip()
    if not host:
        raise ApiError(MALFORMED_URL, "That URL is missing a host.", field="base_url")
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError as error:
        raise ApiError(
            MALFORMED_URL, "The port in that URL is not a number.", field="base_url"
        ) from error
    policy.check_port(port, host=host)
    policy.check_hostname(host)
    path = parts.path or "/"
    if parts.query:
        path = path + "?" + parts.query
    if not resolve:
        # resolve=False exists so a caller can validate the *shape* of a URL
        # without a DNS lookup. An IP literal still needs the address check:
        # there is nothing to resolve, so skipping it would let
        # https://169.254.169.254/ through the early door while the request
        # path refused it later. Validating half a URL is worse than not
        # offering the option.
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            policy.check_address(str(literal), host)
        return ResolvedTarget(
            scheme=parts.scheme.lower(),
            host=host,
            port=port,
            path=path,
            address="",
            addresses=(),
            is_private=False,
        )
    addresses = policy.resolve(host, port)
    return ResolvedTarget(
        scheme=parts.scheme.lower(),
        host=host,
        port=port,
        path=path,
        address=addresses[0],
        addresses=addresses,
        is_private=bool(_is_blocked_address(ipaddress.ip_address(addresses[0]))),
    )


class _PinnedMixin:
    """Connect to the address we validated, not to whatever DNS says now.

    This is the single most important line of defence in this module. Between a
    DNS check and a connection there is a window; pinning the address removes it
    entirely, because the name is used only for the Host header and for TLS SNI,
    never for the connection itself.
    """

    _studio_address: str

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._studio_address, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()
        if isinstance(self, http.client.HTTPSConnection):
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPSConnection(_PinnedMixin, http.client.HTTPSConnection):
    pass


class _PinnedHTTPConnection(_PinnedMixin, http.client.HTTPConnection):
    pass


class HttpClient:
    """A bounded HTTP client that cannot be pointed at internal address space."""

    def __init__(self, policy: Optional[EgressPolicy] = None, *, config: Any = None) -> None:
        if policy is None:
            if config is None:
                raise ValueError("HttpClient needs a policy or a configuration")
            policy = EgressPolicy.from_config(config)
        self.policy = policy
        self._tls: Optional[ssl.SSLContext] = None
        self._lock = threading.Lock()

    # -- TLS ----------------------------------------------------------------
    @property
    def tls_context(self) -> ssl.SSLContext:
        """Verification on, minimum TLS 1.2.

        Built once and shared: constructing an SSLContext per request is a
        surprising amount of work and no security benefit.
        """
        if self._tls is None:
            with self._lock:
                if self._tls is None:
                    context = ssl.create_default_context()
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.check_hostname = True
                    context.verify_mode = ssl.CERT_REQUIRED
                    self._tls = context
        return self._tls

    # -- the request --------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        body: Optional[bytes] = None,
        json_body: Any = None,
        max_bytes: Optional[int] = None,
        read_timeout: Optional[float] = None,
        follow_redirects: bool = True,
        allow_insecure: Optional[bool] = None,
        accept: str = "application/json",
        timeout_seconds: Optional[float] = None,
    ) -> HttpResponse:
        """Perform one request, following redirects only through this validation.

        The body is read fully and bounded before returning. A caller that wants
        to stream a large video output should use :meth:`download` instead, which
        writes to disk without ever holding the whole file in memory.
        """
        if json_body is not None and body is not None:
            raise ValueError("pass either json_body or body, not both")
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        send_headers: Dict[str, str] = {
            "Accept": accept,
            # A gzip bomb is a 10 KB response that expands to 10 GB, which would
            # blow past every size cap in this file. Asking for identity costs a
            # little bandwidth and removes the entire class of problem.
            "Accept-Encoding": "identity",
            "User-Agent": self.policy.user_agent,
        }
        for name, value in (headers or {}).items():
            # A caller-supplied Host or Accept-Encoding would let it bypass the
            # pinning or the decompression choice above.
            if name.lower() in ("host", "accept-encoding", "content-length"):
                continue
            send_headers[name] = str(value)
        if body is not None and "Content-Type" not in send_headers:
            send_headers["Content-Type"] = "application/json"

        limit = max_bytes if max_bytes is not None else self.policy.max_response_bytes
        current = url
        redirects = 0
        started = time.monotonic()
        while True:
            target = parse_target(current, self.policy, allow_insecure=allow_insecure)
            response = self._send(
                method,
                target,
                send_headers,
                body,
                limit=limit,
                read_timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else (
                        read_timeout
                        if read_timeout is not None
                        else self.policy.read_timeout_seconds
                    )
                ),
            )
            location = response.headers.get("location")
            if follow_redirects and response.status in (301, 302, 303, 307, 308) and location:
                if redirects >= self.policy.max_redirects:
                    raise ApiError(
                        NETWORK_RESTRICTED,
                        "The endpoint redirected too many times.",
                        details={"url": redact_url(current), "redirects": redirects},
                        hint="Point the connection at the final URL instead.",
                    )
                redirects += 1
                # Each hop re-enters the loop above, so every redirect is checked
                # with the same rules as the original URL. This is the check that
                # stops a public URL from bouncing into the metadata service.
                current = urljoin(current, location)
                if response.status == 303 and method not in ("GET", "HEAD"):
                    method, body = "GET", None
                    send_headers.pop("Content-Type", None)
                LOGGER.info(
                    "following redirect",
                    extra={"route": "egress", "to": redact_url(current), "hop": redirects},
                )
                continue
            response.redirects = redirects
            response.elapsed_ms = int((time.monotonic() - started) * 1000)
            return response

    def _open_connection(self, target: ResolvedTarget) -> Any:
        """Build a connection pinned to the address the policy already validated.

        http.client resolves the hostname again on its own, which is exactly the
        window a DNS-rebinding attack needs: answer the policy check with a
        public address, then answer the connect with a private one. Connecting to
        the validated address removes the second lookup from the picture; the
        hostname survives only in the Host header and the TLS SNI.
        """
        if target.scheme == "https":
            connection: Any = _PinnedHTTPSConnection(
                target.host,
                target.port,
                timeout=self.policy.connect_timeout_seconds,
                context=self.tls_context,
            )
        else:
            connection = _PinnedHTTPConnection(
                target.host, target.port, timeout=self.policy.connect_timeout_seconds
            )
        connection._studio_address = target.address
        return connection

    def _send(
        self,
        method: str,
        target: ResolvedTarget,
        headers: Mapping[str, str],
        body: Optional[bytes],
        *,
        limit: int,
        read_timeout: float,
    ) -> HttpResponse:
        connection = self._open_connection(target)
        try:
            connection.connect()
            # The socket timeout applies to reads too, so a server that accepts
            # the connection and then says nothing does not hold a worker forever.
            connection.sock.settimeout(read_timeout)
            if body is not None:
                headers = dict(headers)
                headers["Content-Length"] = str(len(body))
            connection.putrequest(
                method, target.path, skip_host=False, skip_accept_encoding=True
            )
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return HttpResponse(
                status=response.status,
                headers={k.lower(): v for k, v in response.getheaders()},
                body=self._read_capped(response, limit),
                url=target.url,
                elapsed_ms=0,
            )
        except ssl.SSLCertVerificationError as error:
            raise ApiError(
                NETWORK_RESTRICTED,
                "The endpoint's TLS certificate could not be verified.",
                details={"host": target.host, "url": redact_url(target.url)},
                provider_message=str(error),
                hint="A certificate that does not match the hostname is not trusted here.",
            ) from error
        except ssl.SSLError as error:
            raise ApiError(
                NETWORK_RESTRICTED,
                "The TLS handshake with the endpoint failed.",
                details={"host": target.host, "url": redact_url(target.url)},
                provider_message=str(error),
            ) from error
        except (socket.timeout, TimeoutError) as error:
            raise ApiError(
                TIMEOUT,
                "The endpoint did not respond in time.",
                details={"url": redact_url(target.url)},
                provider_message=str(error),
                hint="Increase the timeout for slow model endpoints.",
            ) from error
        except (ConnectionError, OSError) as error:
            raise ApiError(
                NETWORK_RESTRICTED,
                "The endpoint could not be reached.",
                details={"url": redact_url(target.url)},
                provider_message=str(error),
                hint="Check the hostname, the port, and that the service is running.",
            ) from error
        except http.client.HTTPException as error:
            raise ApiError(
                INVALID_RESPONSE,
                "The endpoint sent a malformed HTTP response.",
                details={"url": redact_url(target.url)},
                provider_message=str(error),
            ) from error
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    @staticmethod
    def _read_capped(response: Any, limit: int) -> bytes:
        """Read a body, refusing one larger than the limit rather than truncating it.

        A truncated JSON body reaches the parser as a syntax error, which hides
        the real cause. In practice an over-cap response is an error page or an
        endpoint that is not the API it claims to be, so this says that instead.
        """
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(65536, max(1, limit - total + 1)))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ApiError(
                    INVALID_RESPONSE,
                    "The endpoint sent a response larger than this deployment will read.",
                    details={"limit": limit, "read": total},
                    hint="Check that the URL points at the API, not an HTML page or a file host.",
                )
        return b"".join(chunks)

    # -- convenience --------------------------------------------------------
    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("POST", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("HEAD", url, **kwargs)

    def download(
        self,
        url: str,
        destination: Any,
        *,
        headers: Optional[Mapping[str, str]] = None,
        max_bytes: Optional[int] = None,
        expected_content_type: Optional[str] = None,
        chunk_size: int = 262144,
    ) -> Dict[str, Any]:
        """Stream a remote file to disk, bounded, without buffering it in memory.

        Used for generation outputs, which are large images and videos. The file
        is written to a @.part@ path and renamed on success, so a truncated
        download is never mistaken for a finished file.
        """
        limit = max_bytes if max_bytes is not None else self.policy.max_download_bytes
        target = parse_target(url, self.policy)
        connection = self._open_connection(target)
        send = {
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": self.policy.user_agent,
        }
        for name, value in (headers or {}).items():
            if name.lower() in ("host", "accept-encoding"):
                continue
            send[name] = str(value)
        written = 0
        content_type = ""
        part = str(destination) + ".part"
        try:
            connection.connect()
            connection.sock.settimeout(self.policy.read_timeout_seconds)
            connection.putrequest("GET", target.path, skip_accept_encoding=True)
            for name, value in send.items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                # Following a redirect here would mean handing an attacker-chosen
                # URL to a second, unvalidated connection holding nothing but a
                # file body. Output URLs come from the provider's own response,
                # so a redirect is a misconfigured endpoint, not a normal case.
                raise ApiError(
                    OUTPUT_DOWNLOAD_FAILED,
                    "The generated file is behind a redirect, which is not followed.",
                    details={
                        "url": redact_url(target.url),
                        "status": response.status,
                        "location": redact_url(response.getheader("Location") or ""),
                    },
                    hint="Providers should return a direct link to the output file.",
                )
            if response.status >= 400:
                snippet = response.read(4096)
                raise ApiError(
                    OUTPUT_DOWNLOAD_FAILED,
                    "The generated file could not be downloaded.",
                    details={
                        "url": redact_url(target.url),
                        "status": response.status,
                        "received": snippet[:400].decode("utf-8", errors="replace"),
                    },
                )
            content_type = (response.getheader("Content-Type") or "").split(";")[0].strip()
            if (
                expected_content_type
                and content_type
                and not content_type.startswith(expected_content_type.split("/")[0])
            ):
                # A provider that returns an HTML error page with status 200 is
                # common enough to check for, because the alternative is storing
                # an error page as if it were the user's image.
                raise ApiError(
                    OUTPUT_DOWNLOAD_FAILED,
                    "The download did not return the expected file type.",
                    details={
                        "url": redact_url(target.url),
                        "content_type": content_type,
                        "expected": expected_content_type,
                    },
                )
            declared = response.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise ApiError(
                    OUTPUT_DOWNLOAD_FAILED,
                    "The generated file is larger than this deployment will store.",
                    details={
                        "url": redact_url(target.url),
                        "bytes": int(declared),
                        "limit": limit,
                    },
                )
            with open(part, "wb") as handle:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise ApiError(
                            OUTPUT_DOWNLOAD_FAILED,
                            "The generated file is larger than this deployment will store.",
                            details={"limit": limit, "url": redact_url(target.url)},
                        )
                    handle.write(chunk)
        except OSError as error:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise ApiError(
                OUTPUT_DOWNLOAD_FAILED,
                "The generated file could not be written to storage.",
                provider_message=str(error),
            ) from error
        finally:
            with contextlib.suppress(Exception):
                connection.close()
        if written == 0:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise ApiError(
                OUTPUT_DOWNLOAD_FAILED,
                "The download produced an empty file.",
                details={"url": redact_url(target.url)},
            )
        os.replace(part, str(destination))
        return {"bytes": written, "content_type": content_type, "url": redact_url(target.url)}


def scrub_request_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    """A version of the headers safe to put in a log line or an error payload."""
    return scrub_headers(headers)


#: One client per thread. The object holds a policy and a TLS context and nothing
#: per-request, so sharing is safe and recreating it per call would just re-parse
#: the rules.
_local = threading.local()


def client_for(config: Any) -> HttpClient:
    existing = getattr(_local, "client", None)
    if existing is None:
        existing = HttpClient(config=config)
        _local.client = existing
    return existing


def reset_client() -> None:
    """Drop the thread-local client. Used by tests that change configuration."""
    if hasattr(_local, "client"):
        delattr(_local, "client")


def validate_endpoint(url: str, config: Any, *, allow_insecure: Optional[bool] = None) -> ResolvedTarget:
    """Check a URL without sending anything. What the connection form calls on submit."""
    return parse_target(url, EgressPolicy.from_config(config), allow_insecure=allow_insecure)


def probe_addresses(host: str, addresses: Sequence[str]) -> List[Dict[str, Any]]:
    """Explain, per address, whether it would be permitted. Diagnostics only."""
    out = []
    for address in addresses:
        reason = _is_blocked_address(ipaddress.ip_address(address))
        out.append(
            {
                "address": address,
                "host": host,
                "blocked": bool(reason),
                "reason": reason,
                "permitted_here": not reason,
            }
        )
    return out
