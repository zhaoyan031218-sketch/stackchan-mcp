#pragma once
#include <stddef.h>
#include <stdint.h>

void initOutboundService();
void serviceOutboundCommands();
// Thread-safe cancellation check; does not access WebSockets or hardware.
bool isOutboundGenerationCurrent(uint32_t generation);

// Network-task-only hooks for the version-checked local WebSockets receive
// patch. They never retry a read or extend the admitted command's deadline.
bool stackchanWebSocketReadIdleExpired(uint32_t defaultReadLastProgressMs);
bool stackchanWebSocketReadCurrent();
void stackchanWebSocketReadProgress(size_t bytes);
