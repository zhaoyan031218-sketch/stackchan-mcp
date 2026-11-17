#ifndef STACKCHAN_CONFIG_LOADER_H
#define STACKCHAN_CONFIG_LOADER_H

#if defined(STACKCHAN_REQUIRE_OUTBOUND_CONFIG)
#if defined(STACKCHAN_USE_PUBLIC_CONFIG)
#error "Railway firmware cannot use the public rehearsal configuration"
#elif !__has_include("config.h")
#error "Railway firmware requires private src/config.h; run scripts/configure_robot.py"
#endif
#endif

#if defined(STACKCHAN_USE_PUBLIC_CONFIG)
#include "../config.h.example"
#elif __has_include("config.h")
#include "config.h"
#endif

#include "config_defaults.h"

#if defined(STACKCHAN_REQUIRE_OUTBOUND_CONFIG)
static_assert(STACKCHAN_OUTBOUND_ENABLED == 1,
              "Railway firmware requires outbound enabled");
static_assert(sizeof(STACKCHAN_OUTBOUND_HOST) > 1,
              "Railway firmware requires a server hostname");
static_assert(STACKCHAN_OUTBOUND_PORT == 443,
              "Railway firmware requires WSS port 443");
static_assert(sizeof(STACKCHAN_OUTBOUND_DEVICE_TOKEN) >= 25,
              "Railway firmware requires a device token of at least 24 characters");
static_assert(sizeof(STACKCHAN_OUTBOUND_CA_PEM) > 1,
              "Railway firmware requires a TLS trust anchor");
#endif

#endif
