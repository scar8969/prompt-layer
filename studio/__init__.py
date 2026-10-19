"""Studio Platform — a model-agnostic, BYOK/BYOM image and video creative platform.

This is a new subsystem that lives beside Prompt Layer's existing tooling. It
does not modify or replace anything under /api or /promptlayer; it imports that
package's sourced model catalog where useful and follows the same rule that
matters most there: never show a result that did not actually happen.

Module map
----------
config.py        environment parsing, startup validation, honest "needs setup" reporting
logging_setup.py structured JSON logs with credential redaction applied at emit time
crypto.py        AES-256-GCM envelope for provider credentials
db.py            SQLite connection, forward-only migration runner, transactions
migrations/      ordered .sql files; the schema of record
security/        password hashing, session tokens, CSRF, RBAC, rate limiting, SSRF guard
net.py           the only outbound HTTP client; every provider call and output fetch goes through it
adapters/        one interface, several real provider protocols, one clearly-labelled mock
jobs/            durable queue, worker loop, cancellation, retry, artifact ingestion
media.py         upload validation by content sniffing, metadata probing, thumbnails
storage.py       object storage behind a small interface (local disk by default)
routes/          HTTP surface
http.py          the router, request/response plumbing, error rendering
app.py           wiring: everything above assembled into one handler
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
