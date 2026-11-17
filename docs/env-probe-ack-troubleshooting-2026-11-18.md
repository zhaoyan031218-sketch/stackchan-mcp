# Environment probe accepted unacknowledged addresses

## Symptom

After a reboot and before any speech request, `stackchan_sense` returned HTTP
500 from `/device/env`. This is separate evidence from a previous HTTP 503
after audio interrupted the outbound connection. The firmware's environment
handler returns 500 for a sensor read failure; the relay produces 503 when its
device channel is unavailable.

## Confirmed defect

`i2cProbe` returned the result of `M5.Ex_I2C.start()` and discarded the result of
`stop()`. In the pinned M5Unified/M5GFX implementation, `start()` launches the
address transfer. M5GFX sets `wait_ack_stage = 1` and returns before the address
acknowledgment has been evaluated. `stop()` calls `i2c_wait`, which checks NACK
and timeout flags. M5Unified documents that `stop()` returns false on an ACK
error, and its own `scanID` is implemented as `start(...) && stop()`.

Consequently an absent SHT31 address could be marked present. The first actual
measurement then failed and `/env` returned 500. The same false probe also
prevented searching the alternate SHT31 address. QMP6988 additionally checks a
chip ID, so a false address probe was usually rejected later in its setup.

This proves a software defect; it does not establish whether the user's robot
has an external ENV module, a wiring problem, a different sensor, or another
read failure. CoreS3 Port A is the supported external sensor bus, not an assumed
onboard temperature/humidity/pressure sensor.

## Changes

- Use M5Unified `scanID(address, 100000)`, including completed ACK checking.
- Honor external-bus initialization failure and successful SHT31 reset before
  declaring a sensor ready. Continue checking alternate addresses if setup fails.
- Keep missing-sensor results separate from bus initialization and measurement
  failures. All `/env` replies include `bus: port_a`, `bus_ready`, and detected
  sensor booleans. Existing `sensors` values still describe valid current readings.
- Refresh diagnostic errors on each measurement. Failed or unavailable reads
  leave output values as NaN, never previous caller values. Diagnostics contain
  fixed strings and no credentials.
- Keep the camera bus, Wi-Fi settings, and sensor compensation math unchanged.

## Verification

Seven native tests compile and run the actual `env_service.cpp` with an external
bus double that models the pinned driver's deferred ACK behavior. The initial
five tests all failed against the original source: deferred NACK, bus init
failure, failed SHT31 reset, alternate-address detection, and QMP chip-ID
validation. All seven pass after the fix, including recovery from CRC failure
and clearing unavailable output values. Standalone compilation used
`g++ -std=c++17 -Wall -Wextra -Werror` and Unity.

The driver timing was verified from the installed pinned dependency source;
the tests do not exercise physical I2C pins. Firmware build and broader checks
are recorded with the release. Hardware acceptance still requires a known ENV
module or a confirmed absent module, and a fresh `/env` result after restart.

## Source references

- `firmware/src/env_service.cpp`: probe, initialization, and measurements
- `firmware/src/http_server.cpp`: `handleEnv`
- M5Unified 0.2.15 `src/utility/I2C_Class.cpp`: `scanID`, `start`, `stop`
- M5Unified 0.2.15 `src/utility/I2C_Class.hpp`: `stop` ACK-error contract
- Pinned M5GFX `src/lgfx/v1/platforms/esp32/common.inl`: `restart`,
  `beginTransaction`, `endTransaction`, `i2c_wait`
