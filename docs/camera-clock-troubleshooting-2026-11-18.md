# CoreS3 camera clock and external I2C

## Report and investigation

The robot was reported unable to provide usable camera vision through a Kelivo
client connected to Railway MCP. There is no device serial log or captured image
in this workspace, so the cause of that live failure is not yet confirmed.

The firmware set `CAM_PIN_XCLK` to GPIO 2 and configured a 20 MHz output. The
official M5CoreS3 GC0308 driver sets `pin_xclk = -1`. M5Unified 0.2.15 maps GPIO 2
to external I2C SDA on both CoreS3 and StackChan. Using it as the camera clock
therefore reconfigures the wrong board pin and can disrupt external I2C devices.
The installed Arduino ESP32 2.0.17 `libesp32-camera.a` confirms this behavior:
`ll_cam_set_pin` reads `pin_xclk` at configuration offset 8 and branches over
GPIO output setup when it is negative. A nonnegative value calls
`gpio_set_direction` and `gpio_matrix_out` for that pin. The ESP32-S3 camera
driver source has the same `if (config->pin_xclk >= 0)` guard.

Camera power is already enabled by `M5.begin()` through M5Unified's power setup;
adding another speculative power sequence is unnecessary. The pinned Arduino
ESP32 SDK enables `CONFIG_GC0308_SUPPORT`. The relay supports JPEG binary replies,
and `stackchan_see` already returns an MCP image content block, rather than only a
server-local filename. None of these findings proves the deployed hardware or
Kelivo model configuration is working.

## Fix

Set `CAM_PIN_XCLK` to `-1`, matching the board driver. Keep the existing internal
I2C handoff, RGB565-to-JPEG conversion, fresh-frame discard, playback exclusion,
and bounded session cleanup. No Wi-Fi, relay token, TLS, or device data is changed.

## Verification

The native `test_camera_service` suite exercises the actual camera service with
mock camera and bus APIs: correct board clock selection, stale-frame discard,
initialization/capture/conversion failures, bus release/restore failures,
playback rejection, and idle session cleanup. These are software contract
checks; the mocks cannot verify camera exposure or physical wiring.

`pio test -e native -f test_camera_service` initially could not complete because
the PlatformIO `tool-scons` download repeatedly selected a mirror blocked by the
cloud network policy. This is a dependency download failure, not a test failure.
Build and test results after recovery are recorded with the release verification.
As an independent check, compiling `test_camera_service/test_main.cpp` with
`g++ -std=c++17 -Wall -Wextra -Werror`, the repository test support headers, and
Unity v2.6.0 passed all six tests with no failures. This fallback does not replace
the ESP32 firmware build.

On the device, first confirm relay `online: true`. Capture one image while audio
is idle, verify it can be decoded and shows the current scene, then verify touch
and external sensor readings still work. Repeat after speech playback. If MCP
receives an image but the model cannot describe it, inspect Kelivo's model vision
support and image tool-result handling separately.

## References

- [M5CoreS3 GC0308 driver](https://github.com/m5stack/M5CoreS3/blob/main/src/utility/GC0308.cpp)
- [M5Unified 0.2.15 board pin table](https://github.com/m5stack/M5Unified/blob/0.2.15/src/M5Unified.cpp)
- [M5Unified 0.2.15 camera power setup](https://github.com/m5stack/M5Unified/blob/0.2.15/src/utility/Power_Class.cpp)
- [ESP32-S3 camera GPIO setup](https://github.com/espressif/esp32-camera/blob/v2.0.4/target/esp32s3/ll_cam.c#L326-L330)
- `docs/touch-sensor-camera-i2c-troubleshooting-2026-08-30.md`
- `docs/outbound-channel.md`
