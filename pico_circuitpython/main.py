'''
send data and log transfer rate.
'''

import time
import board                        # type: ignore
import busio                        # type: ignore
import adafruit_mlx90640            # type: ignore
from usb_cdc import data as ser     # type: ignore
from ulab import numpy as np        # type: ignore
# import asyncio

FRAME_MARKER = b'\xAA\xAA'
FRAME_SHAPE = (24, 32)
NUM_PIXELS = FRAME_SHAPE[0] * FRAME_SHAPE[1]

i2c = busio.I2C(board.GP17, board.GP16, frequency=1000000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
frame = [0] * 768

def read_sensor_data():  # async
    """Read data from the sensor and return it as bytes."""
    try:
        mlx.getFrame(frame)
    except ValueError:
        return None

    # Convert the frame data to a 24x32 numpy matrix
    return np.array(frame, dtype=np.float).reshape(FRAME_SHAPE)


def send_data(data):  # async
    """Convert the numpy matrix to bytes, prepend the frame marker, send data and return the number of bytes sent."""
    data = FRAME_MARKER + data.tobytes()
    ser.write(data)
    return len(data)


def main():  # async
    while True:
        sensor_data = read_sensor_data()  # await
        if sensor_data is not None:
            send_data(sensor_data)  # await


if __name__ == "__main__":
    try:
        main()  # asyncio.run(main())

    except Exception as e:
        print(f"An error occurred: {e}")
