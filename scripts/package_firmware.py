#!/usr/bin/env python3
"""Package a completed CoreS3 build, then verify it before exposing the ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile

OFFSETS = {
    "bootloader.bin": 0x0000,
    "partitions.bin": 0x8000,
    "boot_app0.bin": 0xE000,
    "firmware.bin": 0x10000,
}
PROFILES = ("verification-only", "provisioned-unverified", "runtime-configurable")
RUNTIME_MARKER = b"STACKCHAN_RUNTIME_SETUP_V1"


def read_complete_file(path: Path) -> bytes:
    before = path.stat()
    if not path.is_file() or before.st_size == 0:
        raise ValueError(f"Missing or empty firmware component: {path.name}")
    payload = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Build is still changing: {path.name}")
    return payload


def validate_images(images: dict[str, bytes]) -> None:
    """Reject wrong chips, partition layouts, and files that overlap saved NVS."""
    for name in ("bootloader.bin", "firmware.bin"):
        payload = images[name]
        if len(payload) < 24 or payload[0] != 0xE9:
            raise ValueError(f"Invalid ESP image header: {name}")
        if struct.unpack_from("<H", payload, 12)[0] != 9:
            raise ValueError(f"Not an ESP32-S3 image: {name}")
    if len(images["bootloader.bin"]) > 0x8000:
        raise ValueError("Bootloader overlaps the partition table")
    if len(images["partitions.bin"]) > 0x1000:
        raise ValueError("Partition table overlaps saved NVS")
    if len(images["boot_app0.bin"]) != 0x2000:
        raise ValueError("Unexpected boot_app0.bin size")

    entries: dict[tuple[int, int], tuple[int, int]] = {}
    table = images["partitions.bin"]
    for pos in range(0, len(table) - 31, 32):
        magic, kind, subtype, offset, size = struct.unpack_from("<HBBII", table, pos)
        if magic != 0x50AA:
            break
        entries[(kind, subtype)] = (offset, size)
    expected = {(1, 2): (0x9000, 0x5000), (1, 0): (0xE000, 0x2000), (0, 0x10): (0x10000, 0x640000)}
    if any(entries.get(key) != value for key, value in expected.items()):
        raise ValueError("Unsupported partition layout; expected CoreS3 default_16MB.csv")
    if len(images["firmware.bin"]) > expected[(0, 0x10)][1]:
        raise ValueError("Firmware exceeds the app0 partition")


def instructions(profile: str) -> str:
    status = {
        "verification-only": "本包仅用于编译验证／本地诊断。云端中继未配置，不能用它恢复 Railway + Kelivo 连接。",
        "provisioned-unverified": "本包按已配置构建打包，但尚未通过实机及 Railway 联调；打包工具不验证配置值。",
        "runtime-configurable": "本包已预编译，支持通过 USB 填入 Wi-Fi 和 Railway 配置；不用下载 PlatformIO 或编译工具链。尚未通过你的设备实机验收。",
    }[profile]
    setup = ""
    if profile == "runtime-configurable":
        setup = """
Windows 使用方法（Python 3.11 或更新版本）：
已经配好 Wi-Fi／Railway、只升级固件时，在新解压目录打开终端运行：
  .\\START-SETUP.cmd --flash-only --port COM5
将 COM5 换成实际端口。此模式验证文件后只烧录，保留 sc-cloud 配置，不读取证书或重新询问密码／令牌。
仅适用于已有兼容分区的本项目固件；从未配置过的设备刷完会等待 USB 设置。
不要为了升级执行 --configure-only，也不要勾选全片擦除。

需要观察掉线／播放问题时，另开一次命令（不刷机、不重置、不改设置）：
  .\\.venv\\Scripts\\python.exe scripts\\setup_prebuilt.py --monitor --port COM5
监视默认 90 秒后自动结束。开始后在 Kelivo 触发一次短句播放；需要开机日志时可自行按设备 Reset。
只显示 BOOT／OUTBOUND／PCM／ENV 和已知 ESP 错误、重启、回溯信息；过滤配置 JSON、Wi-Fi 和网址。
日志只在终端显示，不保存文件。复制这些日志用于排查；看见“烧录成功”不代表语音问题已修复。

首次配置或确实需要更改设置时：
1. 完整解压 ZIP 到新文件夹，把 CoreS3 用可传数据的 USB 线接电脑。
2. 双击 START-SETUP.cmd。首次只安装固定版本 esptool/pyserial 等小型 USB 工具，不下载编译器。
3. 选择 CoreS3 的实际 COM 端口，输入 2.4GHz Wi-Fi 名称、密码及 Railway 的 STACKCHAN_RELAY_DEVICE_TOKEN。
   输入这些内容时屏幕不显示字符。不要使用 Kelivo 的 MCP 令牌代替设备令牌。
4. 工具先验证文件和服务 TLS 证书，再烧录现成四个 bin，然后通过 USB 写入设置；设备确认保存后重启。
   默认服务：gojobody.up.railway.app。令牌和 Wi-Fi 密码不写入电脑文件、命令行或构建产物。
5. 烧录成功但设置未完成时无需再刷固件。在解压目录打开终端运行：
   .venv\\Scripts\\python.exe scripts\\setup_prebuilt.py --configure-only --port COM5
   把 COM5 换成当前实际端口；工具等待时可按 CoreS3 Reset 键进入启动后的 8 秒设置窗口。
   尚未配置的固件会持续等待 USB 设置。设备重置后 COM 号可能变化，需在设备管理器重新确认。

换服务时在上述命令后加 --host 新的完整域名（不含 https:// 或 /mcp）。
查看端口：.venv\\Scripts\\python.exe scripts\\setup_prebuilt.py --list-ports
保存成功只说明 USB 配置写入设备；不等于 Wi-Fi、Railway、摄像头或语音实测成功。
"""
    return f"""Stack-chan CoreS3 固件包

{status}
{setup}

仅适用 M5Stack CoreS3（ESP32-S3，16MB Flash）。收到文件不代表摄像头、语音、网络已通过实机验收。
包中包含四个烧录组件、manifest.json、SHA256SUMS.txt 和本说明；USB 配置版还附带 Windows 配置脚本。
已配置固件本身可能含连接凭证，不应公开分享。

下载完整 ZIP 后先解压。若下载进度停住，保留现有机器人固件，不要把部分下载文件烧进去。
SHA256SUMS.txt 记录包内文件的完整性校验，整个 ZIP 的校验值由交付方另外提供。
macOS/Linux 可用：shasum -a 256 -c SHA256SUMS.txt
Windows PowerShell 可用：Get-FileHash .\\firmware.bin -Algorithm SHA256
将结果与 SHA256SUMS.txt 对应行比较。ZIP 校验不能代替实机测试。

确认要烧录该版本后，连接可传数据的 USB 线并选择实际串口。
支持多个地址的 ESP32 烧录工具应选择 ESP32-S3，并填写：
  bootloader.bin  0x0000
  partitions.bin  0x8000
  boot_app0.bin   0xe000
  firmware.bin   0x10000
不要把 firmware.bin 当作从 0x0000 开始的完整合并镜像。

使用 esptool 4.x 的示例（先把 PORT 改为实际串口，如 COM5 或 /dev/cu.usbmodem...）：
python -m esptool --chip esp32s3 --port PORT --baud 460800 write_flash --flash_mode keep --flash_freq keep --flash_size 16MB 0x0000 bootloader.bin 0x8000 partitions.bin 0xe000 boot_app0.bin 0x10000 firmware.bin

不要勾选“Erase all”或执行 erase_flash。上述四段写入不覆盖 0x9000–0xdfff 的 NVS 保存区；
这仅在原有分区布局兼容时能保留已有 Wi-Fi 等设置。本操作会更新启动程序、分区表及 OTA 选择信息。
如果不清楚原固件分区布局，先备份／核对原固件再烧录。
首次烧录失败可降低波特率并重试同一完整包；不要反复回退不同来源的固件。

验收顺序：Wi-Fi → 设备云端连接 → 单张照片 → 短句播放 → 录音。
Kelivo 能连接 MCP 只证明前端到服务器连通，不能证明机器人已在线。
摄像头照片、实际扬声器声音和录音内容均需要用户在设备端确认。
"""


def package_firmware(build_dir: Path, boot_app0: Path, output: Path, profile: str) -> dict[str, object]:
    if profile not in PROFILES:
        raise ValueError("An explicit supported artifact profile is required")
    if profile != "verification-only" and build_dir.name.endswith("-public"):
        raise ValueError("The public build cannot be labelled provisioned")
    if output.suffix.lower() != ".zip":
        raise ValueError("Output must have a .zip extension")
    paths = {name: (boot_app0 if name == "boot_app0.bin" else build_dir / name) for name in OFFSETS}
    images = {name: read_complete_file(path) for name, path in paths.items()}
    validate_images(images)
    if profile == "runtime-configurable" and RUNTIME_MARKER not in images["firmware.bin"]:
        raise ValueError("Firmware lacks the USB runtime setup marker")
    components = [
        {"name": name, "offset": f"0x{OFFSETS[name]:04x}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in images.items()
    ]
    manifest = {
        "format": 1,
        "board": "m5stack-cores3",
        "chip": "esp32s3",
        "flash_bytes": 16 * 1024 * 1024,
        "profile": profile,
        "hardware_validated": False,
        "cloud_validated": False,
        "components": components,
    }
    if profile == "runtime-configurable":
        manifest["setup_protocol"] = 1
    payloads = dict(images)
    payloads["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    payloads["README-zh.txt"] = instructions(profile).encode()
    if profile == "runtime-configurable":
        script_dir = Path(__file__).resolve().parent
        for destination, source in {
            "scripts/setup_prebuilt.py": "setup_prebuilt.py",
            "scripts/configure_robot.py": "configure_robot.py",
            "scripts/windows_tls_root.ps1": "windows_tls_root.ps1",
            "setup-requirements.txt": "prebuilt-requirements.txt",
            "START-SETUP.cmd": "start_prebuilt.cmd",
        }.items():
            payloads[destination] = read_complete_file(script_dir / source)
    payloads["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in payloads.items()
    ).encode()

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in payloads.items():
                    archive.writestr(name, data)
            handle.flush()
            os.fsync(handle.fileno())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(payloads):
                raise ValueError("ZIP integrity validation failed")
            for name, data in payloads.items():
                if archive.read(name) != data:
                    raise ValueError(f"ZIP content verification failed: {name}")
        final_bytes = temporary.read_bytes()
        result: dict[str, object] = {
            "path": str(output),
            "bytes": len(final_bytes),
            "sha256": hashlib.sha256(final_bytes).hexdigest(),
            "profile": profile,
        }
        temporary.replace(output)
        return result
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--boot-app0", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    args = parser.parse_args()
    try:
        result = package_firmware(args.build_dir, args.boot_app0, args.output, args.profile)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
