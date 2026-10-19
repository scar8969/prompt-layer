"""Security guarantees for the Studio package: egress, envelopes, redaction.

Each test here states one guarantee the platform relies on and says, in its
docstring, why losing that guarantee would matter. Three modules are covered:

* studio.net -- the only place an outbound request may start, and the guard that
  decides whether a user-supplied URL is allowed to become one.
* studio.crypto -- the envelope a provider credential is stored in, and the
  password hash a login is checked against.
* studio.redact -- the scrubber every log line, error payload and provider
  message passes through on its way out.

Everything runs offline. URLs are IP literals so the checks never need DNS, and
the two places where a connection could plausibly be attempted patch
socket.create_connection to fail loudly, so a refused URL is proven to be
refused *before* the network is touched, not merely to fail afterwards.

Where the code behaves differently from what its own docstrings or the docs/
error table claim, the test asserts the behaviour that actually happens and the
docstring says so explicitly. Those cases are marked "BEHAVIOUR NOTE".

Run:  python -m pytest tests/test_security.py -q
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import errors  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.crypto import (  # noqa: E402
    CRYPTO_AVAILABLE,
    EncryptionUnavailable,
    SecretBox,
    SecretUnreadable,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from studio.errors import ApiError  # noqa: E402
from studio.net import (  # noqa: E402
    BLOCKED_HOSTNAMES,
    BLOCKED_NETWORKS,
    NAMED_METADATA_ADDRESSES,
    EgressPolicy,
    HttpClient,
    parse_target,
    validate_endpoint,
)
from studio.redact import (  # noqa: E402
    forget_all_secrets,
    is_sensitive_key,
    redact_url,
    register_secret,
    scrub,
    scrub_headers,
    scrub_structure,
)

#: A public IPv4 literal that is in no blocked range. Nothing is ever sent to it:
#: the guard stops at validation, which is the whole point of these tests.
PUBLIC_IP = "93.184.216.34"

#: The two codes the documented error table (docs/API.md) gives the egress guard
#: for a refused destination. Anything outside this set would be a contract break
#: for the client, which branches on the code and never on the message.
DOCUMENTED_EGRESS_CODES = frozenset({errors.NETWORK_RESTRICTED, errors.MALFORMED_URL})


# ---------------------------------------------------------------------------
# Fixtures: hermetic configuration, one data dir per test
# ---------------------------------------------------------------------------


@pytest.fixture()
def production_config(tmp_path):
    """A production configuration with an empty private-host allowlist.

    Production is the deployment shape the egress guard is written for, and it is
    the only shape in which "refused by default" is literally true -- see the
    development-default test below.
    """
    return load_config(
        {
            "STUDIO_ENV": "production",
            "STUDIO_DATA_DIR": str(tmp_path / "data"),
        }
    )


@pytest.fixture()
def development_config(tmp_path):
    """The default (development) configuration, which auto-allowlists loopback."""
    return load_config(
        {
            "STUDIO_ENV": "development",
            "STUDIO_DATA_DIR": str(tmp_path / "data"),
        }
    )


@pytest.fixture(autouse=True)
def _clean_redaction_registry():
    """Prove the redaction patterns work on their own.

    The scrubber keeps a runtime registry of live secret values, which would make
    every pattern test pass for the wrong reason. Dropping it before and after
    means each assertion is about the pattern, not the registry.
    """
    forget_all_secrets()
    yield
    forget_all_secrets()


def refusal(url, config, **kwargs):
    """Validate url and return the ApiError, failing if it was accepted."""
    with pytest.raises(ApiError) as caught:
        validate_endpoint(url, config, **kwargs)
    return caught.value


# ---------------------------------------------------------------------------
# studio.net -- the scheme allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, scheme",
    [
        ("file:///etc/passwd", "file"),
        ("gopher://93.184.216.34:70/1", "gopher"),
        ("ftp://93.184.216.34/etc/passwd", "ftp"),
        ("data:text/plain,hello", "data"),
        ("javascript:alert(document.cookie)", "javascript"),
        ("dict://93.184.216.34:2628/", "dict"),
        ("//93.184.216.34/v1", "(none)"),
        ("93.184.216.34/v1", "(none)"),
    ],
)
def test_only_http_and_https_can_be_connected(url, scheme, production_config):
    """A non-HTTP scheme is a different protocol wearing a URL's clothes.

    file: reads the server's disk, gopher: and dict: can be aimed at internal
    services, data: and javascript: exist only to be rendered. urllib would
    honour some of these, so the scheme has to be refused before anything that
    understands it ever sees the string.
    """
    error = refusal(url, production_config, allow_insecure=True)
    assert error.code == errors.MALFORMED_URL
    assert error.details["scheme"] == scheme
    assert error.hint
    assert "http" in error.message.lower()


def test_https_to_a_public_literal_address_is_accepted(production_config):
    """The guard has to let the legitimate case through.

    A security check that refuses everything is trivially safe and useless: the
    product is BYOK against public provider APIs, so a public https endpoint must
    validate, and must carry the address the connection will be pinned to.
    """
    target = validate_endpoint("https://%s/v1/models" % PUBLIC_IP, production_config)
    assert target.address == PUBLIC_IP
    assert target.addresses == (PUBLIC_IP,)
    assert target.scheme == "https"
    assert target.port == 443
    assert target.is_private is False


def test_the_policy_describe_view_is_the_audited_surface():
    """Operators reason about egress from this one dict, so it must be complete.

    describe() is what the system-status endpoint and the security docs are built
    from. If a blocked range or a metadata address were missing from it, the
    documented policy and the enforced policy would drift apart silently.
    """
    described = EgressPolicy().describe()
    assert described["allow_insecure_http"] is False
    assert [str(n) for n in BLOCKED_NETWORKS] == described["blocked_networks"]
    assert sorted(BLOCKED_HOSTNAMES) == described["blocked_hostnames"]
    assert dict(NAMED_METADATA_ADDRESSES) == described["named_metadata_addresses"]
    assert "127.0.0.0/8" in described["blocked_networks"]
    assert "fc00::/7" in described["blocked_networks"]
    assert described["named_metadata_addresses"]["169.254.169.254"]


# ---------------------------------------------------------------------------
# studio.net -- cloud metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://169.254.170.2/v2/credentials",
        "https://169.254.169.123/latest/meta-data/",
        "https://100.100.100.200/latest/meta-data/",
        "https://192.0.0.192/opc/v1/instance/",
        "https://[fd00:ec2::254]/latest/meta-data/",
    ],
)
def test_cloud_metadata_addresses_are_refused(url, production_config):
    """The metadata service hands credentials to anyone who asks from inside.

    This is the SSRF payoff: one GET to 169.254.169.254 and the attacker has the
    instance's role credentials, with everything that implies. Every provider's
    address is named individually, because each one is a different number in a
    range some other check might not describe.
    """
    error = refusal(url, production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert error.details["address"] in NAMED_METADATA_ADDRESSES
    assert error.hint


@pytest.mark.parametrize(
    "url",
    [
        "https://metadata.google.internal/computeMetadata/v1/instance/service-accounts/",
        "https://metadata.goog/computeMetadata/v1/",
        "https://metadata/computeMetadata/v1/",
        "https://metadata.azure.internal/metadata/instance",
        "https://kubernetes.default.svc/api/v1/namespaces/default/secrets",
    ],
)
def test_metadata_hostnames_are_refused_by_name_before_dns(url, production_config):
    """A name is stopped before it resolves, so no DNS answer can talk us out of it.

    Resolving first would mean trusting whatever the resolver says; refusing the
    name outright means an attacker cannot control the verdict by controlling the
    lookup. It also means the refusal still holds on a host where those names
    resolve nowhere at all.
    """
    error = refusal(url, production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert error.details["host"] in BLOCKED_HOSTNAMES


def test_an_ipv4_mapped_metadata_address_is_refused(production_config):
    """::ffff:169.254.169.254 is the same service wearing IPv6 clothes.

    An IPv6-only range check would wave this through, because no IPv6 CIDR
    describes the metadata service. The mapped form has to be unwrapped and
    re-checked as the IPv4 address it actually is.
    """
    error = refusal("https://[::ffff:169.254.169.254]/latest/meta-data/", production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert "metadata" in error.details["reason"].lower()


# ---------------------------------------------------------------------------
# studio.net -- loopback, private and reserved ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "10.255.255.254",
        "172.16.0.9",
        "172.31.255.1",
        "192.168.1.10",
        "0.0.0.0",
        "100.64.0.1",
        "169.254.1.1",
        "::1",
        "fc00::1",
        "fd12:3456::1",
        "fe80::1",
        "::",
    ],
)
def test_loopback_private_and_reserved_literals_are_refused(address, production_config):
    """Internal services assume a trusted network; we are on the wrong side of it.

    An admin panel on 10.0.0.5, a key-value store on 127.0.0.1:8500, a printer on
    the link-local range -- all of them authenticate by "you are inside", which
    makes our server an unwitting proxy for anyone who can type a URL.
    """
    url = "https://[%s]/v1" % address if ":" in address else "https://%s/v1" % address
    error = refusal(url, production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert error.details["address"] == address
    assert "private or internal" in error.details["reason"] or "reserved" in error.details["reason"]


def test_the_public_neighbour_of_a_private_range_is_still_allowed(production_config):
    """The ranges must stop exactly where they are documented to stop.

    172.16.0.0/12 ends at 172.31.255.255, so 172.32.0.1 is ordinary public space.
    A guard that widened the block "to be safe" would start refusing real provider
    addresses, which is how a security control gets disabled by an operator under
    pressure.
    """
    target = validate_endpoint("https://172.32.0.1/v1", production_config)
    assert target.address == "172.32.0.1"
    assert target.is_private is False


def test_the_development_allowlist_makes_localhost_usable(development_config):
    '''The default names its own machine, and the name now actually works.

    config.load_config() adds localhost, 127.0.0.1 and ::1 outside production
    so that "point it at my own server" needs no configuration. The hostname
    check used to refuse the name regardless, which made the entry dead and the
    comment wrong. A host on the allowlist is now exempt from the name check,
    which is the only reading of a hostname allowlist that is not a lie.
    '''
    assert 'localhost' in development_config.egress.allow_private_hosts
    target = validate_endpoint(
        'http://localhost:8090/v1', development_config, allow_insecure=True
    )
    assert target.host == 'localhost'
    assert target.address in ('127.0.0.1', '::1'), target.address


def test_a_host_that_is_not_allowlisted_is_still_refused_by_name(development_config):
    '''Exempting one name must not exempt the class of internal names.

    The check exists because metadata.google.internal and kubernetes.default
    hand out credentials. Only the exact names an operator listed are exempt.
    '''
    for url in ('http://metadata.google.internal/v1', 'https://kubernetes.default.svc/v1'):
        error = refusal(url, development_config, allow_insecure=True)
        assert error.code == errors.NETWORK_RESTRICTED
        assert error.details['host']


# ---------------------------------------------------------------------------
# studio.net -- alternate spellings of 127.0.0.1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",          # 127.0.0.1 as one decimal integer
        "http://0x7f000001/",          # 127.0.0.1 as one hex integer
        "http://0177.0.0.1/",          # octal first octet
        "http://017700000001/",        # octal, collapsed
        "http://127.1/",               # inet_aton shorthand
        "http://0x7f.0.0.1/",          # hex octet
    ],
)
def test_integer_hex_and_octal_spellings_never_reach_loopback(url, production_config):
    """Every browser and resolver accepts these spellings as 127.0.0.1.

    "http://2130706433/" and "http://0x7f000001/" are the classic filter bypasses:
    a filter looking for "127." sees an innocent number. This deployment is safe
    because it never parses those forms itself -- they are not IP literals to
    ipaddress, so they fall through to the resolver, which cannot resolve them
    here and the request is refused. The test asserts refusal and does not assert
    which layer refused, because on a host whose resolver does accept such
    spellings the address check is the layer that has to catch it.
    """
    error = refusal(url, production_config, allow_insecure=True)
    assert error.code in DOCUMENTED_EGRESS_CODES
    assert error.hint
    detail_keys = set((error.details or {}).keys())
    assert detail_keys & {"host", "address"}, detail_keys


def test_ipv4_mapped_loopback_is_refused(production_config):
    """[::ffff:127.0.0.1] is loopback, and an IPv6-only check would miss it.

    The mapped form is what a dual-stack host actually connects to when asked for
    the IPv4 address, so permitting it is permitting loopback by another name.
    """
    error = refusal("https://[::ffff:127.0.0.1]/v1", production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert "127.0.0.0/8" in error.details["reason"]


# ---------------------------------------------------------------------------
# studio.net -- the explicit allowlist door
# ---------------------------------------------------------------------------


def test_a_deployment_without_an_allowlist_refuses_private_addresses(production_config):
    """The production default is the one that has to hold with no operator input.

    A deployment nobody configured must not be able to reach the private network,
    because the person who would notice is the person who is not there.
    """
    assert production_config.egress.allow_private_hosts == []
    error = refusal("https://10.0.0.5/v1", production_config)
    assert error.code == errors.NETWORK_RESTRICTED


def test_a_deployment_with_an_allowlist_permits_private_addresses(tmp_path):
    '''A self-hosted inference server is a legitimate reason to cross the line.

    The door exists on purpose: a team running vLLM on their own network must be
    able to point the product at it. The allowlist is what makes that a decision
    somebody made rather than a hole nobody noticed.
    '''
    config = load_config(
        {
            'STUDIO_ENV': 'production',
            'STUDIO_DATA_DIR': str(tmp_path / 'data'),
            'STUDIO_ALLOW_PRIVATE_HOSTS': '10.0.0.5',
        }
    )
    assert config.egress.allow_private_hosts == ['10.0.0.5']
    target = validate_endpoint('https://10.0.0.5/v1', config)
    assert target.address == '10.0.0.5'
    assert target.is_private is True


def test_the_allowlist_is_a_per_host_permit_not_a_global_switch(tmp_path):
    '''One permitted host must not open the whole private network.

    The list was stored as a sequence but tested for truthiness, so naming
    internal.example also permitted 10.0.0.0/8 and 127.0.0.1 -- every private
    range the policy exists to refuse. Each entry now grants exactly itself.
    '''
    config = load_config(
        {
            'STUDIO_ENV': 'production',
            'STUDIO_DATA_DIR': str(tmp_path / 'data'),
            'STUDIO_ALLOW_PRIVATE_HOSTS': 'internal.example',
        }
    )
    built = EgressPolicy.from_config(config)
    assert built.permits_private('internal.example') is True
    assert built.permits_private('other.example') is False

    for url in ('https://10.0.0.5/v1', 'https://127.0.0.1/v1', 'https://192.168.1.1/v1'):
        error = refusal(url, config)
        assert error.code == errors.NETWORK_RESTRICTED


def test_instance_metadata_is_refused_even_when_a_host_is_allowlisted(tmp_path):
    '''No allowlist entry may re-open the cloud credential service.

    The metadata addresses hand out temporary cloud credentials to any process
    that asks, and no self-hosted model server lives at one. Allowing them by
    configuration would be a foot-gun with no legitimate use, so the refusal is
    unconditional and says why.
    '''
    config = load_config(
        {
            'STUDIO_ENV': 'production',
            'STUDIO_DATA_DIR': str(tmp_path / 'data'),
            'STUDIO_ALLOW_PRIVATE_HOSTS': '169.254.169.254,100.100.100.200',
        }
    )
    for url in (
        'https://169.254.169.254/latest/meta-data/',
        'https://100.100.100.200/latest/meta-data/',
    ):
        error = refusal(url, config)
        assert error.code == errors.NETWORK_RESTRICTED
        assert 'metadata' in error.message.lower()


def test_a_boolean_allowlist_is_read_as_no_permission(tmp_path):
    '''A caller that passes True wants nothing private, and must not get everything.

    The parameter used to accept a bool, so EgressPolicy(allow_private_hosts=True)
    opened every private range. It is now a host set; a bare True is logged and
    read as empty, because failing open on a type mistake is exactly how an SSRF
    hole ships.
    '''
    built = EgressPolicy(allow_private_hosts=True)
    assert built.allow_private_hosts == frozenset()
    assert built.permits_private('10.0.0.5') is False


def test_internal_only_suffixes_are_refused_unless_named_exactly(tmp_path):
    '''A suffix nobody can own is refused; the exact name is not.

    .local, .internal and .localdomain are mDNS or org-local conventions that
    resolve differently on every machine on a segment, so they are refused by
    default. The allowance is deliberately per name rather than per suffix: it
    must be possible to run a server called db.local, and impossible to permit
    every name that ends that way.
    '''
    config = load_config(
        {
            'STUDIO_ENV': 'production',
            'STUDIO_DATA_DIR': str(tmp_path / 'data'),
            'STUDIO_ALLOW_PRIVATE_HOSTS': 'db.local',
        }
    )
    error = refusal('https://printer.local/v1', config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert error.details['matched_suffix'] == '.local'


# studio.net -- cleartext, embedded credentials, length, ports
# ---------------------------------------------------------------------------


def test_plain_http_is_refused_unless_the_deployment_allows_it(production_config):
    """An API key over http is an API key handed to the whole network path.

    The credential is the asset being protected here, not the response: a cafe
    network, a compromised router and a shared VPS are all able to read a
    cleartext Authorization header.
    """
    error = refusal("http://%s/v1" % PUBLIC_IP, production_config)
    assert error.code == errors.NETWORK_RESTRICTED
    assert "https" in error.hint.lower()
    assert production_config.egress.allow_insecure is False


def test_allow_insecure_permits_plain_http_and_reports_port_80(production_config):
    """The opt-out has to actually work, or operators will find a worse one.

    A control with no legitimate escape hatch gets bypassed at the proxy layer,
    where nothing can be audited. Making the exception an explicit argument keeps
    it visible at the call site that needed it.
    """
    target = validate_endpoint("http://%s/v1" % PUBLIC_IP, production_config, allow_insecure=True)
    assert target.scheme == "http"
    assert target.port == 80
    assert target.url == "http://%s/v1" % PUBLIC_IP


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@%s/v1" % PUBLIC_IP,
        "https://user@%s/v1" % PUBLIC_IP,
        "https://sk-live-abcdefghijklmnop:@%s/v1" % PUBLIC_IP,
    ],
)
def test_urls_with_embedded_credentials_are_refused_not_stripped(url, production_config):
    """Credentials in a URL leak into logs, error messages and browser history.

    The module's docstring offers "carries no embedded credentials" as the first
    layer, and the implementation refuses rather than stripping: silently
    stripping would leave the caller believing the key they typed is in use when
    it was quietly discarded, and would still have written the secret into the
    request log on the way in.
    """
    error = refusal(url, production_config)
    assert error.code == errors.MALFORMED_URL
    assert "username and password" in error.message
    assert error.hint and "key field" in error.hint
    # The submitted credential is not echoed back in the diagnostic payload.
    assert "sk-live-abcdefghijklmnop" not in str(error.details)


def test_a_url_longer_than_the_enforced_limit_is_refused(production_config):
    """A URL is a request for our server to do work, so its size is attacker-controlled.

    The module enforces a 2048-character cap rather than documenting a number it
    does not check; past that, the refusal must be a clean URL error and not a
    traceback out of the parser.
    """
    base = "https://%s/" % PUBLIC_IP
    error = refusal(base + "a" * (2049 - len(base)), production_config)
    assert error.code == errors.MALFORMED_URL
    assert "too long" in error.message.lower()
    assert error.hint


def test_a_url_at_the_limit_is_accepted(production_config):
    """The cap must start exactly where it claims, or good URLs get refused.

    An off-by-one in a length check is invisible in production until a provider
    with a long query string stops working, and then it looks like a provider
    problem rather than a validation bug.
    """
    base = "https://%s/" % PUBLIC_IP
    url = base + "a" * (2048 - len(base))
    assert len(url) == 2048
    target = validate_endpoint(url, production_config)
    assert target.host == PUBLIC_IP
    assert target.path.startswith("/aaa")


@pytest.mark.parametrize("url", ["https://%s:abc/v1" % PUBLIC_IP, "https://%s:99999/v1" % PUBLIC_IP])
def test_a_port_that_is_not_a_number_or_out_of_range_is_refused(url, production_config):
    """A malformed authority must fail as a URL error, never as a socket error.

    Letting it through means the failure surfaces later as a connection refused
    deep inside a worker, where the user sees a generic provider error instead of
    the typo they could fix.
    """
    error = refusal(url, production_config)
    assert error.code == errors.MALFORMED_URL


def test_the_policy_refuses_ports_outside_the_valid_range():
    """check_port is the reusable half of the same check and must agree with it.

    The connection form validates before anything is stored by calling the policy
    directly, so a range bug that only existed in check_port would let a bad URL be
    saved and fail on the first generation instead.
    """
    bare = EgressPolicy()
    for bad in (0, -1, 65536, 70000):
        with pytest.raises(ApiError) as caught:
            bare.check_port(bad, host="example.invalid")
        assert caught.value.code == errors.MALFORMED_URL
    assert bare.check_port(65535, host="example.invalid") is None
    assert bare.check_port(1, host="example.invalid") is None


# ---------------------------------------------------------------------------
# studio.net -- the error contract, and "refused before the network"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected_code",
    [
        ("https://169.254.169.254/latest/meta-data/", errors.NETWORK_RESTRICTED),
        ("https://127.0.0.1/v1", errors.NETWORK_RESTRICTED),
        ("https://metadata.google.internal/", errors.NETWORK_RESTRICTED),
        ("http://%s/v1" % PUBLIC_IP, errors.NETWORK_RESTRICTED),
        ("https://user:pw@%s/v1" % PUBLIC_IP, errors.MALFORMED_URL),
        ("file:///etc/passwd", errors.MALFORMED_URL),
        ("https://%s:99999/v1" % PUBLIC_IP, errors.MALFORMED_URL),
        ("gopher://%s/" % PUBLIC_IP, errors.MALFORMED_URL),
    ],
)
def test_every_refusal_carries_a_documented_code_a_status_and_a_hint(url, expected_code, production_config):
    """The client branches on the code, and the user acts on the hint.

    docs/API.md promises that identical mistakes produce identical codes, and the
    UI promises that the user is told what to do next. Both promises are only real
    if every path through the guard honours them -- a bare exception, or a code
    invented on the spot, breaks the client silently.
    """
    error = refusal(url, production_config)
    assert error.code == expected_code
    assert error.code in DOCUMENTED_EGRESS_CODES
    assert error.status == 400
    assert isinstance(error.hint, str) and error.hint.strip()


def test_a_refused_url_never_opens_a_socket(monkeypatch, production_config):
    """Refusal has to happen at validation time, not after a connection attempt.

    If the check ran after connect, a refused URL would still have performed a TCP
    handshake -- enough to fingerprint the network, and enough to reach a service
    that acts on the connection itself. Patching create_connection to explode
    proves the guard wins the race.
    """

    def _explode(*args, **kwargs):  # pragma: no cover - reached only on a bug
        raise AssertionError("the egress guard opened a socket: %r %r" % (args, kwargs))

    monkeypatch.setattr(socket, "create_connection", _explode)
    monkeypatch.setattr(socket.socket, "connect", _explode)

    for url in (
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/v1",
        "https://10.0.0.5/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "http://%s/v1" % PUBLIC_IP,
    ):
        with pytest.raises(ApiError) as caught:
            validate_endpoint(url, production_config)
        assert caught.value.code in DOCUMENTED_EGRESS_CODES


def test_the_http_client_refuses_a_blocked_url_without_connecting(monkeypatch, production_config):
    """HttpClient is the one place a request starts, so it must not be a way round the guard.

    validate_endpoint only checks; HttpClient is what a generation worker actually
    calls. If the two disagreed about what is blocked, the check would be theatre.
    """

    def _explode(*args, **kwargs):  # pragma: no cover - reached only on a bug
        raise AssertionError("HttpClient opened a socket to a blocked destination")

    monkeypatch.setattr(socket, "create_connection", _explode)
    client = HttpClient(config=production_config)

    for url, code in (
        ("https://169.254.169.254/latest/meta-data/", errors.NETWORK_RESTRICTED),
        ("https://127.0.0.1/v1", errors.NETWORK_RESTRICTED),
        ("file:///etc/passwd", errors.MALFORMED_URL),
    ):
        with pytest.raises(ApiError) as caught:
            client.request("GET", url)
        assert caught.value.code == code


def test_parse_target_can_validate_without_resolving(production_config):
    """The connection form validates before storing, and can do it without DNS.

    resolve=False is the shape for "is this URL acceptable" when the process must
    stay offline. It still enforces the scheme, hostname, port and credential
    rules, and it reports no address because no lookup happened -- the caller is
    expected to re-validate with resolution before the URL is used.
    """
    built = EgressPolicy.from_config(production_config)
    target = parse_target("https://%s:8443/v1?q=1" % PUBLIC_IP, built, resolve=False)
    assert target.address == ""
    assert target.addresses == ()
    assert target.host == PUBLIC_IP
    assert target.port == 8443
    assert target.path == "/v1?q=1"
    assert target.url == "https://%s:8443/v1?q=1" % PUBLIC_IP

    # The rules that do not need an address are still applied.
    with pytest.raises(ApiError):
        parse_target("file:///etc/passwd", built, resolve=False)
    with pytest.raises(ApiError):
        parse_target("https://user:pw@%s/v1" % PUBLIC_IP, built, resolve=False)


def test_without_resolution_the_address_check_still_runs_for_literals(production_config):
    '''The early door must not be a weaker door.

    parse_target(resolve=False) skips DNS, and it used to skip the private
    address check with it -- that check lives in policy.resolve(). A caller
    taking that path would accept https://169.254.169.254/ and store it, and the
    first real request would then refuse a URL the platform itself had
    validated. An IP literal has nothing to resolve, so it is checked on both
    paths now.
    '''
    built = EgressPolicy.from_config(production_config)

    for url in ('https://127.0.0.1/v1', 'https://10.0.0.5/v1', 'https://169.254.169.254/latest/'):
        with pytest.raises(ApiError) as unresolved:
            parse_target(url, built, resolve=False)
        assert unresolved.value.code == errors.NETWORK_RESTRICTED
        with pytest.raises(ApiError) as resolved:
            parse_target(url, built, resolve=True)
        assert resolved.value.code == errors.NETWORK_RESTRICTED

    # A public literal is accepted on both paths, so the check is not blanket.
    assert parse_target('https://%s/v1' % PUBLIC_IP, built, resolve=False).host == PUBLIC_IP


# ---------------------------------------------------------------------------
# studio.crypto -- envelope sealing
# ---------------------------------------------------------------------------

needs_crypto = pytest.mark.skipif(
    not CRYPTO_AVAILABLE, reason="the cryptography package is not installed in this runtime"
)

PRIMARY_KEY = "studio-primary-key-for-tests-0001"
PREVIOUS_KEY = "studio-previous-key-for-tests-0002"
SCOPE = "workspace:w1|connection:c1|field:api_key"


@needs_crypto
def test_a_sealed_secret_opens_back_to_the_same_plaintext():
    """The envelope is only useful if the credential survives the round trip.

    Everything downstream -- connection tests, generations, rotation -- decrypts a
    stored value and uses it as a bearer token, so one byte of drift in the
    encoding is a platform where no provider works.
    """
    box = SecretBox(PRIMARY_KEY)
    sealed = box.seal("sk-the-real-api-key", SCOPE)
    assert box.open(sealed.ciphertext, SCOPE) == "sk-the-real-api-key"
    # Non-ASCII has to survive too: provider keys are ASCII but text is not.
    assert box.open(box.seal("cle-nda-umlaut-key", SCOPE).ciphertext, SCOPE) == "cle-nda-umlaut-key"


@needs_crypto
def test_the_envelope_is_prefixed_and_carries_no_plaintext():
    """A stored credential must be useless to whoever reads the database.

    The prefix is the contract other code branches on (it identifies a sealed
    value apart from a legacy row), and the absence of the plaintext is the entire
    threat model: a leaked backup, a read replica, a SELECT-only injection.
    """
    plaintext = "sk-super-secret-value-0123456789"
    sealed = SecretBox(PRIMARY_KEY).seal(plaintext, SCOPE)
    assert sealed.ciphertext.startswith("senc.1.")
    assert plaintext not in sealed.ciphertext
    assert sealed.key_id in sealed.ciphertext
    assert sealed.algorithm == "AES-256-GCM"
    prefix, version, key_id, payload = sealed.ciphertext.split(".")
    assert (prefix, version) == ("senc", "1")
    assert len(key_id) == 16
    assert all(character not in payload for character in "+/=")


@needs_crypto
def test_two_seals_of_the_same_plaintext_differ():
    """A fresh nonce per write is what makes AES-GCM safe under one key.

    Reusing a nonce with the same key does not merely leak that two rows match: it
    lets an attacker recover the authentication key and forge arbitrary
    ciphertexts. Identical output for identical input is the visible symptom of
    that bug, and exactly what a "cache the seal" optimisation would cause.
    """
    box = SecretBox(PRIMARY_KEY)
    first = box.seal("same-plaintext", SCOPE)
    second = box.seal("same-plaintext", SCOPE)
    assert first.ciphertext != second.ciphertext
    assert box.open(first.ciphertext, SCOPE) == box.open(second.ciphertext, SCOPE) == "same-plaintext"


@needs_crypto
@pytest.mark.parametrize(
    "mutate",
    [
        lambda parts: [parts[0], parts[1], parts[2], ("A" if parts[3][0] != "A" else "B") + parts[3][1:]],
        lambda parts: [parts[0], parts[1], parts[2], parts[3][:-1]],
        lambda parts: [parts[0], parts[1], parts[2], parts[3] + "AAAA"],
        lambda parts: [parts[0], parts[1], "0" * 16, parts[3]],
    ],
    ids=["first-byte-flipped", "last-character-dropped", "trailing-bytes-added", "key-id-swapped"],
)
def test_a_tampered_envelope_raises_secret_unreadable(mutate):
    """A modified record must fail closed, audibly, rather than decrypt to something.

    AES-GCM's tag is the only reason this can be promised: without it, an attacker
    with UPDATE access could aim a ciphertext at a value they control and the
    platform would happily use it as a provider key.
    """
    box = SecretBox(PRIMARY_KEY)
    sealed = box.seal("sk-original", SCOPE)
    tampered = ".".join(mutate(sealed.ciphertext.split(".")))
    with pytest.raises(SecretUnreadable) as caught:
        box.open(tampered, SCOPE)
    assert caught.value.code == "credential_unreadable"
    assert caught.value.status == 409
    assert caught.value.hint


@needs_crypto
def test_an_envelope_cannot_be_moved_to_another_scope():
    """The scope is authenticated data, which is what stops a ciphertext being pasted elsewhere.

    Without the binding, anyone with UPDATE on one row could copy another
    workspace's sealed key into their own connection and spend that customer's
    credit. The scope makes the copy decrypt to an authentication failure instead.
    """
    box = SecretBox(PRIMARY_KEY)
    sealed = box.seal("sk-workspace-one", "workspace:w1|connection:c1|field:api_key")
    with pytest.raises(SecretUnreadable):
        box.open(sealed.ciphertext, "workspace:w2|connection:c9|field:api_key")


@needs_crypto
def test_sealing_requires_a_scope():
    """A seal with no scope would be a seal with no binding.

    Refusing at seal time is what keeps the guarantee from depending on every
    caller remembering to pass one; an empty string slipping through would quietly
    produce a credential that can be moved between records.
    """
    with pytest.raises(ValueError):
        SecretBox(PRIMARY_KEY).seal("sk-no-scope", "")


@needs_crypto
@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-an-envelope",
        "senc.2.0123456789abcdef.AAAA",
        "senc.1.0123456789abcdef.!!!not-base64!!!",
        "senc.1.0123456789abcdef.",
    ],
    ids=["empty", "no-prefix", "wrong-version", "not-base64", "no-payload"],
)
def test_structurally_broken_envelopes_are_unreadable_not_crashes(stored):
    """A malformed row must produce one actionable error, not five different tracebacks.

    Rows get corrupted by partial restores and hand-edited databases, and the UI
    has exactly one branch for this case: "replace the key". Anything else reaches
    the user as a stack trace they cannot act on.
    """
    with pytest.raises(SecretUnreadable) as caught:
        SecretBox(PRIMARY_KEY).open(stored, SCOPE)
    assert caught.value.code == "credential_unreadable"


@needs_crypto
def test_a_missing_key_is_unavailable_rather_than_quietly_weaker():
    """No key material must mean "refuse to store", never "store it anyway".

    The module says so explicitly: degrading to something weaker when the cipher
    library is missing would turn a configuration mistake into permanent, silent
    exposure of every credential written in the meantime.
    """
    with pytest.raises(EncryptionUnavailable) as caught:
        SecretBox("")
    assert caught.value.status == 503
    assert "cryptography" in caught.value.hint


# ---------------------------------------------------------------------------
# studio.crypto -- rotation
# ---------------------------------------------------------------------------


@needs_crypto
def test_needs_rotation_is_false_for_the_primary_and_true_for_an_older_key():
    """Rotation is driven by this flag, so it must be a property of the envelope alone.

    It is computed from the public key id without decrypting anything: that is
    what lets a rotation pass scan a large table cheaply and re-seal only the rows
    that are actually stale.
    """
    old_sealed = SecretBox(PREVIOUS_KEY).seal("old-value", SCOPE)
    rotating = SecretBox(PRIMARY_KEY, previous=[PREVIOUS_KEY])

    fresh = rotating.seal("new-value", SCOPE)
    assert rotating.needs_rotation(fresh.ciphertext) is False
    assert rotating.needs_rotation(old_sealed.ciphertext) is True
    assert rotating.primary_key_id == fresh.key_id
    assert set(rotating.known_key_ids) == {fresh.key_id, old_sealed.key_id}


@needs_crypto
def test_a_box_built_with_previous_keys_still_opens_the_old_envelope():
    """A rotation that broke every existing connection would be unusable in practice.

    Credentials are entered by the customer and re-entering them is the expensive
    part, so the new key must be additive: old rows keep working until the
    background re-seal reaches them.
    """
    old_sealed = SecretBox(PREVIOUS_KEY).seal("sk-still-valid", SCOPE)
    rotating = SecretBox(PRIMARY_KEY, previous=[PREVIOUS_KEY])
    assert rotating.open(old_sealed.ciphertext, SCOPE) == "sk-still-valid"


@needs_crypto
def test_a_box_without_the_old_key_says_the_key_is_gone():
    """Dropping a key must be a legible failure, not a corrupted-record failure.

    "Re-enter the API key" is something a user can do; "corrupt" sends them to
    support. The two situations look identical from the ciphertext, so the message
    has to be chosen deliberately.
    """
    old_sealed = SecretBox(PREVIOUS_KEY).seal("sk-retired", SCOPE)
    with pytest.raises(SecretUnreadable) as caught:
        SecretBox(PRIMARY_KEY).open(old_sealed.ciphertext, SCOPE)
    assert "no longer holds" in caught.value.message


@needs_crypto
def test_blank_and_duplicate_previous_keys_are_ignored():
    """Operator config is pasted text; a trailing comma must not become a bogus key.

    A blank entry that reached derive_key would either raise through the whole
    rotation or add a meaningless id to known_key_ids, and the second is worse: the
    rotation report would claim two keys are live when only one is.
    """
    box = SecretBox(PRIMARY_KEY, previous=["", "   ", PRIMARY_KEY])
    assert box.known_key_ids == [box.primary_key_id]


# ---------------------------------------------------------------------------
# studio.crypto -- password hashing
# ---------------------------------------------------------------------------


def test_hash_password_verifies_the_right_password_and_rejects_a_wrong_one():
    """Password verification is the whole of account security for this product.

    A false accept is account takeover; a false reject locks a customer out of
    work they have already paid for. Both directions are asserted, with the
    near-miss case (a trailing space) included because it is the one a
    "normalise and compare" refactor breaks first.
    """
    password = "correct horse battery staple"
    stored = hash_password(password)
    assert verify_password(password, stored) is True
    assert verify_password("correct horse battery stapl", stored) is False
    assert verify_password(password + " ", stored) is False
    assert verify_password("", stored) is False


def test_a_stored_password_hash_contains_no_recoverable_password():
    """A leaked user table must not hand out the passwords reused elsewhere.

    The record names its own parameters so they can be raised later, and the salt
    is per-hash so two users with the same password do not collide.
    """
    password = "another-fine-password"
    first = hash_password(password)
    second = hash_password(password)
    assert password not in first
    assert first != second
    assert first.startswith("scrypt$")
    assert password_needs_rehash(first) is False
    assert verify_password(password, first) and verify_password(password, second)


@pytest.mark.parametrize(
    "stored",
    ["", "nonsense", "bcrypt$1$2$3$abc$def", "scrypt$x$y$z$a$b", "scrypt$1$2$3"],
)
def test_a_malformed_password_record_verifies_as_false(stored):
    """Bad data must deny access, not raise and not accidentally match.

    An exception here would 500 the login endpoint for one unlucky account; a
    truthy return would be far worse. There is exactly one acceptable answer.
    """
    assert verify_password("anything", stored) is False
    assert password_needs_rehash(stored) is True


def test_a_corrupt_record_with_current_parameters_is_not_flagged_for_rehash():
    """BEHAVIOUR NOTE: needs_rehash compares parameters only; it does not validate.

    password_needs_rehash() reads the three cost parameters and compares them with
    the current ones. A record whose parameters are current but whose salt or
    digest is not decodable therefore reports False -- "no rehash needed" -- even
    though verify_password() rejects it. The pair is only safe because the auth
    path verifies first and rehashes only after a successful verify, but on its own
    the flag is a weaker signal than its name suggests.
    """
    corrupt = "scrypt$32768$8$1$notbase64$notbase64"
    assert verify_password("anything", corrupt) is False
    assert password_needs_rehash(corrupt) is False


# ---------------------------------------------------------------------------
# studio.redact -- free text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret, text",
    [
        ("sk-abcdefghijklmnopqrstuvwx", "the provider said your key sk-abcdefghijklmnopqrstuvwx is invalid"),
        ("sk-proj-AbCdEfGhIjKlMnOpQrStUvWx", "request failed for sk-proj-AbCdEfGhIjKlMnOpQrStUvWx"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstu", "quota exceeded for AIzaSyA1234567890abcdefghijklmnopqrstu"),
        ("hf_abcdefghijklmnopqrstuvwxyz", "token hf_abcdefghijklmnopqrstuvwxyz was rejected"),
        ("ghp_abcdefghijklmnopqrstuvwxyz", "clone failed with ghp_abcdefghijklmnopqrstuvwxyz"),
    ],
)
def test_known_provider_key_shapes_are_scrubbed_from_free_text(secret, text):
    """An unlabelled key inside a message is the leak nobody notices.

    Provider error bodies echo the key they rejected, and that text goes straight
    into a log line and into the job's stored error. Recognising the shapes of the
    keys we actually accept is the only way to catch one that arrives without a
    label attached.
    """
    out = scrub(text)
    assert secret not in out
    assert "[redacted]" in out


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop",
        'Authorization: "Bearer abcdefghijklmnop"',
        "api_key=abcdefghijklmnop",
        "x-api-key: abcdefghijklmnop",
        "api-key=abcdefghijklmnop",
    ],
)
def test_labelled_credential_assignments_are_scrubbed(text):
    """A labelled credential is how a key most often reaches a log.

    It arrives in two places: a request header we are about to log, and the
    provider's own error text, which frequently quotes the header back. Both are
    covered by the same labelled pattern, and the label is kept so the line still
    reads as a diagnosis.
    """
    out = scrub(text)
    assert "abcdefghijklmnop" not in out
    assert "[redacted]" in out


@pytest.mark.parametrize(
    "text, needle",
    [
        (
            "https://bucket.s3.amazonaws.com/out.mp4?X-Amz-Signature=deadbeefdeadbeef&X-Amz-Credential=AKIAXYZ",
            "deadbeef",
        ),
        ("https://cdn.example.com/a.mp4?sig=abc123def456&expires=99", "abc123def456"),
        ("https://cdn.example.com/a.mp4?access_token=tok_abcdefghijkl", "tok_abcdefghijkl"),
    ],
)
def test_signed_url_query_parameters_are_scrubbed(text, needle):
    """A pre-signed URL is a bearer credential with an expiry date.

    Everything the platform downloads arrives as one of these, and logging "the
    URL that failed" without stripping the signature would publish working access
    to the object for the life of the signature -- often the life of the log.
    """
    out = scrub(text)
    assert needle not in out
    assert "[redacted]" in out
    # The host survives, because that is the part worth debugging.
    assert "cdn.example.com" in out or "bucket.s3.amazonaws.com" in out


@pytest.mark.parametrize(
    "text",
    [
        "generated 3 images in 4.2 seconds",
        "the model token count is 100 and the tokenizer split 12 pieces",
        "job 9f2c queued for workspace w1",
        "GET /api/jobs?limit=20 -> 200",
        "the monkey sat on the keyboard",
        "password reset email requested for ada@example.com",
    ],
)
def test_ordinary_text_survives_the_scrubber_unchanged(text):
    """A scrubber that mangles normal text makes the logs worthless.

    The patterns are deliberately broad -- over-redaction is accepted -- but the
    everyday vocabulary of this product ("token count", "keyframe", "password
    reset") has to survive, or nobody can debug anything and the scrubber gets
    quietly removed.
    """
    assert scrub(text) == text


def test_scrubbing_is_idempotent():
    """The logger, the error renderer and the provider normaliser all call scrub.

    A line passes through more than one layer, so scrubbing must be a fixed point:
    re-scrubbing an already-scrubbed string must not eat the surrounding text.
    """
    once = scrub("api_key=abcdefghijklmnop and sk-abcdefghijklmnopqrstuvwx")
    assert scrub(once) == once


def test_a_registered_secret_is_scrubbed_where_no_pattern_matches():
    """"Remember this exact string" is the only pattern that is always correct.

    When a credential is decrypted for use it is registered, so a leak in a shape
    we did not anticipate is still caught. Forgetting the registry must restore the
    text, which proves the registry -- not an accidental pattern match -- did the
    work.
    """
    registered = "opaque-value-with-no-recognisable-shape"
    assert scrub("using " + registered) == "using " + registered
    register_secret(registered)
    assert registered not in scrub("using " + registered)
    forget_all_secrets()
    assert scrub("using " + registered) == "using " + registered


# ---------------------------------------------------------------------------
# studio.redact -- structures
# ---------------------------------------------------------------------------


def test_a_nested_structure_loses_every_sensitive_field():
    """Provider responses and request echoes are logged as structures, not strings.

    Redacting the leaves but keeping a field named "token" would leave the value
    intact; redacting the field but not the leaves would leave a key echoed inside
    a message. Both layers are needed, and both are asserted here on the same
    nested payload a real provider response produces.
    """
    payload = {
        "api_key": "sk-proj-1234567890abcdefghijkl",
        "nested": {
            "password": "hunter2",
            "authorization": "Bearer abcdefghijkl",
            "token": "abcdefghijklmnop",
            "model": "flux-pro",
        },
        "list": [
            {"secret": "topsecretvalue"},
            "plain text carrying sk-proj-ABCDEFGHIJKLMNOP",
        ],
        "name": "My connection",
    }
    out = scrub_structure(payload)

    assert out["api_key"] == "[redacted]"
    assert out["nested"]["password"] == "[redacted]"
    assert out["nested"]["authorization"] == "[redacted]"
    assert out["nested"]["token"] == "[redacted]"
    assert out["list"][0]["secret"] == "[redacted]"
    assert "sk-proj-ABCDEFGHIJKLMNOP" not in out["list"][1]

    # Everything that is not a secret is preserved: redaction must not destroy
    # the diagnostic value of the payload it is protecting.
    assert out["nested"]["model"] == "flux-pro"
    assert out["name"] == "My connection"
    assert isinstance(out, dict) and isinstance(out["list"], list)


def test_benign_key_lookalikes_are_not_redacted():
    """A field named "keys" or "keyframe" carries no secret, and blanking it hurts.

    These names were excused one at a time after being seen in real provider
    payloads. The narrowness is the point: a blanket "any name containing key"
    rule turns a useful diagnostic into a redacted guess.
    """
    payload = {
        "keys": ["a", "b"],
        "keyframe": 12,
        "key_last4": "abcd",
        "model_key": "flux-pro",
        "has_key": True,
        "seed_key": "s1",
    }
    assert scrub_structure(payload) == payload
    # The predicate behind it agrees, and bare "key" is treated as sensitive.
    assert is_sensitive_key("keys") is False
    assert is_sensitive_key("keyframe") is False
    assert is_sensitive_key("key") is True
    assert is_sensitive_key("api_key") is True


def test_an_empty_sensitive_field_is_left_empty_rather_than_marked():
    """An empty credential is information: it says the connection has no key yet.

    Replacing it with "[redacted]" would make "this connection is incomplete" look
    like "this connection has a secret", which is the opposite of what the caller
    needs to know.
    """
    out = scrub_structure({"password": "", "token": None, "api_key": "x"})
    assert out["password"] == ""
    assert out["token"] is None
    assert out["api_key"] == "[redacted]"


def test_a_deep_payload_is_capped_instead_of_followed():
    """Logging must not become a denial of service when handed a deep payload.

    Provider responses are not ours. A depth cap and a width cap keep one hostile
    response from turning a log call into unbounded work or an unbounded line.
    """
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": "deep"}}}}}}}}}}

    def _deepest(node):
        while isinstance(node, dict) and node:
            node = list(node.values())[0]
        return node

    assert _deepest(scrub_structure(deep)) == "<depth limit>"
    assert len(scrub_structure({"items": list(range(60))})["items"]) == 50


def test_free_text_password_assignments_are_not_scrubbed():
    """BEHAVIOUR NOTE: scrub() alone does not redact "password=hunter2" in a string.

    The module docstring says everything the platform emits passes through
    scrub(), and the sensitive-key list includes "password". That list only applies
    during *structural* scrubbing: in free text the redactor recognises credential
    *shapes* (sk-, AIza, hf_, signed query parameters, and the labelled
    authorization / api-key forms) but not a bare password assignment. A password
    reaching a free-text log line therefore survives.

    The exposure is bounded in practice -- passwords are only handled by the auth
    paths, which log field names rather than values -- but the claim "everything
    the platform emits passes through scrub()" is weaker than it reads. This test
    records the behaviour instead of pretending otherwise.
    """
    for text in (
        "password=hunter2",
        "password: hunter2",
        '{"password": "hunter2"}',
        "the password is hunter2",
        "client_secret=abcdefghijklmnop",
        "secret=abcdefghijklmnop",
    ):
        assert scrub(text) == text
    # Through a structure, the same field is redacted -- that is where the
    # "password" hint actually applies.
    assert scrub_structure({"password": "hunter2"})["password"] == "[redacted]"


# ---------------------------------------------------------------------------
# studio.redact -- URLs and headers
# ---------------------------------------------------------------------------


def test_redact_url_removes_userinfo_and_signature_but_keeps_the_host():
    """The point of logging a URL is to know which host answered.

    Everything else in it is either a credential (userinfo, a signed query) or
    noise. Keeping the host is what makes the line diagnosable; dropping the
    signature is what makes it safe to keep.
    """
    redacted = redact_url("https://user:pass@api.example.com/v1?X-Amz-Signature=abc123def&other=1")
    assert "user:pass@" not in redacted
    assert "[userinfo]" in redacted
    assert "abc123def" not in redacted
    assert "api.example.com" in redacted

    plain = "https://api.example.com/v1/models"
    assert redact_url(plain) == plain
    assert redact_url("") == ""


def test_scrub_headers_redacts_credential_headers_entirely():
    """Headers are logged as a diagnostic, and one of them is the credential.

    Header names are also matched structurally, because a provider may use a
    custom header (x-api-key) as easily as Authorization, and a name-based rule
    covers both without needing the value to look like anything.
    """
    out = scrub_headers(
        {
            "Authorization": "Bearer abcdefghijklmnop",
            "X-Api-Key": "abcdefghijklmnop",
            "Proxy-Authorization": "Basic abcdefghijklmnop",
            "Content-Type": "application/json",
            "X-Request-Id": "abc",
        }
    )
    assert out["Authorization"] == "[redacted]"
    assert out["X-Api-Key"] == "[redacted]"
    assert out["Proxy-Authorization"] == "[redacted]"
    assert out["Content-Type"] == "application/json"
    assert out["X-Request-Id"] == "abc"
