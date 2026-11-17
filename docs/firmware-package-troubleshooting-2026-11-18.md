# CoreS3 firmware package delivery — 2026-11-18

The user reported a previous firmware download stalled. The previous browser
session and transfer logs are unavailable, so this does not establish the cause
of that transfer failure. A build dependency download, a ZIP download, and a
USB flash write are three separate operations and should be reported separately.

`scripts/package_firmware.py` packages completed CoreS3 builds into a compressed
ZIP without padded flash gaps. It includes only the four necessary binary
components, a machine-readable manifest, SHA-256 checksums, and Chinese flash
instructions. It does not include source configuration, caches, ELF debug
symbols, or dependencies. Provisioned binaries can themselves contain secrets
and must still be treated as private artifacts.

Wait for a successful build before invoking it. For the unprovisioned public
build, use:

```sh
python scripts/package_firmware.py \
  --build-dir firmware/.pio/build/m5stack-cores3-public \
  --boot-app0 /path/to/platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin \
  --profile verification-only \
  --output /path/to/output/cores3-verification-only.zip
```

The public build must not be described as Railway-ready: its outbound transport
is disabled. The alternative profile `provisioned-unverified` records a build
operator's declaration, not a successful live test. The script rejects that
profile when the build directory is the public environment. Both profiles
explicitly record `hardware_validated=false` and `cloud_validated=false`.

Before creating the ZIP, the script checks nonempty stable input files,
ESP32-S3 image headers, image size bounds, and the expected NVS/OTA/app0
partitions. These checks do not replace esptool image validation or the build
gate. The ZIP is written to a temporary file in the output directory, flushed,
read back, CRC-checked, and compared byte for byte with all intended contents.
Only after successful verification does an atomic rename expose the final
filename. Any existing package remains untouched if validation fails.
The command prints the completed ZIP's byte count and SHA-256 hash for delivery.
This verifies the generated artifact; it cannot guarantee a user's network or
ChatGPT download transfer completes.

Offsets follow the pinned PlatformIO Arduino build recipe for ESP32-S3:
`bootloader.bin` at `0x0000`, `partitions.bin` at `0x8000`, framework
`boot_app0.bin` at `0xe000`, and application `firmware.bin` at `0x10000`.
Flashing the components separately avoids a merged image's padding over the
saved NVS area at `0x9000–0xdfff`. Do not use erase-all. Preserving the bytes
does not guarantee settings remain interpretable if the installed firmware
uses a different partition layout. This package does update bootloader,
partition table, and OTA selection data.

Validation covers package hashes/content, correct profile labelling, missing
images, wrong chip headers, images that overlap partitions, and failed ZIP
verification without replacing an existing release:

```sh
uv run --locked ruff check scripts/package_firmware.py tests/test_package_firmware.py
uv run --locked pyright scripts/package_firmware.py
uv run --locked pytest -q tests/test_package_firmware.py
```

References: `firmware/platformio.ini`; PlatformIO espressif32 7.0.0
`builder/main.py`; Arduino ESP32 2.0.17 `tools/platformio-build.py` and
`tools/partitions/default_16MB.csv`.
