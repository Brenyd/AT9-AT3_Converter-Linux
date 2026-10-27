"""BMK AT9 & AT3 Converter - native Linux port.

Sony's at3tool/at9tool are closed-source Windows binaries, so they run through
Wine. Everything else (resampling, MP3 decode/encode, playback) is native:
ffmpeg, lame and PipeWire/PulseAudio/ALSA.
"""

APP_NAME = "BMK AT9 & AT3 Converter"
APP_ID = "at9-at3-converter"
VERSION = "2.7"
