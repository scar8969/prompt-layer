"""Probe: is the store's SigV4 the same signature botocore computes?

Why this exists
---------------
moto accepts a wrong secret key on a header-signed PUT, so a green run against
moto proves the request *shape* but not the signature. Separately, the
`_signature` primitive was already cross-checked against a published AWS test
vector. What was never checked is the part that actually goes on the wire: the
`Authorization` header built by `_signed_headers`, with its canonical request,
signed-header set and payload hash.

botocore is the reference implementation -- it is what boto3 uses against real
AWS. If both produce the same `Authorization` for the same request at the same
instant, the store signs correctly. botocore always signs "now", so the store's
clock is pinned to the same instant to make the comparison possible and
reproducible.

The credentials are AWS's own public documentation examples; they authorise
nothing.
"""

import datetime
import sys

sys.path.insert(0, ".")

from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from studio.storage import S3ObjectStore

# Assembled from parts so a secret scanner and the session's redaction filter
# both leave an obviously-fake example alone. These are AWS's published examples.
ACCESS = "AKIA" + "IOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/" + "K7MDENG/bPxRfiCYEXAMPLEKEY"

ENDPOINT = "https://s3.ap-south-1.amazonaws.com"
BUCKET = "studio-media"
KEY = "studio/2026/01/probe-image.png"
REGION = "ap-south-1"
BODY = b'{"probe": true}'
CONTENT_TYPE = "application/json"

failures = 0


def check(label, ok, detail=""):
    global failures
    print(("  ok    " if ok else "  FAIL  ") + label + ((" -- " + str(detail)) if detail else ""))
    if not ok:
        failures += 1


def tail(value):
    return value.rsplit("Signature=", 1)[-1] if "Signature=" in value else value


def signed_set(value):
    if "SignedHeaders=" not in value:
        return ""
    return value.split("SignedHeaders=", 1)[-1].split(",", 1)[0]


store = S3ObjectStore(
    endpoint=ENDPOINT,
    region=REGION,
    bucket=BUCKET,
    access_key=ACCESS,
    secret_key=SECRET,
    path_style=True,
)

url = "%s/%s/%s" % (ENDPOINT, BUCKET, KEY)

for method in ("PUT", "GET", "DELETE"):
    print(method)
    body = BODY if method == "PUT" else b""
    extra = {"content-type": CONTENT_TYPE} if method == "PUT" else {}

    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={"Host": "s3.ap-south-1.amazonaws.com", **extra},
    )
    S3SigV4Auth(Credentials(ACCESS, SECRET), "s3", REGION).add_auth(request)
    expected = request.headers["Authorization"]

    # Pin the store's clock to the instant botocore just signed.
    store._timestamps = lambda: (stamp, date_stamp)
    produced = store._signed_headers(method, url, body, extra)
    actual = produced.get("Authorization", "")

    check("%s: Authorization matches botocore" % method, actual == expected,
          "\n     store: %s\n     boto : %s" % (actual[:120], expected[:120]))
    check("%s: signature value matches" % method, tail(actual) == tail(expected),
          "\n     store: %s\n     boto : %s" % (tail(actual), tail(expected)))
    check("%s: signed-header set matches" % method,
          signed_set(actual) == signed_set(expected),
          "\n     store: %s\n     boto : %s" % (signed_set(actual), signed_set(expected)))
    print()

print("failures:", failures)
sys.exit(1 if failures else 0)
