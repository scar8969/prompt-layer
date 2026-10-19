"""Authenticated encryption for provider credentials, and password hashing.

Threat model this file addresses
--------------------------------
The database is assumed to be readable by someone who should not have the keys:
a backup that leaked, a read replica someone queried, a SQL injection that only
gets SELECT. Therefore:

* Provider API keys are stored **only** as AES-256-GCM ciphertext.
* The key-encryption key lives in the environment (or a 0600 file beside the
  data directory), never in the database and never in the repository.
* The additional authenticated data binds each ciphertext to the exact record it
  belongs to -- connection id, workspace id and field name -- so a ciphertext
  lifted from one row cannot be pasted into another to make the platform use
  someone else's key.
* A separate random nonce per write. Nonces are never derived from the data.
* Two long-lived keys means rotation is a config change plus a re-encrypt pass,
  not a migration of plaintext through memory.

Why a third-party library here and not in Prompt Layer's own code
----------------------------------------------------------------
Prompt Layer is deliberately standard-library-only and a test enforces it. This
package is a separate subsystem with different requirements: hand-rolling AES-GCM
is exactly the kind of decision that looks clever and ends with a padding oracle.
`cryptography` is a vetted implementation with wheels for the deployment target.
If it is unavailable the platform refuses to store credentials rather than
quietly degrading to something weaker -- see :class:`EncryptionUnavailable`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .errors import ApiError, INTERNAL

try:  # pragma: no cover - exercised by both branches in the test suite
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover
    CRYPTO_AVAILABLE = False

    class InvalidTag(Exception):  # type: ignore[no-redef]
        """Placeholder so the except clauses below remain valid."""


ENVELOPE_PREFIX = "senc"
ENVELOPE_VERSION = "1"
NONCE_BYTES = 12
KEY_BYTES = 32
#: Fixed context string. Changing it invalidates every stored credential, so it
#: is a constant on purpose and not a parameter.
_HKDF_INFO = b"studio-platform/credential-envelope/v1"
_PASSWORD_CONTEXT = b"studio-platform/password/v1"

#: Password hashing parameters. Memory-hard, tuned for roughly 100 ms on a
#: modern core; stored per-hash so they can be raised without invalidating
#: existing passwords.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2


class EncryptionUnavailable(ApiError):
    """No usable cipher is present, so credentials cannot be stored safely."""

    def __init__(self) -> None:
        super().__init__(
            INTERNAL,
            "This deployment cannot store provider credentials: no authenticated "
            "cipher is available in the runtime.",
            status=503,
            hint="Install the 'cryptography' package (see requirements.txt) and restart.",
        )


@dataclass(frozen=True)
class SealedSecret:
    """A credential's stored form. The plaintext never appears on this object."""

    ciphertext: str
    key_id: str
    algorithm: str = "AES-256-GCM"

    def __str__(self) -> str:  # pragma: no cover - defensive
        return "<SealedSecret key_id=%s>" % self.key_id


def _decode_key_material(raw: str) -> bytes:
    """Accept the formats an operator is realistically going to paste.

    A 64-character hex string and a 44-character base64 string are both 32 bytes
    of key. Anything else is treated as a passphrase and stretched.
    """
    candidate = raw.strip()
    if len(candidate) == 64:
        try:
            decoded = bytes.fromhex(candidate)
            if len(decoded) == KEY_BYTES:
                return decoded
        except ValueError:
            pass
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = candidate + "=" * (-len(candidate) % 4)
            decoded = decoder(padded)
            if len(decoded) == KEY_BYTES:
                return decoded
        except Exception:
            continue
    return b""


def derive_key(raw: str) -> Tuple[bytes, str]:
    """Return (32-byte key, short stable key id derived from it).

    The key id is a public fingerprint. It is what lets a rotation pass tell
    which rows are still under an old key without decrypting anything.
    """
    if not raw:
        raise EncryptionUnavailable()
    material = _decode_key_material(raw)
    if not material:
        # A passphrase: stretch deterministically so restarts can still read.
        material = hashlib.scrypt(
            raw.encode("utf-8"),
            salt=_HKDF_INFO,
            n=2 ** 14,
            r=8,
            p=1,
            dklen=KEY_BYTES,
            maxmem=128 * 2 ** 14 * 8 * 2,
        )
    if CRYPTO_AVAILABLE:
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=None,
            info=_HKDF_INFO,
        ).derive(material)
    else:
        key = hmac.new(_HKDF_INFO, material, hashlib.sha256).digest()[:KEY_BYTES]
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


class SecretBox:
    """Seals and opens credentials under a primary key with optional old keys."""

    def __init__(self, primary: str, previous: Optional[List[str]] = None) -> None:
        self._keys: Dict[str, bytes] = {}
        self._primary_id = ""
        if not CRYPTO_AVAILABLE:
            raise EncryptionUnavailable()
        key, key_id = derive_key(primary)
        self._keys[key_id] = key
        self._primary_id = key_id
        for old in previous or []:
            if not old.strip():
                continue
            try:
                old_key, old_id = derive_key(old)
            except EncryptionUnavailable:
                continue
            if old_id != key_id:
                self._keys.setdefault(old_id, old_key)

    @property
    def primary_key_id(self) -> str:
        return self._primary_id

    @property
    def known_key_ids(self) -> List[str]:
        return sorted(self._keys)

    def _aad(self, scope: str) -> bytes:
        """Additional authenticated data.

        `scope` is required to be a non-empty, structurally meaningful string
        (workspace, connection, field). It is authenticated but not encrypted,
        which is what makes a cut-and-paste between rows fail closed.
        """
        if not scope:
            raise ValueError("credential scope must not be empty")
        return ("studio.v1|" + scope).encode("utf-8")

    def seal(self, plaintext: str, scope: str) -> SealedSecret:
        if plaintext is None:
            raise ValueError("nothing to seal")
        key = self._keys[self._primary_id]
        nonce = os.urandom(NONCE_BYTES)
        blob = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), self._aad(scope))
        encoded = base64.urlsafe_b64encode(nonce + blob).decode("ascii").rstrip("=")
        return SealedSecret(
            ciphertext="%s.%s.%s.%s" % (ENVELOPE_PREFIX, ENVELOPE_VERSION, self._primary_id, encoded),
            key_id=self._primary_id,
        )

    def open(self, stored: str, scope: str) -> str:
        """Decrypt, or raise :class:`SecretUnreadable` without leaking which step failed."""
        if not stored:
            raise SecretUnreadable("the stored credential is empty")
        parts = stored.split(".")
        if len(parts) != 4 or parts[0] != ENVELOPE_PREFIX or parts[1] != ENVELOPE_VERSION:
            raise SecretUnreadable("the stored credential is not in a recognised format")
        key_id = parts[2]
        key = self._keys.get(key_id)
        if key is None:
            # A rotation that removed the old key. Actionable, not mysterious.
            raise SecretUnreadable(
                "the credential was sealed with a key this deployment no longer holds; "
                "re-enter the API key to re-seal it"
            )
        try:
            raw = base64.urlsafe_b64decode(parts[3] + "=" * (-len(parts[3]) % 4))
        except Exception:
            raise SecretUnreadable("the stored credential is corrupt")
        if len(raw) <= NONCE_BYTES:
            raise SecretUnreadable("the stored credential is corrupt")
        nonce, blob = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        try:
            plaintext = AESGCM(key).decrypt(nonce, blob, self._aad(scope))
        except InvalidTag:
            raise SecretUnreadable(
                "the credential failed authentication; it may have been modified, "
                "or copied from another record"
            )
        return plaintext.decode("utf-8")

    def needs_rotation(self, stored: str) -> bool:
        """True when `stored` was sealed under a non-primary key."""
        parts = stored.split(".")
        if len(parts) != 4:
            return True
        return parts[2] != self._primary_id


class SecretUnreadable(ApiError):
    """A stored credential cannot be used, with a reason the user can act on."""

    def __init__(self, message: str) -> None:
        super().__init__(
            "credential_unreadable",
            message,
            status=409,
            hint="Open the connection and replace its API key.",
        )


# --- password hashing -------------------------------------------------------


def hash_password(password: str) -> str:
    """scrypt, with parameters stored alongside so they can be raised later."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
        maxmem=SCRYPT_MAXMEM,
    )
    return "scrypt$%d$%d$%d$%s$%s" % (
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. A malformed record verifies as False."""
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=max(SCRYPT_MAXMEM, 128 * n * r * 2),
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


def password_needs_rehash(stored: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, _, _ = stored.split("$")
        return (
            scheme != "scrypt"
            or int(n_raw) < SCRYPT_N
            or int(r_raw) < SCRYPT_R
            or int(p_raw) < SCRYPT_P
        )
    except Exception:
        return True


# --- opaque tokens ----------------------------------------------------------


def new_token(nbytes: int = 32) -> str:
    """A URL-safe random token. Used for session ids, CSRF and signed links."""
    return secrets.token_urlsafe(nbytes)


def token_fingerprint(token: str, secret: str) -> str:
    """Keyed digest of a token, for storage.

    Session tokens are stored hashed so a database dump cannot be replayed as a
    live session. The digest is keyed with the app secret so an attacker with
    only the dump cannot even do an offline dictionary attack on the token.
    """
    return hmac.new(
        secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def fingerprint_public(value: str, length: int = 8) -> str:
    """A short, non-reversible label for a secret: safe to show in a list."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length]
