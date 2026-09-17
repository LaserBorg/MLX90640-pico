'''
based on
https://github.com/mwerezak/micropython-mlx90640/tree/master

using micropython 1.23 build with ulab:
https://github.com/v923z/micropython-builder
https://micropython-ulab.readthedocs.io/en/latest/

Frames leave the board on stdout, which is the USB CDC interface, using the
format shared by every variant in this repository: a 16 byte little-endian
header followed by the 768 temperatures. See ../receiver/README.md for the
protocol and ../receiver/viewer.py for the host side.

The temperatures are sent as 32 bit floats rather than the 16 bit floats the
Pico SDK build uses: ulab stores them that way, and converting 768 of them per
frame would have to be done by hand. The header says which format the payload
uses, so the viewer does not care.
'''

import math
import struct
import sys

from ulab import numpy as np            # type: ignore
from machine import Pin, I2C            # type: ignore
import uasyncio                         # type: ignore
import micropython                      # type: ignore

import mlx90640
from mlx90640.calibration import NUM_ROWS, NUM_COLS, TEMP_K
from mlx90640.image import ChessPattern, InterleavedPattern


PIN_I2C_SDA = Pin(16, Pin.IN, Pin.PULL_UP)
PIN_I2C_SCL = Pin(17, Pin.IN, Pin.PULL_UP)

I2C_CAMERA = I2C(id=0, scl=PIN_I2C_SCL, sda=PIN_I2C_SDA)

FRAME_SHAPE = (NUM_ROWS, NUM_COLS)

MAGIC = 0xAA55                          # 0x55 0xAA on the wire, little endian
PROTOCOL_VERSION = 1
FLAG_FLOAT32 = 0x02
HEADER_FORMAT = '<HBBIff'               # magic, version, flags, counter, min, max
FRAME_SIZE = struct.calcsize(HEADER_FORMAT) + NUM_ROWS * NUM_COLS * 4

# The USB buffer can fill up when the host stops reading. Rather than blocking
# the sensor, give up after a couple of tries; the host resynchronises on the
# next frame's magic number.
WRITE_RETRIES = 4


class Config:
    def __init__(self):
        self.refresh_rate = 8
        self.debug = False
        self.pattern = ChessPattern  # InterleavedPattern
        self.bad_pixels = (34, 35)
        

class CameraLoop:
    def __init__(self):
        config = Config()

        self.camera = mlx90640.detect_camera(I2C_CAMERA)
        self.camera.set_pattern(config.pattern)  
        self.camera.refresh_rate = config.refresh_rate
        self._refresh_period = math.ceil(1000/self.camera.refresh_rate)

        self.bad_pix = config.bad_pixels
        self.debug = config.debug

        self.update_event = uasyncio.Event()
        self.state = None
        self.frame_object = None
        self.frame = np.zeros(FRAME_SHAPE, dtype=np.float)
        self.counter = 0


    async def run(self):
        await uasyncio.sleep_ms(80 + 2 * int(self._refresh_period))
        print("setup camera...")
        self.camera.setup()

        tasks = [self.stream_images(),]
        if self.debug:
            tasks.append(self.print_mem_usage())
        await uasyncio.gather(*tasks)


    async def wait_for_data(self):
        await uasyncio.wait_for_ms(self._wait_inner(), int(self._refresh_period))


    async def _wait_inner(self):
        while not self.camera.has_data:
            await uasyncio.sleep_ms(50)


    async def send_frame(self):
        """Write one wire frame: the header followed by the temperatures.

        The frame counter increments for every frame the sensor produced, even
        when the send below has to give up, so the host can see that frames
        were missed.
        """
        header = struct.pack(HEADER_FORMAT, MAGIC, PROTOCOL_VERSION, FLAG_FLOAT32,
                             self.counter,
                             float(self.frame.min()), float(self.frame.max()))
        self.counter = (self.counter + 1) & 0xFFFFFFFF

        # tobytes() writes the values row major in 32 bit floats, which is the
        # order the sensor reports and the order the wire format expects.
        data = header + self.frame.tobytes()
        view = memoryview(data)
        stream = sys.stdout.buffer

        sent = 0
        for _ in range(WRITE_RETRIES):
            written = stream.write(view[sent:])
            sent += written if written else len(data)
            if sent >= len(data):
                break
            # A short write means the USB buffer is full: let the other tasks
            # run and try the rest again.
            await uasyncio.sleep_ms(1)


    async def stream_images(self):
        print("start image read loop...")
        sp = 0

        while True:
            await self.wait_for_data()
            
            self.camera.read_image(sp)

            self.state = self.camera.read_state()
            self.frame_object = self.camera.process_image(sp, self.state)

            # # Interpolate bad pixels
            # self.frame_object.interpolate_bad_pixels(self.bad_pix)

            for row in range(NUM_ROWS):
                for col in range(NUM_COLS):
                    idx = row * NUM_COLS + col
                    self.frame[row, col] = self.frame_object.calc_temperature(idx, self.state)

            await self.send_frame()

            sp = int(not sp)
            self.update_event.set()

            await uasyncio.sleep_ms(int(self._refresh_period * 0.8))


    async def print_mem_usage(self):
        while True:
            await uasyncio.sleep(5)
            micropython.mem_info()


if __name__ == "__main__":
    main = CameraLoop()
    uasyncio.run(main.run())
