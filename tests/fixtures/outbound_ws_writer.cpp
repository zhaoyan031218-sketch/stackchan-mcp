// Feed production firmware framing into an independent real WebSocket receiver.
#include <iostream>
#include <iterator>
#include <vector>
#include "outbound_ws_writer.h"

int main() {
    const std::vector<uint8_t> data(std::istreambuf_iterator<char>(std::cin), {});
    uint32_t counter = 0;
    const auto result = outbound::writeMaskedBinary(data.data(), data.size(),
        [](uint8_t* bytes, size_t size) -> size_t {
            std::cout.write(reinterpret_cast<const char*>(bytes), size);
            return std::cout.good() ? size : 0;
        }, [&]() -> uint32_t { return 0x12345678u + counter++; }, []() { return true; });
    return result.status == outbound::BinaryWriteStatus::COMPLETE ? 0 : 1;
}
