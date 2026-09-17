#!/usr/bin/env python3
"""
Host side viewer for the MLX90640 thermal camera firmware.

The firmware of every variant in this repository streams the same compact
binary frame for every capture:

    16 byte little-endian header
        0   H   magic number (0xAA55)
        2   B   protocol version
        3   B   flags (bit 0: payload is 16 bit floats, bit 1: 32 bit floats)
        4   I   frame counter
        8   f   minimum temperature of the frame (degrees Celsius)
       12   f   maximum temperature of the frame (degrees Celsius)
    followed by
        768 e   32 x 24 temperatures (degrees Celsius, row major, 1552 byte frame)
     or 768 f   the same values as 32 bit floats (3088 byte frame)

Both payload formats are accepted because the firmware picks between them.
Half precision halves the payload, which matters over WiFi and on the boards
whose firmware cannot convert to it for free; 0.25 degrees near room
temperature is well below the sensor's own noise either way.

This script reads that stream either from the USB CDC device or from the TCP
socket served by boards that have a wireless chip, and renders it: the 32 x 24
grid is scaled up, colour mapped, and shown in a window. Scaling uses nearest
neighbour by default, which keeps every sensor pixel a distinct block; pass
--interpolate for a smooth image instead.

Examples
--------
    # a board streaming over USB
    ./viewer.py --device /dev/ttyACM0

    # Pico W / Pico 2 W over WiFi
    ./viewer.py --host 192.168.1.42 --port 4242

    # or let view.sh find the port by itself
    ./view.sh

Keys
----
    q / ESC   quit
    c         next colour map
    i         toggle bilinear interpolation (off by default)
    h / v     toggle horizontal / vertical flip
    s         save a snapshot (PNG)
    r         reset statistics and the automatic range

The colour scale reserves 62 pixels on the right. Values below are in degrees
Celsius. The scale always covers at least the window given by --min-range
(default 24 to 38 degrees), and never a narrower span than --min-span allows
(default 10 degrees).
"""

import argparse
import struct
import sys
import time

try:
    import cv2
    import numpy as np
    _IMPORT_ERROR = None
except ImportError as exc:  # reported after the arguments have been validated
    cv2 = None
    np = None
    _IMPORT_ERROR = exc


# ---- protocol ----

MAGIC = b"\x55\xaa"                     # 0xAA55, little endian
PROTOCOL_VERSION = 1
FLAG_FLOAT16 = 0x01
FLAG_FLOAT32 = 0x02

COLUMNS = 32
ROWS = 24
PIXELS = COLUMNS * ROWS

HEADER = struct.Struct("<HBBIff")

# The pixels arrive as either IEEE 754 binary16 ('e') or binary32 ('f') values
# and the flags byte says which, so the size of a frame is only known once its
# header has been read. Other flag bits are ignored, which leaves room for
# further payload formats without breaking this parser.
PAYLOAD_FORMATS = {
    FLAG_FLOAT16: struct.Struct(f"<{PIXELS}e"),
    FLAG_FLOAT32: struct.Struct(f"<{PIXELS}f"),
}
PAYLOAD_FLAGS = FLAG_FLOAT16 | FLAG_FLOAT32

FRAME_SIZES = {flag: HEADER.size + fmt.size for flag, fmt in PAYLOAD_FORMATS.items()}
assert FRAME_SIZES == {FLAG_FLOAT16: 1552, FLAG_FLOAT32: 3088}, "unexpected frame size"

SERIAL_TIMEOUT_S = 0.05
TCP_TIMEOUT_S = 0.05


class FrameStream:
    """Turns a byte stream into frames, tolerating partial reads and log text.

    Data is pulled with ``read()``, which returns the bytes that are currently
    available (``b""`` if there are none) and raises ``EOFError`` once the other
    end is gone.
    """

    def __init__(self, read):
        self._read = read
        self.buffer = bytearray()
        self.closed = False
        self.frames = 0
        self.dropped = 0
        self.skipped = 0
        self.counter = None             # the firmware's frame counter
        self._last_counter = None

    def _sync(self):
        """Advance to the next candidate frame start. False = need more data."""
        while True:
            index = self.buffer.find(MAGIC)
            if index >= 0:
                if index:
                    self.skipped += index
                    del self.buffer[:index]
                return True
            # No magic number: discard everything but a trailing 0x55, which may
            # be the first byte of one that got split across two reads.
            keep = 1 if self.buffer.endswith(MAGIC[:1]) else 0
            self.skipped += len(self.buffer) - keep
            del self.buffer[: len(self.buffer) - keep]
            chunk = self._read()
            if not chunk:
                return False
            self.buffer += chunk

    def poll(self):
        """Return ``(minimum, maximum, temperatures)`` or None if no frame yet."""
        try:
            while True:
                if not self._sync():
                    return None

                # The header is a fixed size, so it can be decoded on its own;
                # only then is the length of the payload known.
                while len(self.buffer) < HEADER.size:
                    chunk = self._read()
                    if not chunk:
                        return None
                    self.buffer += chunk

                magic, version, flags, counter, minimum, maximum = HEADER.unpack_from(self.buffer)
                payload_format = PAYLOAD_FORMATS.get(flags & PAYLOAD_FLAGS)
                if magic != 0xAA55 or version != PROTOCOL_VERSION or payload_format is None:
                    del self.buffer[:2]         # false positive: resynchronise
                    continue

                frame_size = HEADER.size + payload_format.size
                while len(self.buffer) < frame_size:
                    chunk = self._read()
                    if not chunk:
                        return None
                    self.buffer += chunk

                values = payload_format.unpack_from(self.buffer, HEADER.size)
                del self.buffer[:frame_size]

                # The counter is bumped once per transmitted frame, so a gap
                # means the firmware had to drop frames (host too slow, no link,
                # ...). Counting modulo 2**32.
                if self._last_counter is not None:
                    self.dropped += (counter - self._last_counter - 1) & 0xFFFFFFFF
                self._last_counter = counter
                self.counter = counter
                self.frames += 1
                return minimum, maximum, values
        except EOFError:
            self.closed = True
            return None


# ---- sources ----

def open_serial(device, baud):
    import serial

    try:
        port = serial.Serial(device, baud, timeout=SERIAL_TIMEOUT_S)
    except serial.SerialException as exc:
        sys.exit(
            f"Error: cannot open {device}: {exc}\n"
            "On Linux you may need to be in the 'dialout' group (then log out and in again):\n"
            "    sudo usermod -aG dialout $USER"
        )

    def read():
        try:
            return port.read(65536)
        except serial.SerialException as exc:
            raise EOFError(str(exc)) from exc

    return read, f"USB {device}"


def open_socket(host, port):
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TCP_TIMEOUT_S)
    try:
        sock.connect((host, port))
    except OSError as exc:
        sys.exit(f"Error: cannot connect to {host}:{port}: {exc}")

    def read():
        try:
            data = sock.recv(65536)
        except socket.timeout:
            return b""
        if not data:
            raise EOFError("the connection was closed")
        return data

    return read, f"TCP {host}:{port}"


# ---- colour maps ----

# The 7 stop gradient used by the original firmware.
THERMAL_STOPS = [
    [0, 0, 0],          # black
    [4, 51, 255],       # blue
    [0, 253, 255],      # cyan
    [0, 249, 0],        # green
    [255, 255, 0],      # yellow
    [255, 38, 0],       # red
    [255, 255, 255],    # white
]


def build_thermal_lut():
    stops = np.array(THERMAL_STOPS, dtype=np.float32)[:, ::-1]   # RGB -> BGR for OpenCV
    positions = np.linspace(0.0, 1.0, 256)
    xs = np.linspace(0.0, 1.0, len(stops))
    lut = np.stack([np.interp(positions, xs, stops[:, channel]) for channel in range(3)], axis=1)
    return np.clip(lut + 0.5, 0, 255).astype(np.uint8).reshape(256, 1, 3)


COLORMAPS = {}
COLORMAP_NAMES = ["thermal", "turbo", "jet", "inferno", "magma", "hot", "gray"]


def init_colormaps():
    COLORMAPS["thermal"] = build_thermal_lut()
    COLORMAPS["turbo"] = cv2.COLORMAP_TURBO
    COLORMAPS["jet"] = cv2.COLORMAP_JET
    COLORMAPS["inferno"] = cv2.COLORMAP_INFERNO
    COLORMAPS["magma"] = cv2.COLORMAP_MAGMA
    COLORMAPS["hot"] = cv2.COLORMAP_HOT
    COLORMAPS["gray"] = cv2.COLORMAP_BONE


# ---- rendering ----

# Geometry of the temperature scale drawn to the right of the image.
SCALE_BAR_WIDTH = 70         # total strip added to the right of the thermal image
SCALE_BAR_MARGIN = 12        # vertical margin above and below the bar
SCALE_BAR_THICKNESS = 14     # width of the gradient bar itself
SCALE_TICKS = 5              # number of labelled ticks, including both ends


class Viewer:
    def __init__(self, args):
        self.args = args
        self.colormap = args.colormap
        # Nearest neighbour by default: each sensor pixel stays a distinct
        # block, which is an honest view of the 32 x 24 grid. Interpolation
        # invents detail between pixels, so it is opt-in via --interpolate.
        self.smooth = args.interpolate
        self.flip_h = args.flip_h
        self.flip_v = args.flip_v
        self.fixed_range = None
        if args.range:
            self.fixed_range = (args.range[0], args.range[1])
        # The colour scale is stretched over at least this many degrees. Without
        # a floor, a scene with a narrow temperature range would map sensor
        # noise (0.14 K RMS on a -BAA) onto the full colour range and look
        # extremely grainy.
        self.min_span = max(0.0, args.min_span)
        # An absolute window the scale always covers, as opposed to min_span
        # which only sets a width. This keeps the colours still from frame to
        # frame whenever the scene sits inside the window, instead of the whole
        # image shifting colour because one pixel moved.
        self.min_range = None
        if args.min_range and list(args.min_range) != [0.0, 0.0]:
            self.min_range = (float(args.min_range[0]), float(args.min_range[1]))
        self.show_scale = not args.no_scale
        # The scene range is smoothed over time, otherwise a single noisy hot
        # pixel would re-scale the whole image from frame to frame. Kept
        # responsive so the colours do not visibly lag the scene.
        self.range_smoothing = 0.35
        self._smooth_range = None
        self.fps = 0.0
        self.counter_fps = 0.0
        self._fps_samples = []
        # Temporal averaging: keeps the last N frames and averages them, which
        # suppresses the sensor's per-pixel noise at the cost of motion blur.
        self.temporal = max(1, args.temporal)
        self._history = []

    def average_frames(self, minimum, maximum, values):
        """Average the last few frames to reduce sensor noise.

        Takes and returns (minimum, maximum, values), the same order the frame
        stream uses, so the two can be chained without reordering.
        """
        if self.temporal <= 1:
            return minimum, maximum, values

        self._history.append(values)
        if len(self._history) > self.temporal:
            self._history.pop(0)

        if len(self._history) == 1:
            return minimum, maximum, values

        frames = np.asarray(self._history, dtype=np.float32)
        averaged = frames.mean(axis=0)
        # The reported range has to follow the averaged data, otherwise the
        # colour scaling would drift away from what is displayed.
        return float(averaged.min()), float(averaged.max()), averaged

    def color_range(self, minimum, maximum):
        """Work out the temperature range to map onto the colour scale.

        A fixed range wins. Otherwise the range of the current frame is used,
        but widened so that it always covers min_range and spans at least
        min_span degrees, so that a nearly uniform view does not stretch its own
        noise across the whole colour scale.
        """
        if self.fixed_range:
            return self.fixed_range

        # Smooth the scene range over time so that noise on a single pixel does
        # not make the whole image flicker between colour scales.
        if self._smooth_range is None:
            self._smooth_range = (minimum, maximum)
        else:
            alpha = self.range_smoothing
            previous_low, previous_high = self._smooth_range
            self._smooth_range = (previous_low + alpha * (minimum - previous_low),
                                  previous_high + alpha * (maximum - previous_high))

        low, high = self._smooth_range

        # Cover the minimum window first. It is an absolute floor on the scale
        # rather than a fixed range, so a scene hotter or colder than the window
        # still scales to its own range, and doing this before the span test
        # below keeps the result as tight as it can be.
        if self.min_range is not None:
            low = min(low, self.min_range[0])
            high = max(high, self.min_range[1])

        span = high - low
        if self.min_span > 0 and span < self.min_span:
            centre = (low + high) / 2.0
            low = centre - self.min_span / 2.0
            high = centre + self.min_span / 2.0

        if high - low <= 0:
            # A perfectly uniform frame: give the scale something to work with.
            low -= 0.5
            high += 0.5

        return low, high

    def colorize(self, values, minimum, maximum):
        grid = np.asarray(values, dtype=np.float32).reshape(ROWS, COLUMNS)
        if self.flip_h:
            grid = grid[:, ::-1]
        if self.flip_v:
            grid = grid[::-1, :]

        low, high = self.color_range(minimum, maximum)
        span = high - low
        if span <= 0:
            span = 1.0
        normalized = np.clip((grid - low) / span, 0.0, 1.0)

        interpolation = cv2.INTER_LINEAR if self.smooth else cv2.INTER_NEAREST
        scale = self.args.scale
        # The sensor grid is 32 x 24 (4:3), so scale the smaller dimension and
        # derive the other, rather than drawing it onto a square and stretching
        # the image vertically.
        width = scale
        height = int(round(scale * ROWS / COLUMNS))
        enlarged = cv2.resize(normalized, (width, height), interpolation=interpolation)
        eight_bit = np.clip(enlarged * 255.0 + 0.5, 0, 255).astype(np.uint8)
        image = cv2.applyColorMap(eight_bit, COLORMAPS[self.colormap])

        if self.show_scale:
            image = self.draw_temperature_scale(image, low, high)
        return image

    def draw_temperature_scale(self, image, low, high):
        """Append a strip on the right showing the gradient and its temperatures."""
        height, width = image.shape[:2]
        strip = np.zeros((height, SCALE_BAR_WIDTH, 3), dtype=np.uint8)

        # The gradient runs from the top of the bar (hot) to the bottom (cold),
        # using the same colour map as the image but read from low to high so
        # the top of the bar matches the brightest colour.
        bar_height = height - 2 * SCALE_BAR_MARGIN
        ramp = np.linspace(0.0, 1.0, bar_height, dtype=np.float32)
        ramp = np.clip(ramp * 255.0 + 0.5, 0, 255).astype(np.uint8).reshape(-1, 1)
        gradient = cv2.applyColorMap(ramp, COLORMAPS[self.colormap])
        gradient = cv2.resize(gradient, (SCALE_BAR_THICKNESS, bar_height),
                              interpolation=cv2.INTER_NEAREST)
        gradient = gradient[::-1]                   # hottest at the top

        bar_x = SCALE_BAR_MARGIN
        strip[SCALE_BAR_MARGIN:SCALE_BAR_MARGIN + bar_height, bar_x:bar_x + SCALE_BAR_THICKNESS] = gradient

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.30
        thickness = 1
        # The numbers sit a clear distance from the tick marks, otherwise the
        # ticks read as minus signs and a temperature of 41 looks like -41.
        text_x = bar_x + SCALE_BAR_THICKNESS + 8

        span = high - low
        top_label_bottom = SCALE_BAR_MARGIN
        for tick in range(SCALE_TICKS):
            fraction = tick / (SCALE_TICKS - 1)           # 0 at the bottom (cold)
            temperature = low + fraction * span
            y = int(SCALE_BAR_MARGIN + bar_height - fraction * (bar_height - 1))
            y = max(SCALE_BAR_MARGIN, min(height - SCALE_BAR_MARGIN, y))

            # A short tick mark, kept dim so it reads as part of the bar.
            cv2.line(strip, (bar_x + SCALE_BAR_THICKNESS, y), (bar_x + SCALE_BAR_THICKNESS + 3, y),
                     (140, 140, 140), 1)
            # One decimal for every tick. The range is usually fractional (the
            # minimum window guarantees that), and rounding the intermediate
            # ticks to whole degrees would print a number the tick does not
            # stand for - 34.25 is not 34. Negative values keep their sign,
            # which is all that is needed to tell them apart.
            label = f"{temperature:.1f}"
            (text_w, text_h), _ = cv2.getTextSize(label, font, font_scale, thickness)
            text_y = max(text_h, min(height - 1, y + text_h // 2))
            if tick == SCALE_TICKS - 1:
                top_label_bottom = text_y
            # Only draw a label if it fits in the strip.
            if text_x + text_w < SCALE_BAR_WIDTH:
                cv2.putText(strip, label, (text_x, text_y), font, font_scale,
                            (0, 0, 0), thickness + 2, cv2.LINE_AA)
                cv2.putText(strip, label, (text_x, text_y), font, font_scale,
                            (255, 255, 255), thickness, cv2.LINE_AA)

        # The unit sits just under the hottest label, where it reads as part of
        # the scale rather than as another temperature.
        unit_y = min(height - 2, top_label_bottom + 11)
        cv2.putText(strip, "C", (text_x, unit_y), font, font_scale,
                    (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(strip, "C", (text_x, unit_y), font, font_scale,
                    (170, 170, 170), thickness, cv2.LINE_AA)

        return np.hstack([image, strip])

    def display_fps(self):
        """The frame rate to show: the firmware's, falling back to wall clock."""
        return self.counter_fps if self.counter_fps else self.fps

    def draw_overlay(self, image, minimum, maximum, dropped):
        height, width = image.shape[:2]
        pad = 6
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.34
        thickness = 1

        low, high = self.color_range(minimum, maximum)
        lines = [
            f"{self.display_fps():4.1f} fps",
            f"scene {minimum:5.1f} - {maximum:5.1f} C",
            f"scale {low:5.1f} - {high:5.1f} C",
            f"{self.colormap}{'' if self.smooth else ' / nearest'}"
            + (f" avg{self.temporal}" if self.temporal > 1 else ""),
        ]
        if dropped:
            lines.append(f"dropped {dropped}")

        y = pad + 8
        for line in lines:
            cv2.putText(image, line, (pad, y), font, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
            cv2.putText(image, line, (pad, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
            y += 13

        hint = "q quit  c colors  i interp  h/v flip  s save"
        cv2.putText(image, hint, (pad, height - pad), font, 0.28, (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(image, hint, (pad, height - pad), font, 0.28, (200, 200, 200), thickness, cv2.LINE_AA)

    def update_fps(self, counter=None):
        """Track the frame rate over a sliding window.

        Timing between individual frames is useless here: USB delivers a frame
        in ~30 chunks and the OS hands them over in bursts, so consecutive
        parses can be microseconds apart while the sensor is really running at
        32 Hz. Averaging the firmware's frame counter over roughly a second
        gives the true rate no matter how the data is batched.
        """
        now = time.monotonic()

        if counter is None:
            return

        self._fps_samples.append((now, counter))
        # Keep a window of about one second, but always at least two samples.
        while len(self._fps_samples) > 2 and now - self._fps_samples[0][0] > 1.0:
            self._fps_samples.pop(0)

        t0, c0 = self._fps_samples[0]
        elapsed = now - t0
        if elapsed > 0 and len(self._fps_samples) >= 2:
            # The counter is 32 bit and wraps; advancing is modulo 2**32.
            advanced = (counter - c0) & 0xFFFFFFFF
            self.counter_fps = advanced / elapsed
            self.fps = self.counter_fps

    def handle_key(self, key, image):
        if key in (ord("q"), 27):                       # q or ESC
            return False
        if key == ord("c"):
            index = (COLORMAP_NAMES.index(self.colormap) + 1) % len(COLORMAP_NAMES)
            self.colormap = COLORMAP_NAMES[index]
            print(f"Colour map: {self.colormap}")
        elif key == ord("i"):
            self.smooth = not self.smooth
            print(f"Interpolation: {'bilinear' if self.smooth else 'nearest neighbour'}")
        elif key == ord("h"):
            self.flip_h = not self.flip_h
        elif key == ord("v"):
            self.flip_v = not self.flip_v
        elif key == ord("s"):
            if image is None:
                print("Nothing to save yet")
            else:
                name = time.strftime("thermal_%Y%m%d_%H%M%S.png")
                cv2.imwrite(name, image)
                print(f"Saved {name}")
        elif key == ord("r"):
            self.fixed_range = None
            self._smooth_range = None
            self._history.clear()
        return True


def parse_args():
    parser = argparse.ArgumentParser(
        description="View the thermal camera frame stream on the host.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-d", "--device", help="serial device of the USB stream, e.g. /dev/ttyACM0")
    source.add_argument("-H", "--host", help="host name or address of a board streaming over TCP")
    parser.add_argument("-p", "--port", type=int, default=4242, help="TCP port (default: 4242)")
    parser.add_argument("--baud", type=int, default=115200, help="serial baud rate (ignored by USB CDC)")
    parser.add_argument("-c", "--colormap", default="thermal", choices=COLORMAP_NAMES,
                        help="colour map to start with (default: thermal)")
    parser.add_argument("-s", "--scale", type=int, default=512,
                        help="width the 32x24 image is scaled to (default: 512); the height "
                             "follows to keep the 4:3 aspect ratio")
    parser.add_argument("-i", "--interpolate", action="store_true",
                        help="smooth the upscaled image with bilinear interpolation. Off by "
                             "default, which shows each 32x24 sensor pixel as a solid block")
    parser.add_argument("--flip-h", action=argparse.BooleanOptionalAction, default=True,
                        help="mirror horizontally. The MLX90640 reads its pixels out mirrored "
                             "relative to the scene, so this is on by default; use --no-flip-h "
                             "if the image comes out backwards")
    parser.add_argument("--flip-v", action=argparse.BooleanOptionalAction, default=False,
                        help="mirror vertically (default: off)")
    parser.add_argument("--range", type=float, nargs=2, metavar=("MIN", "MAX"),
                        help="use a fixed temperature range instead of one per frame")
    parser.add_argument("-t", "--temporal", type=int, default=1, metavar="N",
                        help="average N frames to reduce sensor noise (default: 1, off). "
                             "The MLX90640 has 0.14 K of noise per pixel (BAA, at 1 Hz), so "
                             "2-8 noticeably cleans the image at the cost of motion blur")
    parser.add_argument("-m", "--min-span", type=float, default=10.0, metavar="DEGREES",
                        help="stretch the colour scale over at least this many degrees "
                             "(default: 10). Guards against a nearly uniform scene being "
                             "magnified into noise; too large a value wastes colour range "
                             "and washes the image out. Use 0 to always fit the scene exactly")
    parser.add_argument("--min-range", type=float, nargs=2, default=(24.0, 38.0),
                        metavar=("MIN", "MAX"),
                        help="the colour scale always covers at least this window of "
                             "temperatures (default: 24 38), so a scene cooler than MIN or "
                             "hotter than MAX still scales to its own range. Unlike --range "
                             "this is a floor, not a fixed range. Use 0 0 to cover only the "
                             "scene range")
    parser.add_argument("--no-scale", action="store_true",
                        help="do not draw the temperature gradient scale")

    args = parser.parse_args()
    if list(args.min_range) != [0.0, 0.0] and args.min_range[0] >= args.min_range[1]:
        parser.error("--min-range MIN must be smaller than MAX (use 0 0 to disable)")
    return args


def main():
    args = parse_args()

    if _IMPORT_ERROR is not None:
        sys.exit(
            f"Error: {_IMPORT_ERROR.name} is required to display the stream. Install the dependencies with:\n"
            "    python3 -m pip install opencv-python numpy pyserial"
        )

    init_colormaps()

    if args.device:
        read, description = open_serial(args.device, args.baud)
    else:
        read, description = open_socket(args.host, args.port)

    stream = FrameStream(read)
    viewer = Viewer(args)

    print(f"Reading thermal frames from {description}")
    print("Waiting for data... (is the firmware running and the sensor connected?)")

    window = "MLX90640 thermal camera"
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)

    try:
        while True:
            frame = stream.poll()
            if frame is not None:
                minimum, maximum, values = frame
                minimum, maximum, values = viewer.average_frames(minimum, maximum, values)
                viewer.update_fps(stream.counter)
                image = viewer.colorize(values, minimum, maximum)
                viewer.draw_overlay(image, minimum, maximum, stream.dropped)
                cv2.imshow(window, image)

            if stream.closed:
                print("The stream ended.", file=sys.stderr)
                break

            key = cv2.waitKey(1) & 0xFF
            if key != 0xFF and not viewer.handle_key(key, image if frame is not None else None):
                break

            if frame is None:
                # Nothing to do: keep the window responsive without spinning.
                time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()

    print(f"Received {stream.frames} frames, {stream.dropped} dropped, {stream.skipped} bytes skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())