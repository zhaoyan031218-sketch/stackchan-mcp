// Extracted unchanged readCb from links2004/WebSockets 2.7.2 WebSockets.cpp.
// Copyright (c) 2015 Markus Sattler.
// This excerpt is licensed under GNU LGPL version 2.1 or, at your option, later.
// See the WebSockets dependency's LICENSE; this fixture exists to execute the
// exact synchronous callback patched by firmware/scripts/websocket_limits.py.

bool WebSockets::readCb(WSclient_t * client, uint8_t * out, size_t n, WSreadWaitCb cb) {
#if (WEBSOCKETS_NETWORK_TYPE == NETWORK_ESP8266_ASYNC)
    if(!client->tcp || !client->tcp->connected()) {
        return false;
    }

    client->tcp->readBytes(out, n, std::bind([](WSclient_t * client, bool ok, WSreadWaitCb cb) {
        if(cb) {
            cb(client, ok);
        }
    },
                                       client, std::placeholders::_1, cb));

#else
    unsigned long t = millis();
    ssize_t len;
    DEBUG_WEBSOCKETS("[readCb] n: %zu t: %lu\n", n, t);
    while(n > 0) {
        if(client->tcp == NULL) {
            DEBUG_WEBSOCKETS("[readCb] tcp is null!\n");
            if(cb) {
                cb(client, false);
            }
            return false;
        }

        if(!client->tcp->connected()) {
            DEBUG_WEBSOCKETS("[readCb] not connected!\n");
            if(cb) {
                cb(client, false);
            }
            return false;
        }

        if((millis() - t) > WEBSOCKETS_TCP_TIMEOUT) {
            DEBUG_WEBSOCKETS("[readCb] receive TIMEOUT! %lu\n", (millis() - t));
            if(cb) {
                cb(client, false);
            }
            return false;
        }

        if(!client->tcp->available()) {
            WEBSOCKETS_YIELD_MORE();
            continue;
        }

        len = client->tcp->read((uint8_t *)out, n);
        if(len > 0) {
            t = millis();
            out += len;
            n -= len;
            // DEBUG_WEBSOCKETS("Receive %d left %d!\n", len, n);
        } else {
            // DEBUG_WEBSOCKETS("Receive %d left %d!\n", len, n);
        }
        if(n > 0) {
            WEBSOCKETS_YIELD();
        }
    }
    if(cb) {
        cb(client, true);
    }
    WEBSOCKETS_YIELD();
#endif
    return true;
}
