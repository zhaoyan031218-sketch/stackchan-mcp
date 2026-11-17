#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#include "outbound_protocol.h"

// Validate the exact relay-produced metadata and payload with the firmware's
// production parser. This host fixture does not emulate ESP32 TCP/TLS reads.
int main() {
    std::string metadata;
    if (!std::getline(std::cin, metadata)) return 1;
    std::vector<uint8_t> binary(std::istreambuf_iterator<char>(std::cin), {});
    JsonDocument doc;
    outbound::RequestInfo info;
    if (!outbound::parseRequest(reinterpret_cast<const uint8_t*>(metadata.data()),
                               metadata.size(), doc, info)) return 2;
    if (!outbound::validBinary(binary.data(), binary.size(), info)) return 3;
    if (std::string(doc["method"] | "") != "POST" ||
        std::string(doc["path"] | "") != "/play/pcm" ||
        std::string(doc["query"]["mode"] | "") != "staged" ||
        std::string(doc["query"]["seq"] | "") != "0" ||
        std::string(doc["query"]["final"] | "") != "0" ||
        !doc["body"].is<JsonObject>() || doc["body"].size() != 0) return 4;
    std::cout << info.binarySize << '\n';
    return 0;
}
