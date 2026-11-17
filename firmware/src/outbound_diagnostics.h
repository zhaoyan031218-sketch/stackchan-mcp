#pragma once

#include "outbound_protocol.h"

namespace outbound {

enum class CommandValidity {
    CURRENT,
    DISCONNECTED,
    GENERATION_CHANGED,
    AGE_TIMEOUT,
    CLOCK_UNAVAILABLE,
    DEADLINE_ELAPSED,
    DEADLINE_TOO_FAR,
};

// Same admission rules as commandIsCurrent(), with fixed diagnostic labels.
// Never derive a log label from request headers, paths, IDs or payload bytes.
inline CommandValidity classifyCommand(bool connected, uint32_t generation,
                                       uint32_t currentGeneration, uint32_t now,
                                       uint32_t started, int64_t utcNowMs,
                                       int64_t expiresAtMs) {
    if (!connected) return CommandValidity::DISCONNECTED;
    if (generation != currentGeneration) return CommandValidity::GENERATION_CHANGED;
    if (deadlineExpired(now, started, COMMAND_TIMEOUT_MS)) return CommandValidity::AGE_TIMEOUT;
    if (utcNowMs <= 0) return CommandValidity::CLOCK_UNAVAILABLE;
    if (expiresAtMs <= utcNowMs) return CommandValidity::DEADLINE_ELAPSED;
    if (expiresAtMs - utcNowMs > COMMAND_TIMEOUT_MS + CLOCK_SKEW_TOLERANCE_MS) {
        return CommandValidity::DEADLINE_TOO_FAR;
    }
    return CommandValidity::CURRENT;
}

inline const char* commandValidityLabel(CommandValidity reason) {
    switch (reason) {
        case CommandValidity::CURRENT: return "current";
        case CommandValidity::DISCONNECTED: return "disconnected";
        case CommandValidity::GENERATION_CHANGED: return "generation_changed";
        case CommandValidity::AGE_TIMEOUT: return "age_timeout";
        case CommandValidity::CLOCK_UNAVAILABLE: return "clock_unavailable";
        case CommandValidity::DEADLINE_ELAPSED: return "deadline_elapsed";
        case CommandValidity::DEADLINE_TOO_FAR: return "deadline_too_far";
        default: return "unknown";
    }
}

}  // namespace outbound
