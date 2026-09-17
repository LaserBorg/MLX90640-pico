# Host side viewer

`viewer.py` reads the frame stream that the firmware of every variant in this
repository sends over USB (or WiFi, on the builds that support it) and shows it
as a colour mapped 32 x 24 thermal image.

- `view.sh` sets up the Python environment, finds the serial port by itself and
  starts the viewer, which is the easiest way in.
- `viewer.py` is the viewer itself, for when you want to control the
  environment or the arguments.
- `requirements.txt` lists the dependencies.

Only `pico_sdk_version/` streams over WiFi; the Arduino, CircuitPython and
MicroPython variants stream over USB only.

## Quick start

```sh
cd receiver
./view.sh
```

The script creates `.venv` next to itself on first use, installs the
dependencies, picks the first serial port that exists and runs the viewer:

```sh
./view.sh --colormap turbo
./view.sh --host 192.168.1.42 --port 4242      # a board streaming over WiFi
./view.sh --no-flip-h                          # if the image comes out mirrored
```

To run it directly instead:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python viewer.py --device /dev/ttyACM0
```

On Linux you may need to be in the `dialout` group (log out and back in for it
to take effect):

```sh
sudo usermod -aG dialout "$USER"
```

## Keys

| key   | action                         |
| ----- | ------------------------------ |
| `q`   | quit (or `ESC`)                |
| `c`   | next colour map                |
| `i`   | toggle bilinear interpolation  |
| `h`   | mirror horizontally            |
| `v`   | mirror vertically              |
| `s`   | save a PNG snapshot            |
| `r`   | reset the automatic range      |

## Options

| option            | meaning                                                          |
| ----------------- | ---------------------------------------------------------------- |
| `-d`, `--device`  | serial device of the USB stream, e.g. `/dev/ttyACM0`             |
| `-H`, `--host`    | host name or address of a board streaming over TCP               |
| `-p`, `--port`    | TCP port (default: 4242)                                         |
| `--baud`          | serial baud rate; ignored by USB CDC                             |
| `-c`, `--colormap`| colour map to start with (default: `thermal`)                    |
| `-s`, `--scale`   | width to scale the 32 x 24 grid to (default: 512)                |
| `-i`, `--interpolate` | smooth the image instead of showing each sensor pixel as a block |
| `--flip-h` / `--no-flip-h` | mirror horizontally (on by default)                      |
| `--flip-v` / `--no-flip-v` | mirror vertically (off by default)                       |
| `--range MIN MAX` | fixed temperature range instead of one per frame                 |
| `-t`, `--temporal N` | average N frames to reduce sensor noise (default: 1, off)     |
| `-m`, `--min-span`| stretch the colour scale over at least this many degrees         |
| `--no-scale`      | do not draw the temperature gradient scale                       |

`--device` and `--host` are mutually exclusive and one of them is required when
you run `viewer.py` on its own; `view.sh` fills in `--device` for you.

## Rendering

Scaling is done with **nearest neighbour by default**, so each of the 768 sensor
pixels is drawn as a solid block and the image shows exactly what the sensor
measured. Press `i` (or pass `--interpolate`) for bilinear smoothing, which
looks softer but invents detail between pixels that the sensor never captured.

The grid is drawn at its correct 4:3 proportions: `--scale` sets the width and
the height follows, so the image is never stretched into a square.

A temperature gradient is drawn to the right of the image, showing the colours
and the temperatures they stand for, so temperatures can be read straight off
the display. The overlay reports both the scene's own range and the range the
colours are actually mapped to, along with the frame rate and the number of
frames the firmware had to drop.

The colour scale covers at least 10 °C (`--min-span`, use `0` to always fit the
scene exactly). This floor exists so that a nearly uniform scene is not
magnified into noise, but keep it modest: too large a value wastes the ends of
the colour ramp and a scene with real contrast then comes out flat. The widened
range is centred on the scene, so nothing is clipped, and it is smoothed over
time so a single noisy pixel cannot rescale the whole image.

The image is mirrored horizontally by default, because the MLX90640 reads its
pixels out mirrored relative to the scene. Use `--no-flip-h` if that is not what
you want.

The MLX90640 has around 1 °C of noise per pixel, so `--temporal 2` to `8`
noticeably cleans the image up at the cost of some motion blur.

## The frame protocol

Every frame is a 16 byte little-endian header followed by the temperatures. The
host finds the start of a frame by searching for the magic number, so any
diagnostic text printed by the firmware is skipped automatically. The frame
counter increments once per frame the firmware produced, which is how the viewer
knows that frames were dropped.

| offset | size  | field                                             |
| ------ | ----- | ------------------------------------------------- |
| 0      | 2     | magic number, `0xAA55`                            |
| 2      | 1     | protocol version (currently 1)                    |
| 3      | 1     | flags: bit 0 = payload is float16, bit 1 = float32 |
| 4      | 4     | frame counter                                     |
| 8      | 4     | minimum temperature of the frame (°C, float32)    |
| 12     | 4     | maximum temperature of the frame (°C, float32)    |
| 16     | 1536 or 3072 | 768 temperatures (°C, row major 24 x 32)     |

The layout is little-endian on both ends. The payload is either

- 768 IEEE 754 **binary16** values (flag bit 0), a 1552 byte frame, or
- 768 IEEE 754 **binary32** values (flag bit 1), a 3088 byte frame.

The viewer accepts both and uses the flags byte to tell them apart, so the
firmwares are free to pick whichever is convenient:

| variant               | payload | why                                              |
| --------------------- | ------- | ------------------------------------------------ |
| `pico_sdk_version/`   | float16 | half the bytes over USB and WiFi; the RP2040 converts to it cheaply |
| `pico_arduino/`       | float32 | the library hands out floats                      |
| `pico_circuitpython/` | float32 | ulab stores floats, converting 768 per frame is not worth it |
| `pico_micropython/`   | float32 | same                                             |

Half precision costs about 0.25 °C near room temperature, which is far below the
sensor's own noise, but it halves the payload — 50 KB/s instead of 99 KB/s at
16 frames per second. Unused flag bits are ignored by the viewer, which leaves
room for further payload formats (8 bit quantised images, for instance).

## Troubleshooting

- **`no serial port found`** — is the board plugged in and running the firmware?
  `ls /dev/ttyACM*` should list it. If a different port appears (or a board that
  needs `--host`), pass `--device`/`--host` explicitly or set `VIEWER_PORT`.
- **Permission denied on the port** — add yourself to `dialout` (see above).
- **"Waiting for data..." forever** — the board is not sending. Check the
  console for the firmware's own log output; for the MicroPython and
  CircuitPython variants, make sure the firmware is actually running (they print
  `setup camera...` / `start image read loop...`).
- **The image is upside down or mirrored** — press `h` or `v`, or start with
  `--no-flip-h`.
- **The image is grainy** — that is the sensor. Raise `--temporal` or
  `--min-span`.