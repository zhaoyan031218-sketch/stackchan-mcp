# CoreS3 precompiled firmware USB configuration

## Symptom and design

The Windows setup path previously compiled firmware locally and could remain
stuck downloading the ESP32 compiler or its dependencies. A precompiled image
cannot contain the user's Wi-Fi password, Railway device token, or server trust
anchor. The `m5stack-cores3-runtime` target therefore accepts those values over
USB after flashing, with no local C++ compiler requirement.

The runtime target always loads public build defaults and ignores private
`firmware/src/config.h`. Existing compile-time targets retain their behavior.
This target keeps its Wi-Fi and cloud settings together; the old `wifi` NVS
namespace does not override a newly provisioned SSID. It does not start an open
Wi-Fi portal when the configured network is unavailable.

## Protocol

At boot, before the peripheral services are initialized, the device advertises
this line every second for eight seconds:

```json
{"type":"stackchan.setup.ready","v":1}
```

A request is one UTF-8 JSON line followed by LF. It contains string fields
`host`, `token`, `ssid`, `password`, and `ca_pem`, with
`"type":"stackchan.configure"` and `"v":1`. The firmware bounds a line to
12288 bytes, the CA to 4096 bytes, and the canonical stored configuration to
6144 bytes. Hostnames cannot include a scheme, port, path, empty DNS labels, or
invalid label characters. Tokens use the same 24–512-character bearer-token
alphabet as the outbound WebSocket client. SSID and WPA password checks match
the setup helper. The CA must parse successfully with mbedTLS.

The parser rejects malformed or partial JSON, embedded NUL characters, trailing
objects, and invalid fields without changing the active configuration. An
in-progress line gets at most five additional seconds after the initial window;
five seconds without another input byte expires a partial line. Every polling
iteration reads a bounded number of bytes. If no saved configuration is valid,
the device shows the USB setup screen and continues waiting for configuration.

CoreS3 uses USB Serial/JTAG CDC. Its Arduino driver defaults to a 256-byte RX
queue and drops excess received bytes, regardless of the nominal 115200 baud
setting. The runtime target allocates a 12290-byte RX queue before starting
Serial and fails closed if that allocation fails. The Windows helper also
paces small writes. This prevents a certificate-bearing request from silently
overflowing the default queue.

## Persistence and replies

Validated settings are serialized as one NVS byte blob at namespace `sc-cloud`,
key `config`. `Preferences.putBytes` performs the NVS update and commit. The
firmware checks the written length and reads the exact bytes back before
acknowledging success. It never clears the namespace or writes individual
credentials as separate keys. A String value was intentionally avoided because
NVS strings have a roughly 4 KB limit and stricter contiguous-page requirements.

```json
{"type":"stackchan.setup.result","ok":true}
```

Success is followed by flushing Serial, a 250 ms delay, and a restart. It means
the configuration was saved, not that Railway authentication or the Wi-Fi
connection has already succeeded. Configuration values remain immutable during
normal runtime; replace them in the next boot's USB window. Fixed error codes
include `invalid_host`, `invalid_token`, `invalid_ssid`, `invalid_password`,
`invalid_ca`, `invalid_size`, `input_timeout`, `storage_open_failed`,
`storage_full_or_write_failed`, `storage_verify_failed`, and `rx_buffer_failed`.
Requests and secret values are never echoed in protocol replies.

## Verification and limits

`test_runtime_config` covers successful parsing, Wi-Fi password forms, validation
boundaries, certificate-validator failure, embedded NUL input, trailing objects,
and preserving the prior configuration after invalid input. All seven tests
passed with a native g++ build using `-Wall -Wextra -Werror`. The complete
PlatformIO build and release verification are recorded separately.

The tests use a certificate-validator stub; the firmware build links the real
mbedTLS parser. USB timing, physical flash behavior, Wi-Fi association, NVS
power-loss recovery, and Railway connectivity require the actual robot. No such
hardware success is inferred from offline tests. An ACK followed by failure to
join Wi-Fi should be handled by checking the network details and rerunning USB
configuration after a restart, without erasing unrelated NVS settings.

## References

- `firmware/src/runtime_config.cpp`
- `firmware/src/config_loader.h`
- `firmware/src/wifi_manager.cpp`
- Arduino ESP32 2.0.17 `HWCDC.cpp` (`setRxBufferSize`, RX interrupt handler)
- Arduino ESP32 2.0.17 `Preferences.cpp` (`putBytes`, `nvs_commit`)
