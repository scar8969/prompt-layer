"""The Studio application: wires storage, services, routes and the worker together.

One function builds everything, and one function starts it. Nothing is created at
import time, so a test can build an app against a temporary database and a
temporary object store without touching the real data directory.

Run it directly to serve the API and the built client:

    python -m studio.app --port 8091

The worker threads start in the same process by default. Set STUDIO_JOB_WORKERS
to zero to serve HTTP only and run a separate process with the same command.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import Configuration, load_config
from .db import Database
from .errors import ConfigError
from .http import HttpApp, Router, build_server
from .logging_setup import configure_logging, get_logger
from .repository import repositories
from .security.accounts import AccountService
from .security.rate_limit import RateLimiter
from .security.sessions import SessionService
from .services.assets import AssetService
from .services.connections import ConnectionService
from .services.generations import GenerationService
from .services.runner import GenerationRunner
from .storage import store_from_config

LOG = get_logger(__name__)

#: Where the built client is, relative to the repository root.
DEFAULT_STATIC_ROOT = os.path.join("web", "dist")


@dataclass
class StudioApp:
    """Everything one running instance owns. Assembled once, then read-only.

    Routes and the worker both receive this object, so a handler never reaches
    for a global and a test never has to patch one.
    """

    config: Configuration
    db: Database
    store: Any
    repos: Any
    sessions: SessionService
    accounts: AccountService
    limiter: RateLimiter
    connections: ConnectionService
    assets: AssetService
    generations: GenerationService
    runner: GenerationRunner
    router: Router
    http: HttpApp

    def status(self) -> Dict[str, Any]:
        """A redacted description of what this instance can actually do.

        Every field here is an observation, not an intention: the adapter list
        comes from the live registry, the worker fields from the live runner, and
        the missing-configuration list from the configuration itself.
        """
        from . import __version__
        from .adapters.base import CAMERA_MOVES, REFERENCE_ROLES, describe_parameter_types

        return {
            "version": __version__,
            "environment": self.config.environment,
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "database": "ok" if self.db.ping() else "unavailable",
            "database_backend": "sqlite",
            "storage_backend": getattr(self.store, "backend_name", "unknown"),
            "adapters": self.connections.types(),
            "mock_provider_enabled": bool(self.config.mock_provider_enabled),
            "billing": {
                "mode": "byok",
                "platform_credits": False,
                "note": (
                    "Generations are billed by the provider account behind the connection you "
                    "chose. This platform does not charge credits for them."
                ),
            },
            "missing_configuration": sorted(
                set(self.config.missing_for("credentials"))
                | set(self.config.missing_for("auth"))
                | set(self.config.missing_for("storage_object"))
            ),
            "parameter_types": describe_parameter_types(),
            "camera_moves": [
                {
                    "key": move.key,
                    "label": move.label,
                    "phrase": move.phrase,
                    "is_native": move.is_native,
                }
                for move in CAMERA_MOVES
            ],
            "reference_roles": list(REFERENCE_ROLES),
            "worker": {
                "running": self.runner.running,
                "workers": int(self.config.jobs.workers),
                "stats": self.runner.stats.to_public(),
            },
            "egress": _egress_summary(self.config),
        }

    def shutdown(self) -> None:
        """Stop the worker, then close the database handles."""
        self.runner.stop()
        self.db.close_all()


def _egress_summary(config: Configuration) -> Dict[str, Any]:
    """The outbound policy as the system page shows it, with no secrets."""
    from .net import EgressPolicy

    policy = EgressPolicy.from_config(config)
    described = policy.describe()
    described["allow_private_hosts"] = list(config.egress.allow_private_hosts or [])
    described["blocked_network_count"] = len(described.pop("blocked_networks", []) or [])
    described["blocked_hostname_count"] = len(described.pop("blocked_hostnames", []) or [])
    described.pop("named_metadata_addresses", None)
    return described


def build_app(config: Optional[Configuration] = None, *, start_worker: bool = False) -> StudioApp:
    """Assemble the application. Does not open a socket and does not start threads.

    Refuses nothing: a deployment with no encryption key or no auth secret still
    boots, and the features that need them answer 503 with the variable name. That
    is deliberate, because a server that will not start cannot tell you what it is
    missing.
    """
    settings = config or load_config()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    db = Database.from_config(settings)
    applied = db.migrate()
    if applied:
        LOG.info("database migrated", extra={"versions": applied})

    store = store_from_config(settings, data_dir=str(settings.data_dir))
    repos = repositories(db)
    sessions = SessionService(db, settings.security)
    accounts = AccountService(db, settings)
    limiter = RateLimiter(db)
    connections = ConnectionService(repos, settings)
    assets = AssetService(repos, store, settings)
    generations = GenerationService(repos, connections, assets, settings)
    runner = GenerationRunner(
        repositories=repos,
        connections=connections,
        assets=assets,
        config=settings,
    )

    router = Router()
    http_app = HttpApp(
        router, sessions=sessions, limiter=limiter, config=settings, database=db
    )

    app = StudioApp(
        config=settings,
        db=db,
        store=store,
        repos=repos,
        sessions=sessions,
        accounts=accounts,
        limiter=limiter,
        connections=connections,
        assets=assets,
        generations=generations,
        runner=runner,
        router=router,
        http=http_app,
    )

    from .routes import register_routes

    register_routes(router, app)
    LOG.info(
        "studio application assembled",
        extra={"routes": len(router), "storage": getattr(store, "backend_name", "?")},
    )
    if start_worker:
        runner.start()
    return app


def serve(
    config: Optional[Configuration] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8091,
    static_root: str = DEFAULT_STATIC_ROOT,
) -> None:
    """Build, bind, serve until interrupted, then shut down cleanly."""
    app = build_app(config, start_worker=True)
    settings = app.config
    root = static_root if os.path.isdir(static_root) else ""
    server = build_server(app.http, host=host, port=port, static_root=root)
    address = "http://%s:%d" % (host, server.server_address[1])
    LOG.info("serving", extra={"address": address, "static_root": root or "(none)"})
    print("Studio API listening on %s" % address)
    if not root:
        print("No built client found at %s; only the API is being served." % static_root)
    stop = threading.Event()

    def _handle(_signum: int, _frame: Any) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _handle)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass
    thread = threading.Thread(target=server.serve_forever, name="studio-http", daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        pass
    finally:
        server.shutdown()
        server.server_close()
        app.shutdown()
        LOG.info("stopped")


def check() -> int:
    """A startup check for a deployment: report what is missing, exit non-zero.

    Prints the variables that are not set and the adapters that are registered.
    It never prints a secret, only whether one is present.
    """
    settings = load_config()
    problems = []
    for feature in ("credentials", "auth", "storage_object"):
        for name in settings.missing_for(feature):
            problems.append("%s is not set (%s cannot work without it)" % (name, feature))
    if settings.is_production and not settings.security.require_https:
        problems.append(
            "STUDIO_REQUIRE_HTTPS is off in production; the session cookie will not be Secure"
        )
    from .adapters.core import known_adapters

    print("environment: %s" % settings.environment)
    print("database:    %s" % settings.database_path)
    print("storage:     %s" % settings.storage.backend)
    print("adapters:    %s" % ", ".join(known_adapters()))
    if problems:
        print("")
        for problem in problems:
            print("MISSING: %s" % problem)
        return 1
    print("configuration looks complete for auth and credential storage")
    return 0


def main(argv: Optional[list] = None) -> int:
    """The command line: --check, or serve on a host and port."""
    parser = argparse.ArgumentParser(prog="studio.app", description="Studio API server")
    parser.add_argument("--host", default=os.environ.get("STUDIO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STUDIO_PORT", "8091")))
    parser.add_argument("--static-root", default=DEFAULT_STATIC_ROOT)
    parser.add_argument("--check", action="store_true", help="report configuration and exit")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    try:
        serve(host=args.host, port=args.port, static_root=args.static_root)
    except ConfigError as error:
        print("Configuration error: %s" % error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
