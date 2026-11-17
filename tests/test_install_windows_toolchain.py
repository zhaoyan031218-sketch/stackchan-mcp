import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.install_windows_toolchain import (
    Archive,
    network_timeouts,
    package_lock,
    select_archive,
    verified_snapshot,
)


def fixture_archive(data: bytes) -> Archive:
    return Archive("windows_amd64", len(data), hashlib.sha256(data).hexdigest())


def test_verified_copy_is_unchanged_when_source_is_replaced(tmp_path):
    data = b"complete archive"
    source = tmp_path / "download.tar.gz"
    source.write_bytes(data)
    with verified_snapshot(source, tmp_path / "checks", fixture_archive(data)) as snapshot:
        source.write_bytes(b"browser changed the file")
        assert snapshot.read_bytes() == data
    assert not snapshot.exists()
    assert source.read_bytes() == b"browser changed the file"


@pytest.mark.parametrize("actual", [b"", b"short", b"wrong file bytes"])
def test_incomplete_or_changed_download_never_reaches_installation(tmp_path, actual):
    expected = b"correct contents"
    source = tmp_path / "download.tar.gz"
    source.write_bytes(actual)
    reached_installation = False
    with pytest.raises(ValueError), verified_snapshot(source, tmp_path / "checks", fixture_archive(expected)):
        reached_installation = True
    assert not reached_installation
    assert source.read_bytes() == actual
    assert not list((tmp_path / "checks").glob("toolchain-*"))


def test_same_size_corruption_is_rejected_by_digest(tmp_path):
    data = b"complete archive"
    source = tmp_path / "download.tar.gz"
    source.write_bytes(bytes(byte ^ 1 for byte in data))
    with pytest.raises(ValueError, match="SHA-256"), verified_snapshot(source, tmp_path / "checks", fixture_archive(data)):
        pytest.fail("Corrupt archive reached installation")


def test_shared_package_lock_refuses_parallel_installer_and_remains_usable(tmp_path):
    packages = tmp_path / "packages"
    with package_lock(packages), pytest.raises(RuntimeError, match="still running"), package_lock(packages):
        pytest.fail("Two installers acquired the package lock")
    with package_lock(packages):
        assert packages.is_dir()
    assert Path(str(packages) + ".lock").exists()


def test_windows_arm64_uses_registry_approved_archive():
    assert select_archive("windows_arm64") == select_archive("windows_amd64")
    assert select_archive("windows_x86").sha256 != select_archive("windows_amd64").sha256
    with pytest.raises(ValueError):
        select_archive("linux_x86_64")


def test_network_timeout_is_restored_after_failed_install():
    http = SimpleNamespace(__default_requests_timeout__=(10, None))
    with pytest.raises(RuntimeError, match="failed"), network_timeouts(http):
        assert http.__default_requests_timeout__ == (10, 30)
        raise RuntimeError("failed")
    assert http.__default_requests_timeout__ == (10, None)
