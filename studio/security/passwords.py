"""Password policy and hashing.

Policy
------
A length floor, plus a small list of obviously-weak choices, and nothing else.
Composition rules ("one uppercase, one symbol") are deliberately absent: they
push people toward `Password1!` while making long passphrases fail. Length is
the property that actually helps, so length is what is enforced.

The floor is configuration, not a constant, because a self-hosted deployment may
have its own policy it must satisfy.

Hashing
-------
Delegated to :mod:`studio.crypto`, which uses scrypt with parameters stored
inside each hash. :func:`needs_rehash` compares a stored hash's parameters
against the current ones so the cost can be raised later without invalidating
anyone's password.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..crypto import (
    password_needs_rehash,
    hash_password as _hash_password,
    verify_password as _verify_password,
)

#: Not a blocklist of every leaked password -- that would need a large corpus and
#: would still be the wrong control. These are the strings that show up in the
#: first thousand attempts against any new service.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "letmein",
        "welcome",
        "welcome1",
        "qwertyuiop",
        "qwerty123",
        "iloveyou",
        "administrator",
        "changeme",
        "secret",
        "1234567890",
        "12345678901",
        "123456789012",
        "abcdefghij",
        "1111111111",
        "0000000000",
        "aaaabbbbcccc",
        "trustno1",
        "sunshine",
        "princess",
        "dragon123",
        "monkey123",
        "football",
        "baseball",
        "superman",
        "michael123",
        "starwars",
    }
)

MAX_PASSWORD_LENGTH = 512  # scrypt on a megabyte of input is a denial of service


@dataclass(frozen=True)
class PasswordProblem:
    """Why a password was rejected, and which field to attach it to."""

    code: str
    message: str
    field: str = "password"

    def to_detail(self) -> dict:
        return {"field": self.field, "message": self.message, "code": self.code}


def validate_password(password: str, *, min_length: int = 10) -> List[PasswordProblem]:
    """Return every problem with `password`. Empty list means acceptable.

    All problems are returned rather than the first, so the UI can tell someone
    everything that is wrong in one round instead of one message per attempt.
    """
    problems: List[PasswordProblem] = []
    if not password:
        problems.append(PasswordProblem("required", "Choose a password."))
        return problems
    if len(password) < min_length:
        problems.append(
            PasswordProblem(
                "too_short",
                "Use at least %d characters. A short phrase of a few words works well."
                % min_length,
            )
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(
            PasswordProblem(
                "too_long", "Use at most %d characters." % MAX_PASSWORD_LENGTH
            )
        )
    lowered = password.strip().lower()
    if lowered in COMMON_PASSWORDS:
        problems.append(
            PasswordProblem(
                "too_common",
                "That password appears in every list of commonly used passwords.",
            )
        )
    if _is_single_repeated_character(password):
        problems.append(
            PasswordProblem("repetitive", "That password is one character repeated.")
        )
    # A password equal to the email or display name is a real pattern, but
    # checking it here would need context this function does not have; the
    # account service does that check where it has both values.
    return problems


def _is_single_repeated_character(password: str) -> bool:
    stripped = password.strip()
    return len(set(stripped)) == 1


def describe_policy(min_length: int = 10) -> dict:
    """The policy as data, so the signup form can state the rule before failing."""
    return {
        "min_length": min_length,
        "max_length": MAX_PASSWORD_LENGTH,
        "rules": [
            {"code": "length", "minimum": min_length},
            {"code": "not_common", "description": "Not a widely used password."},
        ],
    }


def hash_password(password: str) -> str:
    return _hash_password(password)


def verify_password(password: str, stored: Optional[str]) -> bool:
    """Constant-time verification that treats a missing hash as a failed login.

    Returning False rather than raising keeps the caller's flow identical for a
    nonexistent user and a wrong password, which is what prevents the login
    endpoint from becoming a user-enumeration oracle.
    """
    if not stored:
        return False
    return _verify_password(password, stored)


def needs_rehash(stored: Optional[str]) -> bool:
    if not stored:
        return False
    return password_needs_rehash(stored)
