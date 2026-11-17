#pragma once

#include <stddef.h>
#include <stdint.h>

namespace outbound {

// Only an admitted incoming PCM command uses this allowance. Connect, control
// message and write timeouts keep their pinned WebSockets defaults/overrides.
constexpr uint32_t PCM_RECEIVE_IDLE_TIMEOUT_MS = 8000;

class BinaryReceiveWindow {
public:
    void begin(uint32_t now) { lastProgressMs_ = now; }

    // Polling, log output and fragment callbacks are not receive progress.
    // The TLS read hook calls this only for bytes actually returned to it.
    void progress(uint32_t now, size_t bytes) {
        if (bytes) lastProgressMs_ = now;
    }

    bool idleExpired(uint32_t now) const {
        return static_cast<uint32_t>(now - lastProgressMs_) >= PCM_RECEIVE_IDLE_TIMEOUT_MS;
    }

private:
    uint32_t lastProgressMs_ = 0;
};

}  // namespace outbound
