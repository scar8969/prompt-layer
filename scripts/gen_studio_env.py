#!/usr/bin/env python3
"""Generate fresh secrets and print the export commands for the current shell.

Use this once per machine. Copy the two export commands into the shell where
you will run python -m studio.app, OR pipe them through `eval`.
"""

import secrets
import subprocess
import sys


def main() -> int:
    enc = secrets.token_hex(32)
    auth = secrets.token_hex(32)
    # ASCII-safe: no Unicode, no CRLF. A 64-char hex value never needs quoting.
    print("# Fresh Studio secrets for this session:")
    print(f'export STUDIO_ENCRYPTION_KEY="{enc}"')
    print(f'export STUDIO_AUTH_SECRET="{auth}"')
    print("export STUDIO_ENV=development")
    print("export STUDIO_REQUIRE_HTTPS=false")
    print("export STUDIO_ALLOW_INSECURE_ENDPOINTS=true")
    print("export STUDIO_ENABLE_MOCK_PROVIDER=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())