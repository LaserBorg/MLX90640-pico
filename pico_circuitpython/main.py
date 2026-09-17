'''
Stream MLX90640 frames to a host over USB CDC.

The frame format is the one shared by every variant in this repository: a 16
byte little-endian header followed by the 768 temperatures. See
../receiver/README.md for the protocol and ../receiver/viewer.py for the host
side.

This firmware sends the temperatures as 32 bit floats rather than the 16 bit
floats the Pico SDK build uses, because ulab stores its arrays as 32 bit floats
and converting 768 of them per frame would have to be done by hand. The flag in
the header tells the viewer which format to expect, so the difference is
invisible to it.
'''

import struct
import time

import board                        # type: ignore
import busio                        # type: ignore
import adafruit_mlx90640            # type: ignore
from usb_cdc import data as ser     # type: ignore
from ulab import numpy as np        # type: ignore

PIXEL_ROWS = 24
PIXEL_COLS = 32
NUM_PIXELS = PIXEL_ROWS * PIXEL_COLS

MAGIC = 0xAA55                      # 0x55 0xAA on the wire, little endian
PROTOCOL_VERSION = 1
FLAG_FLOAT32 = 0x02

HEADER_FORMAT = '<HBBIff'           # magic, version, flags, counter, min, max
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
FRAME_SIZE = HEADER_SIZE + NUM_PIXELS * 4

SCL_PIN = board.GP27
SDA_PIN = board.GP26

# Report the transfer rate on the console (the other USB interface, not the one
# the frames go to). Off by default, because a console nobody reads fills up
# and then the printing blocks, which would stall the stream.
DEBUG = False

i2c = busio.I2C(SCL_PIN, SDA_PIN, frequency=1000000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
frame = [0.0] * NUM_PIXELS


def read_frame():
    """Read one frame from the sensor, or None if the reading failed."""
    try:
        mlx.getFrame(frame)
    except ValueError:
        return None
    # ulab keeps the values as 32 bit floats and tobytes() writes them in the
    # same row major order the sensor reports, which is the wire order.
    return np.array(frame, dtype=np.float)


def pack_frame(values, counter):
    """Build one wire frame: the header followed by the temperatures."""
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    header = struct.pack(HEADER_FORMAT, MAGIC, PROTOCOL_VERSION, FLAG_FLOAT32,
                         counter, minimum, maximum)
    return header + values.tobytes()


def main():
    counter = 0
    frames = 0
    window_start = time.monotonic()

    while True:
        values = read_frame()
        if values is None:
            continue

        # Without a host attached the write would only fill the USB buffer, so
        # there is no point building the frame. The counter advances either
        # way, which is how the host can tell that frames were missed.
        if ser.connected:
            ser.write(pack_frame(values, counter))
        counter = (counter + 1) & 0xFFFFFFFF
        frames += 1

        if DEBUG and time.monotonic() - window_start >= 5.0:
            now = time.monotonic()
            print(f"{frames / (now - window_start):.1f} fps, {FRAME_SIZE} bytes/frame")
            frames = 0
            window_start = now


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"An error occurred: {e}")
