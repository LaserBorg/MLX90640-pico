'''
based on
https://github.com/mwerezak/micropython-mlx90640/tree/master

using micropython 1.23 build with ulab:
https://github.com/v923z/micropython-builder
https://micropython-ulab.readthedocs.io/en/latest/
'''

import sys
import math

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

FRAME_MARKER = b'\xAA\xAA'
FRAME_SHAPE = (NUM_ROWS, NUM_COLS)

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

            # Convert the numpy matrix to bytes, prepend the frame marker and send it to stdout
            sys.stdout.buffer.write(FRAME_MARKER + self.frame.tobytes())

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
