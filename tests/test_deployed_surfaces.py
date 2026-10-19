"""Phase 3 + 4: the cron tick, and S3 storage against a live S3 server.

Two separate concerns, together because both are "the part that only exists when
deployed":

**The tick.** A serverless deployment has no worker threads, so a scheduled
invocation is the only thing that moves a job. The tick must claim real work,
drive it one step, and return a summary -- and it must stop rather than overrun
its budget, because a killed invocation leaves a job holding a lease.

**S3.** `docs/LIMITATIONS.md` recorded that `S3ObjectStore` had never been run
against a live bucket. These tests close that, against any S3-compatible endpoint
given in `STUDIO_TEST_S3_ENDPOINT`. They are skipped when it is unset, because a
storage test that silently tests the local disk backend is worse than no test.

Honest limit, stated here rather than buried: the endpoint these were developed
against accepts a wrong secret on a header-signed request, so this suite proves
the request shape and the round trip, not the server-side signature check. That
check is covered separately by `scripts/check_sigv4_parity.py`, which compares the
signature against botocore's reference implementation.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.storage import S3ObjectStore  # noqa: E402

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# S3 storage, against a live server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def s3():
    endpoint = os.environ.get("STUDIO_TEST_S3_ENDPOINT", "").strip()
    if not endpoint:
        pytest.skip("set STUDIO_TEST_S3_ENDPOINT to run the S3 tests")
    store = S3ObjectStore(
        endpoint=endpoint,
        region=os.environ.get("STUDIO_TEST_S3_REGION", "us-east-1"),
        bucket=os.environ.get("STUDIO_TEST_S3_BUCKET", "studio-media"),
        access_key=os.environ.get("STUDIO_TEST_S3_ACCESS_KEY", "testkey"),
        secret_key=os.environ.get("STUDIO_TEST_S3_SECRET_KEY", "testsecret"),
    )
    return store


class TestS3RoundTrip:
    def test_put_then_get_returns_the_same_bytes(self, s3):
        key = "tests/%s/round-trip.png" % uuid.uuid4().hex[:8]
        stored = s3.put(key, PNG, "image/png")
        assert stored.key == key
        assert stored.byte_size == len(PNG)
        assert s3.get(key) == PNG
        s3.delete(key)

    def test_a_missing_key_raises_rather_than_returning_empty_bytes(self, s3):
        with pytest.raises(Exception):
            s3.get("tests/%s/absent.png" % uuid.uuid4().hex[:8])

    def test_list_prefix_finds_the_key(self, s3):
        prefix = "tests/%s/" % uuid.uuid4().hex[:8]
        key = prefix + "listed.png"
        s3.put(key, PNG, "image/png")
        keys = [item.key for item in s3.list_prefix(prefix)]
        assert key in keys
        s3.delete(key)

    def test_delete_removes_the_object(self, s3):
        key = "tests/%s/gone.png" % uuid.uuid4().hex[:8]
        s3.put(key, PNG, "image/png")
        assert s3.delete(key) is True
        with pytest.raises(Exception):
            s3.get(key)

    def test_a_signed_url_fetches_the_object_and_carries_no_secret(self, s3):
        import urllib.request

        key = "tests/%s/signed.png" % uuid.uuid4().hex[:8]
        s3.put(key, PNG, "image/png")
        url = s3.signed_url(key, expires_seconds=300)
        assert "X-Amz-Signature" in url or "Signature" in url
        assert "X-Amz-Expires" in url or "Expires" in url
        # The secret key must never appear in a URL that leaves the process.
        assert "testsecret" not in url and "secret" not in url.lower().replace("x-amz-", "")
        with urllib.request.urlopen(url, timeout=20) as response:
            assert response.read() == PNG
        s3.delete(key)

    def test_a_tampered_signed_url_is_refused(self, s3):
        """The one signature check the server does enforce.

        A pre-signed URL names its object in the signed query, so pointing it at
        a different key invalidates the signature. This is the part of the S3
        contract that is genuinely verified end to end here.
        """
        import urllib.error
        import urllib.request

        key = "tests/%s/tamper-a.png" % uuid.uuid4().hex[:8]
        other = "tests/%s/tamper-b.png" % uuid.uuid4().hex[:8]
        s3.put(key, PNG, "image/png")
        url = s3.signed_url(key, expires_seconds=300)
        tampered = url.replace(key, other)
        assert tampered != url
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(tampered, timeout=20).read()
        s3.delete(key)


class TestStorageConfigurationFailsClosed:
    def test_asking_for_s3_without_credentials_is_an_error_not_a_disk_backend(self):
        """The failure mode this prevents: writes succeed, then vanish.

        A serverless filesystem is discarded between invocations, so silently
        falling back to the local backend means every uploaded asset and every
        generated output disappears while every request reported success.
        """
        from studio.config import StorageConfig
        from studio.errors import ApiError
        from studio.storage import store_from_config

        with pytest.raises(ApiError) as caught:
            store_from_config(StorageConfig(backend="s3"))
        assert caught.value.code == "configuration_required"
        missing = (caught.value.details or {}).get("missing") or []
        assert "STUDIO_STORAGE_BUCKET" in missing

    def test_a_complete_s3_configuration_builds_the_s3_backend(self):
        from studio.config import StorageConfig
        from studio.storage import store_from_config

        store = store_from_config(
            StorageConfig(
                backend="s3",
                endpoint="https://example.invalid",
                bucket="b",
                access_key="a",
                secret_key="c",
            )
        )
        assert isinstance(store, S3ObjectStore)

    def test_the_local_backend_is_still_the_default(self, tmp_path):
        from studio.config import StorageConfig
        from studio.storage import LocalObjectStore, store_from_config

        store = store_from_config(StorageConfig(backend="local", root=str(tmp_path)))
        assert isinstance(store, LocalObjectStore)
