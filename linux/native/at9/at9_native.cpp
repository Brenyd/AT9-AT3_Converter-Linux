// Native ATRAC9 command-line encoder for AT9 & AT3 Converter.
// Input: raw little-endian float64 stereo PCM at 48 kHz.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "at9.h"
#include "at9_internal.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::vector<uint8_t> read_file(const std::string &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("cannot read " + path);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(file),
                                std::istreambuf_iterator<char>());
}

void write_file(const std::string &path, const std::vector<uint8_t> &bytes) {
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char *>(bytes.data()),
               static_cast<std::streamsize>(bytes.size()));
    if (!file) throw std::runtime_error("cannot write " + path);
}

void put_u32(std::vector<uint8_t> &bytes, size_t offset, uint32_t value) {
    if (offset + 4 > bytes.size()) throw std::runtime_error("invalid RIFF output");
    for (unsigned i = 0; i < 4; ++i)
        bytes[offset + i] = static_cast<uint8_t>(value >> (8 * i));
}

void set_custom_loop(std::vector<uint8_t> &bytes, uint32_t start, uint32_t end) {
    // build_at9 writes one smpl chunk containing one loop. Locate it by walking
    // the RIFF chunks rather than relying on fixed header offsets.
    if (bytes.size() < 12 || std::memcmp(bytes.data(), "RIFF", 4) != 0)
        throw std::runtime_error("invalid RIFF output");
    size_t pos = 12;
    while (pos + 8 <= bytes.size()) {
        const uint8_t *id = bytes.data() + pos;
        uint32_t size = 0;
        for (unsigned i = 0; i < 4; ++i)
            size |= static_cast<uint32_t>(bytes[pos + 4 + i]) << (8 * i);
        size_t payload = pos + 8;
        if (payload + size > bytes.size()) throw std::runtime_error("invalid RIFF chunk");
        if (std::memcmp(id, "smpl", 4) == 0) {
            if (size < 60) throw std::runtime_error("invalid smpl chunk");
            // Nine header words, then loop id/type/start/end. Sony's AT9 files
            // include the 256-sample encoder delay in these positions.
            put_u32(bytes, payload + 36 + 8, start + at9::internal::ENCODER_DELAY);
            put_u32(bytes, payload + 36 + 12, end + at9::internal::ENCODER_DELAY - 1);
            return;
        }
        pos = payload + size + (size & 1);
    }
    throw std::runtime_error("missing smpl chunk");
}

int usage() {
    std::fprintf(stderr,
                 "usage: atrac9-encoder INPUT.f64le OUTPUT.at9 BITRATE_KBPS "
                 "[--whole-loop|--no-loop|--loop START END]\n");
    return 2;
}

} // namespace

int main(int argc, char **argv) {
    try {
        if (argc < 4) return usage();
        const std::string input = argv[1], output = argv[2];
        const int bitrate = std::atoi(argv[3]);
        enum LoopMode { WHOLE, NONE, CUSTOM } mode = WHOLE;
        uint32_t loop_start = 0, loop_end = 0;
        if (argc == 5 && std::string(argv[4]) == "--no-loop") mode = NONE;
        else if (argc == 5 && std::string(argv[4]) == "--whole-loop") mode = WHOLE;
        else if (argc == 7 && std::string(argv[4]) == "--loop") {
            mode = CUSTOM;
            unsigned long long a = std::strtoull(argv[5], nullptr, 10);
            unsigned long long b = std::strtoull(argv[6], nullptr, 10);
            if (a > UINT32_MAX || b > UINT32_MAX) throw std::runtime_error("loop point is too large");
            loop_start = static_cast<uint32_t>(a);
            loop_end = static_cast<uint32_t>(b);
        } else if (argc != 4) {
            return usage();
        }

        std::vector<uint8_t> raw = read_file(input);
        if (raw.empty() || raw.size() % 16 != 0)
            throw std::runtime_error("input must contain interleaved float64 stereo PCM");
        const size_t samples = raw.size() / 16;
        if (samples > UINT32_MAX) throw std::runtime_error("input is too long");
        if (mode == CUSTOM && (loop_start >= loop_end || loop_end > samples))
            throw std::runtime_error("loop must satisfy 0 <= start < end <= input samples");

        std::vector<double> left(samples), right(samples);
        for (size_t i = 0; i < samples; ++i) {
            std::memcpy(&left[i], raw.data() + i * 16, 8);
            std::memcpy(&right[i], raw.data() + i * 16 + 8, 8);
        }
        std::vector<uint8_t> data = at9::detail::encode_atrac9(left, right, bitrate, 0);
        std::vector<uint8_t> file = at9::detail::build_at9(
            bitrate, static_cast<uint32_t>(samples), data, mode != NONE);
        if (mode == CUSTOM) set_custom_loop(file, loop_start, loop_end);
        write_file(output, file);
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "ATRAC9 encoder: %s\n", error.what());
        return 1;
    }
}
