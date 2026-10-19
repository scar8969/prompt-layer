"""Probe: does S3ObjectStore work against a live S3-compatible server?

`docs/LIMITATIONS.md` says this class "has never been run against a live bucket".
That is the honest state the maintainer recorded, and it is the Phase 4 gap. This
probe closes it against a real HTTP S3 server (moto, which validates SigV4
properly -- it rejects a wrong signature rather than ignoring the header).

Every method the application actually calls is exercised, in the order it calls
them, and each result is checked rather than assumed:

    put -> get -> head-ish (list_prefix) -> signed_url -> delete

If this passes, the remaining risk in S3 storage is credential delivery on the
platform, not the wire format.
"""

import sys
import time

sys.path.insert(0, ".")

from studio.storage import S3ObjectStore

ENDPOINT = "http://127.0.0.1:5055"
BUCKET = "studio-media"
KEY = "studio/2026/01/probe-image.png"

store = S3ObjectStore(
    endpoint=ENDPOINT,
    region="us-east-1",
    bucket=BUCKET,
    access_key="testkey",
    secret_key="testsecret",
)

# A real 1x1 PNG, so a byte comparison means something.
PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

failures = 0


def check(label, ok, detail=""):
    global failures
    print(("  ok    " if ok else "  FAIL  ") + label + ((" -- " + str(detail)) if detail else ""))
    if not ok:
        failures += 1


print("S3ObjectStore against a live server at", ENDPOINT)
print()

print("put")
stored = store.put(KEY, PNG, "image/png")
check("put returns a StoredObject", stored is not None)
check("key round-trips", stored.key == KEY, stored.key)
check("size is the byte length", stored.byte_size == len(PNG), stored.byte_size)
check("etag present", bool(getattr(stored, "etag", "")), getattr(stored, "etag", ""))

print("get")
got = store.get(KEY)
check("bytes are identical", got == PNG, "%d vs %d" % (len(got), len(PNG)))

print("get of a missing key is an error, not empty bytes")
try:
    store.get("studio/does-not-exist.png")
    check("missing key raises", False, "returned without raising")
except Exception as error:
    check("missing key raises", True, type(error).__name__)

print("list_prefix")
listed = store.list_prefix("studio/")
keys = [item.key for item in listed]
check("the uploaded key is listed", KEY in keys, keys[:5])

print("signed_url")
url = store.signed_url(KEY, expires_seconds=300)
check("url points at the endpoint", ENDPOINT in url, url[:80])
check("url carries a signature", "X-Amz-Signature" in url or "Signature" in url)
check("url carries an expiry", "X-Amz-Expires" in url or "Expires" in url)
check("the secret is not in the url", "testsecret" not in url)

print("the signed url actually fetches the object")
import urllib.request

try:
    with urllib.request.urlopen(url, timeout=15) as response:
        fetched = response.read()
    check("signed GET returns the object", fetched == PNG, len(fetched))
except Exception as error:
    check("signed GET returns the object", False, "%s: %s" % (type(error).__name__, error))

print("delete")
check("delete reports success", store.delete(KEY) is True)
try:
    store.get(KEY)
    check("deleted key is gone", False, "still readable")
except Exception:
    check("deleted key is gone", True)

print()
print("failures:", failures)
sys.exit(1 if failures else 0)
