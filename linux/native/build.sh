#!/usr/bin/env bash
# Build the bundled native ATRAC9 and ATRAC3/3+ encoders.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
build="$here/build"
jobs="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

command -v cmake >/dev/null || { echo "cmake is required" >&2; exit 1; }
command -v "${CXX:-c++}" >/dev/null || { echo "a C++17 compiler is required" >&2; exit 1; }

cmake -S "$here/atracdenc" -B "$build/atracdenc" \
    -DCMAKE_BUILD_TYPE=Release -DATRACDENC_PCM_IO_BACKEND=libsndfile
cmake --build "$build/atracdenc" --target atracdenc -j"$jobs"

"${CXX:-c++}" -std=c++17 -O2 -Wall -Wextra -Wno-unused-function \
    "$here/at9/at9_native.cpp" "$here/at9/at9_encoder.cpp" \
    "$here/at9/at9_tables.cpp" -o "$build/atrac9-encoder" -pthread

echo "Built:"
echo "  $build/atrac9-encoder"
echo "  $build/atracdenc/src/atracdenc"
