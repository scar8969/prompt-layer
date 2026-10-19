"""Rate limiting, counted in the database.

Why a table and not a dictionary
--------------------------------
A process-local counter resets on restart and is not shared between the web
process and the workers, so the limit it enforces is not the limit anyone would
claim. A row per (bucket, window) is a few bytes, survives a restart, is visible
to every process, and -- when someone asks why they were throttled -- can be
inspected with a query rather than argued about.

Fixed windows, deliberately
---------------------------
A sliding window would smooth bursts but needs a second table or a timestamped
event log. Fixed windows are what the two limits here actually need: an ordinary
per-address ceiling that stops a runaway script, and a much tighter one on
credential operations that stops password guessing. Both are about sustained
rate, not about microseconds of precision.

Buckets are opaque strings assembled by the caller, e.g. `ip:1.2.3.4:login`.
They are hashed before storage where they contain an address, so the table is
not a record of who connected from where.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..db import Database
from ..util import iso_in, now_iso


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: str
    retry_after_seconds: int
    bucket: str

    def headers(self) -> Dict[str, str]:
        """Standard rate-limit headers, so a client can back off correctly."""
        values = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.retry_after_seconds),
        }
        if not self.allowed:
            values["Retry-After"] = str(max(1, self.retry_after_seconds))
        return values

    def to_payload(self) -> Dict[str, Any]:
        return {
            "limit": self.limit,
            "remaining": max(0, self.remaining),
            "retry_after_seconds": max(1, self.retry_after_seconds),
            "reset_at": self.reset_at,
        }


class RateLimiter:
    """Fixed-window counters over the `rate_limits` table."""

    def __init__(self, db: Database, *, namespace: str = "studio") -> None:
        self._db = db
        self._namespace = namespace

    # -- bucket naming ------------------------------------------------------
    def bucket(self, *parts: str) -> str:
        """Build a bucket key, hashing anything that looks like an address.

        Hashing here rather than at the call site means a caller cannot forget:
        every bucket that reaches storage is already opaque.
        """
        cleaned = []
        for part in parts:
            text = str(part or "anonymous")
            if _looks_like_address(text):
                text = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
            cleaned.append(text)
        return self._namespace + ":" + ":".join(cleaned)

    # -- the check ----------------------------------------------------------
    def check(
        self,
        bucket: str,
        *,
        limit: int,
        window_seconds: int = 60,
        cost: int = 1,
    ) -> RateLimitResult:
        """Record one hit against `bucket` and report whether it is allowed.

        The counter is incremented even when the request is refused, so a
        determined client stays refused instead of receiving a fresh allowance
        the moment the window rolls over.
        """
        if limit <= 0:
            # A limit of zero means "this is disabled", not "refuse everything".
            return RateLimitResult(
                allowed=True,
                limit=0,
                remaining=0,
                reset_at=iso_in(window_seconds),
                retry_after_seconds=window_seconds,
                bucket=bucket,
            )
        now = int(time.time())
        window_start = now - (now % window_seconds)
        reset_at = window_start + window_seconds
        with self._db.write_tx() as tx:
            row = tx.query_one(
                "SELECT count FROM rate_limits WHERE bucket = ? AND window_start = ?",
                (bucket, window_start),
            )
            current = int(row["count"]) if row else 0
            already_over = current >= limit
            if row:
                tx.execute(
                    "UPDATE rate_limits SET count = count + ? WHERE bucket = ? AND window_start = ?",
                    (cost, bucket, window_start),
                )
            else:
                tx.execute(
                    "INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, ?)",
                    (bucket, window_start, cost),
                )
            # Opportunistic cleanup: one window's worth of stale rows at most, so
            # this never turns into a full table scan on the hot path.
            if window_start % 32 == 0:
                tx.execute(
                    "DELETE FROM rate_limits WHERE window_start < ?",
                    (window_start - max(window_seconds * 10, 3600),),
                )
        used = current + cost
        remaining = limit - used
        return RateLimitResult(
            allowed=not already_over,
            limit=limit,
            remaining=remaining,
            reset_at=iso_in(reset_at - now),
            retry_after_seconds=max(1, reset_at - now),
            bucket=bucket,
        )

    def peek(self, bucket: str, *, window_seconds: int = 60) -> int:
        """Read the current count without counting a hit. For diagnostics."""
        now = int(time.time())
        window_start = now - (now % window_seconds)
        row = self._db.query_one(
            "SELECT count FROM rate_limits WHERE bucket = ? AND window_start = ?",
            (bucket, window_start),
        )
        return int(row["count"]) if row else 0

    def reset(self, bucket: str, *, window_seconds: int = 60) -> None:
        """Clear a bucket. Used by tests and by an operator clearing a lockout."""
        now = int(time.time())
        window_start = now - (now % window_seconds)
        self._db.execute(
            "DELETE FROM rate_limits WHERE bucket = ? AND window_start = ?",
            (bucket, window_start),
        )

    def purge(self, *, older_than_seconds: int = 86400) -> int:
        return self._db.execute(
            "DELETE FROM rate_limits WHERE window_start < ?",
            (int(time.time()) - older_than_seconds,),
        ).rowcount


def _looks_like_address(value: str) -> bool:
    """Cheap test for "this is an IP or a hostname, hash it before storing"."""
    if not value or len(value) > 253:
        return False
    if value.replace(".", "").replace(":", "").isdigit():
        return True
    return False


@dataclass
class LimitPolicy:
    """A named limit, so route code reads as intent rather than as numbers."""

    name: str
    limit: int
    window_seconds: int = 60

    def apply(self, limiter: RateLimiter, *bucket_parts: str) -> RateLimitResult:
        return limiter.check(
            limiter.bucket(self.name, *bucket_parts),
            limit=self.limit,
            window_seconds=self.window_seconds,
        )


def policy_from_config(config: Any) -> Dict[str, LimitPolicy]:
    """The limits a deployment runs with, derived from configuration."""
    security = config.security
    return {
        "api": LimitPolicy("api", security.rate_limit_per_minute, 60),
        "credentials": LimitPolicy(
            "credentials", security.credential_rate_limit_per_minute, 60
        ),
        # Generation is the expensive operation, so it gets its own budget even
        # though it is also counted against the general API limit.
        "generation": LimitPolicy("generation", max(10, security.rate_limit_per_minute // 10), 60),
        "upload": LimitPolicy("upload", max(5, security.rate_limit_per_minute // 20), 60),
    }
