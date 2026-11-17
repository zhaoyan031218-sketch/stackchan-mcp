"""Install the exact CoreS3 Windows compiler using PlatformIO Core or a verified archive.

Run with firmware/.venv/Scripts/python.exe (PlatformIO 6.1.18). Without
--archive, use Core's registry installer with a bounded network read timeout.
The helper neither builds nor flashes firmware and never reads config.h.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "espressif"
NAME = "toolchain-xtensa-esp32s3"
PACKAGE_ID = 12550
VERSION = "8.4.0+2021r2-patch5"
PIO_VERSION = "6.1.18"


@dataclass(frozen=True)
class Archive:
    system: str
    size: int
    sha256: str


# Exact PlatformIO registry metadata, checked on 2026-11-18. These are package
# archives, including package.json; upstream bare compiler ZIPs are different.
ARCHIVES = {
    "windows_x86": Archive("windows_x86", 81421435,
                           "02eb9dbd6d545ec9f2880e70028dad834a70fdd2b54548f5d2f23d5e136f1b74"),
    "windows_amd64": Archive("windows_amd64", 84769299,
                             "c45a0427288d406a530a80f486fba7e77273f8152cb057cc48ef3aa64c085bf5"),
}


def select_archive(system: str) -> Archive:
    # The registry explicitly serves this same archive to Windows ARM64.
    if system == "windows_arm64":
        system = "windows_amd64"
    if system not in ARCHIVES:
        raise ValueError("This helper supports Windows only")
    return ARCHIVES[system]


@contextmanager
def verified_snapshot(source: Path, directory: Path, archive: Archive):
    if not source.is_file() or source.stat().st_size != archive.size:
        raise ValueError(f"Download is missing/incomplete or the wrong package; expected {archive.size} bytes")
    directory.mkdir(parents=True, exist_ok=True)
    # Verify a private copy so another process updating the source cannot change what
    # gets installed after verification. Never extract an unchecked archive.
    with tempfile.TemporaryDirectory(prefix="toolchain-", dir=directory) as temporary:
        snapshot = Path(temporary) / "toolchain.tar.gz"
        checksum = hashlib.sha256()
        size = 0
        with source.open("rb") as incoming, snapshot.open("xb") as output:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                size += len(chunk)
                if size > archive.size:
                    raise ValueError("Download changed during verification; nothing was installed")
                checksum.update(chunk)
                output.write(chunk)
        if size != archive.size or checksum.hexdigest() != archive.sha256:
            raise ValueError("SHA-256 mismatch; nothing was installed. Download the exact official file again")
        yield snapshot


@contextmanager
def package_lock(packages: Path):
    # Use the same OS lock and path as PlatformIO 6.1.18. Fail immediately if
    # another build is active. Leave the harmless lock file in place rather
    # than deleting a file that another process might already have opened.
    packages.mkdir(parents=True, exist_ok=True)
    with Path(str(packages) + ".lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("PlatformIO is still running. Stop the old build with Ctrl+C, then retry") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            # Allows the shared lock behavior to be exercised by Linux tests.
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("PlatformIO is still running. Stop the old build with Ctrl+C, then retry") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def network_timeouts(http_module):
    # Core 6.1.18 defaults to (10, None): a connected but stalled download can
    # wait indefinitely. Change this process only; retain normal TLS/proxy rules.
    previous = http_module.__default_requests_timeout__
    http_module.__default_requests_timeout__ = (10, 30)
    try:
        yield
    finally:
        http_module.__default_requests_timeout__ = previous


def install_package(manager, spec_factory, snapshot: Path | None = None) -> Path:
    # A generic `pio pkg install --tool file://...` loses the registry owner/id,
    # so the next build may download the same tool again. Retain its identity.
    spec = spec_factory(owner=OWNER, id=PACKAGE_ID, name=NAME, requirements=VERSION)
    package = manager.get_package(spec)
    if package is None:
        if snapshot is None:
            package = manager.install_from_registry(spec)
        else:
            # Core 6.1.18 strips "file://" literally, without URL-decoding.
            # Keep native Windows drive letters, spaces and non-ASCII intact.
            package = manager.install_from_uri(f"file://{snapshot.resolve()}", spec)
        if package is None:
            raise RuntimeError("PlatformIO could not install the compiler; keep the download error above")
        manager.call_pkg_script(package, "postinstall")
        manager.memcache_reset()
    resolved = manager.get_package(spec)
    if resolved is None or str(resolved.metadata.version) != VERSION:
        raise RuntimeError("PlatformIO did not recognize the exact registered toolchain")
    return Path(resolved.path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Path to the unextracted official .tar.gz download")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this helper on the Windows computer used to build the firmware")
    core = ROOT / "work/platformio"
    os.environ["PLATFORMIO_CORE_DIR"] = str(core)
    os.environ["PLATFORMIO_SETTING_ENABLE_TELEMETRY"] = "no"
    try:
        # Deliberately use the isolated firmware venv, not the MCP dependencies.
        import platformio  # pyright: ignore[reportMissingImports]
        from platformio import http, util  # pyright: ignore[reportMissingImports]
        from platformio.exception import (  # pyright: ignore[reportMissingImports]
            PlatformioException,
        )
        from platformio.package.manager.tool import (  # pyright: ignore[reportMissingImports]
            ToolPackageManager,
        )
        from platformio.package.meta import PackageSpec  # pyright: ignore[reportMissingImports]

        if platformio.__version__ != PIO_VERSION:
            raise RuntimeError(f"This helper requires PlatformIO {PIO_VERSION} in firmware/.venv")
        archive = select_archive(util.get_systype())
        print(f"Required archive: {archive.system}; {archive.size} bytes")
        manager = ToolPackageManager()
        if Path(manager.package_dir).resolve() != (core / "packages").resolve():
            raise RuntimeError("Unexpected PlatformIO package directory; installation stopped")
        try:
            with package_lock(Path(manager.package_dir)), network_timeouts(http):
                if args.archive:
                    with verified_snapshot(args.archive.resolve(), core.parent / "offline-checks", archive) as snapshot:
                        print("File size and SHA-256 verified.")
                        installed = install_package(manager, PackageSpec, snapshot)
                else:
                    print("Using PlatformIO Core. A network read with no data times out after 30 seconds.")
                    installed = install_package(manager, PackageSpec)
                compiler = installed / "bin/xtensa-esp32s3-elf-gcc.exe"
                result = subprocess.run(  # noqa: S603 - executable from the verified official package; no shell.
                    [str(compiler), "--version"], check=True, capture_output=True, text=True, timeout=20,
                )
                if "8.4.0" not in result.stdout:
                    raise RuntimeError("Compiler did not report the required GCC version")
                print(result.stdout.splitlines()[0])
        except PlatformioException as exc:
            raise RuntimeError(str(exc)) from exc
        print("Compiler installed and recognized. Resume with: py scripts\\rebuild_robot.py")
        print("Other firmware dependencies may still need to download.")
    except ImportError:
        print(r"Use firmware\.venv\Scripts\python.exe to run this helper (PlatformIO 6.1.18).", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
