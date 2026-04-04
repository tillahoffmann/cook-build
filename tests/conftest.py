"""Shared test fixtures.

The fake-gcs-server container is session-scoped: started once, shared across
all test modules that need GCS. STORAGE_EMULATOR_HOST is set so that
google-cloud-storage talks to the fake server without any patching.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Generator
from uuid import uuid4

import pytest

CONTAINER_NAME = f"cook-gcs-test-{uuid4()}"
IMAGE = "fsouza/fake-gcs-server"


@pytest.fixture(scope="session")
def gcs_server() -> Generator[int]:
    """Start fake-gcs-server in Docker, yield the host port."""
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            CONTAINER_NAME,
            "-p",
            "0:4443",
            IMAGE,
            "-scheme",
            "http",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Failed to start fake-gcs-server: {result.stderr}"

    port_result = subprocess.run(
        ["docker", "port", CONTAINER_NAME, "4443"],
        capture_output=True,
        text=True,
    )
    assert port_result.returncode == 0, f"Failed to get port: {port_result.stderr}"
    port = int(port_result.stdout.strip().rsplit(":", 1)[-1])

    for _ in range(30):
        time.sleep(0.5)
        try:
            r = subprocess.run(
                ["curl", "-sf", f"http://localhost:{port}/storage/v1/b"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
    else:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
        pytest.fail("fake-gcs-server did not become ready in time")

    yield port

    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


@pytest.fixture()
def _gcs_env(gcs_server: int) -> Generator[None]:
    """Set STORAGE_EMULATOR_HOST and reset the cached GCS client."""
    import cook.resource as resource_mod

    old_env = os.environ.get("STORAGE_EMULATOR_HOST")
    old_client = resource_mod._gcs_client

    os.environ["STORAGE_EMULATOR_HOST"] = f"http://localhost:{gcs_server}"
    resource_mod._gcs_client = None

    yield

    resource_mod._gcs_client = old_client
    if old_env is None:
        os.environ.pop("STORAGE_EMULATOR_HOST", None)
    else:
        os.environ["STORAGE_EMULATOR_HOST"] = old_env


@pytest.fixture()
def gcs_bucket(_gcs_env: None, gcs_server: int) -> Generator:  # type: ignore[no-untyped-def]
    """Create a temporary GCS bucket, clean up after."""
    from google.cloud import storage  # type: ignore[import-untyped]

    client = storage.Client(project="test-project")
    bucket_name = f"test-bucket-{uuid4()}"
    bucket = client.create_bucket(bucket_name)
    yield bucket
    bucket.delete(force=True)
