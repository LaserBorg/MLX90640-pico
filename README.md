# MLX90640 based Thermal Camera for Raspberry Pi Pico (RP2040)

A simple but fast Thermal Imaging Camera using the MLX90640 sensor and USB-CDC

## Code Based on
- unmodified Melexis Driver: https://github.com/melexis/mlx90640-library/
- the [Pico SDK](https://www.raspberrypi.com/documentation/microcontrollers/c_sdk.html)

## Hardware
- Raspberry Pi Pico or Pico W (RP2040)
- MLX90640 Thermal Camera Breakout (55º or 110º), e.g. [Pimoroni](https://shop.pimoroni.com/products/mlx90640-thermal-camera-breakout)

### Wiring

Connect the MLX90640 to the 3.3 V Pin 36 of the Raspberry Pi Pico.

| MLX90640 | RP2040   | GPIO | Pin |
| -------- | -------- | ---- | --- |
| SDA      | I2C1 SDA | 26   | 31  |
| SDC      | I2C1 SCL | 27   | 32  |


## Building
- make sure the "[Pico SDK](https://www.raspberrypi.com/documentation/microcontrollers/c_sdk.html)" is installed and the environment variable "PICO_SDK_PATH" refers to it.
- `git clone https://github.com/LaserBorg/MLX90640_pico.git -b main --depth 1`
- `cd MLX90640_pico`
- `git submodule init`
- `git submodule update --recursive`
- `mkdir build`
- `cd build`
- `export PICO_SDK_PATH=/home/flip/pico/pico-sdk`
- `cmake .. -DPICO_BOARD=pico_w`
- `make -j8`

See [`pico_sdk_version/README.md`](pico_sdk_version/README.md) for the WiFi
build and the streaming details.

## Viewing the image

Every variant streams raw temperature frames to a host PC and the same viewer
renders them, so there is one tool to learn:

```sh
cd receiver
./view.sh
```

The launcher creates its own virtual environment on first use, finds the serial
port by itself and opens the viewer window. The frame format, the viewer's keys
and options, and the troubleshooting notes are in
[`receiver/README.md`](receiver/README.md).

## Firmware variants

All four send the same frame format (a 16 byte header followed by the 768
temperatures) and are read by the same viewer.

| variant               | language      | transport    | pixels  |
| --------------------- | ------------- | ------------ | ------- |
| `pico_sdk_version/`   | C, Pico SDK   | USB and WiFi | float16 |
| `pico_micropython/`   | MicroPython   | USB          | float32 |
| `pico_circuitpython/` | CircuitPython | USB          | float32 |
| `pico_arduino/`       | Arduino C++   | USB          | float32 |

`pico_sdk_version/` is the actively developed one: it merges the sensor's two
subpages into complete images, drops frames rather than stalling when the host
cannot keep up, and can stream over WiFi on boards with a wireless chip. The
other three are ports of the same idea to the other languages. The old
`receiver/receive.py` they used originally understood only the bare frame
marker those ports sent, and has been replaced by `receiver/viewer.py`.


## other

- heat map inspiration: http://www.andrewnoske.com/wiki/Code_-_heatmaps_and_color_gradients


## MLX90640 implementations:

product:

pimoroni 56€
https://shop.pimoroni.com/products/mlx90640-thermal-camera-breakout?variant=12536948654163

adafruit 75$ (stemma)
https://learn.adafruit.com/adafruit-mlx90640-ir-thermal-camera

waveshare 80$ (Quiic)
https://www.waveshare.com/mlx90640-d55-thermal-camera.htm
wiki: https://www.waveshare.com/wiki/MLX90640-D55_Thermal_Camera

Seeed
https://www.seeedstudio.com/blog/2019/11/25/mlx90640-thermal-imaging-cameras-for-your-microcontroller/

-------

Guide https://github.com/EverythingSmartHome/mlx90640-thermal-camera
https://everythingsmarthome.co.uk/building-a-raspberry-pi-thermal-imaging-camera-mlx90640-guide/

https://www.instructables.com/Infrared-Thermal-Imaging-Camera-With-MLX90640-and-/

----

melexis official C driver
https://github.com/melexis/mlx90640-library/

adafruit C driver fork
https://github.com/adafruit/Adafruit_MLX90640/

-----

Pico SDK using Melexis driver
https://github.com/VianPatel/mlx90640-RPI-Pico


arduino sample using adafruit fork
https://github.com/adafruit/Adafruit_MLX90640/blob/master/examples/MLX90640_simpletest/MLX90640_simpletest.ino


waveshare Python, C, Arduino
https://files.waveshare.com/upload/5/56/MLX90640_Thermal_Camera_Code.7z

Pimoroni C with Python bindings
https://github.com/pimoroni/mlx90640-library

---------

STM32F7
https://www.optolab.ftn.uns.ac.rs/index.php/education/project-base/260-mlx90640-thermal-camera
https://github.com/OptoLAB/MLX90640-Thermal-Camera-STM32-STemWin


--------

arduino

adafruit arcada example:
https://learn.adafruit.com/adafruit-mlx90640-ir-thermal-camera/arduino-thermal-camera

https://github.com/sparkfun/SparkFun_MLX90640_Arduino_Example


--------

micropython:
https://github.com/mwerezak/micropython-mlx90640/tree/master

------

circuitpython
driver:
https://docs.circuitpython.org/projects/mlx90640/en/latest/

example:
https://learn.adafruit.com/adafruit-mlx90640-ir-thermal-camera/python-circuitpython

forked for different screen:
https://github.com/dglaude/circuitpython_phat_on_pico/blob/main/mlx90640_240x240.py


---------
CPython (Pi4?)

Seeed/Grove (Adafruit fork)
https://github.com/Seeed-Studio/Seeed_Python_MLX9064x
https://pypi.org/project/seeed-python-mlx90640/
(requires https://github.com/Seeed-Studio/grove.py)


Pi4 using Adafruit circuitpython + blinka (3fps)
https://makersportal.com/blog/2020/6/8/high-resolution-thermal-camera-with-raspberry-pi-and-mlx90640

Pimoroni python rewrite
https://github.com/a-kore/mlx90640-python

