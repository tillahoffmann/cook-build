from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse


class Resource(Protocol):
    @property
    def label(self) -> str:
        """Stable string identifier for hashing and display."""
        ...

    def digest(self) -> bytes:
        """Return a content hash as raw bytes."""
        ...

    def exists(self) -> bool:
        """Return True if the resource exists."""
        ...


@dataclass
class FileResource:
    path: Path

    @property
    def label(self) -> str:
        return str(self.path)

    def digest(self) -> bytes:
        return hashlib.sha256(self.path.read_bytes()).digest()

    def exists(self) -> bool:
        return self.path.exists()


_gcs_client = None


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage

        _gcs_client = storage.Client()
    return _gcs_client


@dataclass
class GcsResource:
    bucket: str
    object_key: str

    @property
    def label(self) -> str:
        return f"gs://{self.bucket}/{self.object_key}"

    def digest(self) -> bytes:
        from google.api_core.exceptions import NotFound

        client = _get_gcs_client()
        blob = client.bucket(self.bucket).blob(self.object_key)
        try:
            blob.reload()
        except NotFound as exc:
            raise FileNotFoundError(f"GCS object not found: {self.label}") from exc
        if blob.md5_hash is None:  # pragma: no cover
            raise ValueError(
                f"GCS object {self.label} has no md5 hash. "
                "This can happen with composite objects."
            )
        return base64.b64decode(blob.md5_hash)

    def exists(self) -> bool:
        client = _get_gcs_client()
        blob = client.bucket(self.bucket).blob(self.object_key)
        return blob.exists()


def resolve_resource(path: str | Path, project_root: Path | None = None) -> Resource:
    """Dispatch a path string to the appropriate Resource implementation."""
    s = str(path)
    parsed = urlparse(s)
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_key = parsed.path.lstrip("/")
        if not bucket:
            raise ValueError(
                f"gs:// URL has empty bucket name: {s!r}. "
                f"Expected format: gs://bucket/path/to/object"
            )
        if not object_key:
            raise ValueError(
                f"gs:// URL has empty object key: {s!r}. "
                f"Expected format: gs://bucket/path/to/object"
            )
        return GcsResource(bucket=bucket, object_key=object_key)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(
            f"Unsupported URL scheme {parsed.scheme!r} in {s!r}. "
            f"Supported schemes: file, gs"
        )
    # Local file: resolve against project_root
    if parsed.scheme == "file":
        if not s.startswith("file:///"):
            raise ValueError(
                f"file:// URLs must use file:///absolute/path format: {s!r}"
            )
        p = Path(unquote(parsed.path))
    else:
        p = Path(s)
    root = (project_root or Path.cwd()).resolve()
    resolved = (root / p).resolve() if not p.is_absolute() else p.resolve()
    return FileResource(path=resolved)
