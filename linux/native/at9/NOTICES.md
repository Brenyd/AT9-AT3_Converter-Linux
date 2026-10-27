# Native ATRAC9 encoder notices

- `at9.h`, `at9_internal.h`, `at9_encoder.cpp`, and `at9_native.cpp` use the
  C++ ATRAC9 encoder from [psgfx](https://github.com/elripalda/psgfx), derived
  from [PS5 AT9 Converter](https://github.com/blackbearreloaded/ps5-at9-converter)
  v0.1.0 (commit 65be0a1). Copyright (C) 2026 BlackBearReloaded;
  GPL-3.0-or-later. See `LICENSE` in this directory.
- `at9_tables.h` and `at9_tables.cpp` contain ATRAC9 constant tables from
  [LibAtrac9](https://github.com/Thealexbarney/LibAtrac9), MIT License,
  Copyright (c) 2018 Alex Barney. The full MIT notice is preserved in the
  source file headers.

The encoder is original native C++ code. It does not contain, invoke, or wrap
Sony's proprietary `at9tool` executable.
