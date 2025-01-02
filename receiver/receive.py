'''
receive MLX90640 sensor data from USB and display it
'''

import serial
import struct
import numpy as np
import cv2
import matplotlib.cm as cm
from matplotlib import colors
import time


def read_thermal_from_serial(ser):
    frame_marker_len = len(frame_marker)
    buffer = bytearray(frame_marker_len)
    buffer_ptr = 0
    bytes_since_marker = 0

    while True:
        # Read one byte
        byte = ser.read(1)
        if byte:
            # Add the byte to the current position in the buffer and increment the pointer
            buffer[buffer_ptr] = byte[0]
            buffer_ptr = (buffer_ptr + 1) % frame_marker_len
            bytes_since_marker += 1

            # Check if the buffer matches the frame marker
            if bytes(buffer[buffer_ptr:] + buffer[:buffer_ptr]) == frame_marker:
                bytes_since_marker = 0

                # Read bytes until we have enough data for a full frame
                data = bytearray(num_pixels * 4)
                data_ptr = 0
                while data_ptr < num_pixels * 4:
                    chunk = ser.read(num_pixels * 4 - data_ptr)
                    data[data_ptr:data_ptr+len(chunk)] = chunk
                    data_ptr += len(chunk)
                if data_ptr == num_pixels * 4:

                    # Convert the bytes to a numpy array
                    image = np.array(struct.unpack('f' * num_pixels, data)).astype(np.float32).reshape(frame_shape)

                    # Flip the image vertically
                    image = np.flip(image, 0)

                    # print(image.min(), image.max())

                    return image


def display_thermal(thermal_map, size=(320, 240), vdefault=(20, 40)):

    def apply_cmap(thermal_map, vdefault, vmin=None, vmax=None):
        # If vmin and vmax are not specified, set them to min and max within a reasonable range
        if vmin is None:
            vmin = min(np.min(thermal_map), vdefault[0])  # np.percentile(thermal_map, 5)
        if vmax is None:
            vmax = max(np.max(thermal_map), vdefault[1])  # np.percentile(thermal_map, 95)

        # Normalize the thermal map to the vmin-vmax range and apply the colormap
        cmap = cm.ScalarMappable(cmap='CMRmap', norm=colors.Normalize(vmin=0, vmax=1))
        normalized_map = (thermal_map - vmin) / (vmax - vmin)
        cmapped_image = cmap.to_rgba(normalized_map)

        cmapped_image = cv2.cvtColor((cmapped_image * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        return cmapped_image

    cmapped_image = apply_cmap(thermal_map.copy(), vdefault)
    cmapped_image = cv2.resize(cmapped_image, size, interpolation = cv2.INTER_NEAREST)
    cv2.imshow('Image', cmapped_image)


if __name__ == "__main__":
    port = "/dev/ttyACM1"  # 'COM3'
    frame_marker = b'\xAA\xAA'
    frame_shape = (24, 32)  # MLX90640: 32 rows, 24 columns
    num_pixels = frame_shape[0] * frame_shape[1]

    with serial.Serial(port, 921600, timeout=1) as ser:  # baudrate is irrelevant for USB-CDC
        while True:
            thermal_map = read_thermal_from_serial(ser)
            if thermal_map is not None:
                display_thermal(thermal_map)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                print("Resynchronizing...")

    cv2.destroyAllWindows()
