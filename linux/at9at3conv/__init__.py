"""BMK AT9 & AT3 Converter - native Linux port.

ATRAC9 and ATRAC3plus encoding run through bundled native C++ encoders.
FFmpeg handles decoding and resampling; MP3 encoding uses lame or FFmpeg,
and playback uses PipeWire/PulseAudio/ALSA. Wine is not required.
"""

APP_NAME = "BMK AT9 & AT3 Converter"
APP_ID = "at9-at3-converter"
VERSION = "2.7"
