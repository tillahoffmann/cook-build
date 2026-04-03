"""Tests for the Resource abstraction.

FileResource tests use real files via tmp_path.
GcsResource tests use fake-gcs-server in Docker (fixtures from conftest.py).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cook.resource import (
    FileResource,
    GcsResource,
    resolve_resource,
)

# ---------------------------------------------------------------------------
# FileResource
# ---------------------------------------------------------------------------


def test_file_resource_digest(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello world")
    r = FileResource(path=f)
    assert r.digest() == hashlib.sha256(b"hello world").digest()


def test_file_resource_exists(tmp_path: Path) -> None:
    f = tmp_path / "exists.txt"
    r = FileResource(path=f)
    assert not r.exists()
    f.write_text("x")
    assert r.exists()


def test_file_resource_label(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    r = FileResource(path=f)
    assert r.label == str(f)


def test_file_resource_digest_file_not_found(tmp_path: Path) -> None:
    r = FileResource(path=tmp_path / "nope.txt")
    with pytest.raises(FileNotFoundError):
        r.digest()


# ---------------------------------------------------------------------------
# resolve_resource dispatch
# ---------------------------------------------------------------------------


def test_resolve_resource_local_relative(tmp_path: Path) -> None:
    r = resolve_resource("foo/bar.txt", project_root=tmp_path)
    assert isinstance(r, FileResource)
    assert r.path == (tmp_path / "foo" / "bar.txt").resolve()


def test_resolve_resource_local_absolute(tmp_path: Path) -> None:
    p = tmp_path / "abs.txt"
    r = resolve_resource(str(p), project_root=tmp_path)
    assert isinstance(r, FileResource)
    assert r.path == p.resolve()


def test_resolve_resource_path_object(tmp_path: Path) -> None:
    r = resolve_resource(Path("x.txt"), project_root=tmp_path)
    assert isinstance(r, FileResource)


def test_resolve_resource_gs() -> None:
    r = resolve_resource("gs://my-bucket/path/to/obj.txt")
    assert isinstance(r, GcsResource)
    assert r.bucket == "my-bucket"
    assert r.object_key == "path/to/obj.txt"


def test_resolve_resource_gs_no_key() -> None:
    r = resolve_resource("gs://bucket-only/")
    assert isinstance(r, GcsResource)
    assert r.bucket == "bucket-only"
    assert r.object_key == ""


def test_resolve_resource_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        resolve_resource("ftp://example.com/file")


def test_resolve_resource_file_scheme(tmp_path: Path) -> None:
    target = tmp_path / "foo.txt"
    r = resolve_resource(f"file://{target}")
    assert isinstance(r, FileResource)
    assert r.path == target.resolve()


def test_resolve_resource_file_scheme_with_spaces(tmp_path: Path) -> None:
    target = tmp_path / "path with spaces" / "foo.txt"
    from urllib.parse import quote

    url = f"file://{quote(str(target))}"
    r = resolve_resource(url)
    assert isinstance(r, FileResource)
    assert r.path == target.resolve()


def test_resolve_resource_file_scheme_with_hostname() -> None:
    with pytest.raises(ValueError, match="file:// URLs with a hostname"):
        resolve_resource("file://hostname/path")


def test_gcs_resource_label() -> None:
    r = GcsResource(bucket="b", object_key="k")
    assert r.label == "gs://b/k"


# ---------------------------------------------------------------------------
# GcsResource integration tests (fake-gcs-server via conftest.py)
# ---------------------------------------------------------------------------


def test_gcs_resource_digest(gcs_bucket) -> None:  # type: ignore[no-untyped-def]
    gcs_bucket.blob("test-object.txt").upload_from_string(b"hello gcs")

    r = GcsResource(bucket=gcs_bucket.name, object_key="test-object.txt")
    assert r.digest() == hashlib.md5(b"hello gcs").digest()


def test_gcs_resource_exists(gcs_bucket) -> None:  # type: ignore[no-untyped-def]
    r = GcsResource(bucket=gcs_bucket.name, object_key="nope.txt")
    assert not r.exists()

    gcs_bucket.blob("nope.txt").upload_from_string(b"now it exists")
    assert r.exists()


def test_gcs_resource_digest_changes_on_content_change(gcs_bucket) -> None:  # type: ignore[no-untyped-def]
    blob = gcs_bucket.blob("mutable.txt")
    blob.upload_from_string(b"version1")
    r = GcsResource(bucket=gcs_bucket.name, object_key="mutable.txt")
    d1 = r.digest()

    blob.upload_from_string(b"version2")
    d2 = r.digest()

    assert d1 != d2
