# MLX90640 Thermal Camera streaming to a host PC

A thermal imaging camera using the MLX90640 sensor on a Raspberry Pi Pico, which
streams its images to a host PC instead of rendering them on an attached display.

## Features

- streams complete 32 x 24 temperature frames: 8 images per second by default,
  up to 16
- sends raw temperatures, so the host does the smoothing, scaling and colouring
- two transports from a single code base:
  - **USB** (CDC) on every board
  - **WiFi** (TCP) on boards that have a wireless chip: Pico W, Pico 2 W, and
    other boards with a CYW43 chip
- both transports are best effort: if a frame cannot be handed over it is
  dropped (and reported) rather than slowing the sensor down
- the shared Python viewer in [`../receiver/`](../receiver/README.md) is
  included, with several colour maps

## Why streaming is cheap

Rendering on the device was by far the most expensive part of the original
project: a 128 x 128 RGB565 display frame is 32 KB, i.e. about **740 KB/s** at
23 fps, all of it over SPI.

The sensor data itself is tiny: a frame is 32 x 24 = 768 values, and the
default setting sends 8 of them per second. Sending them as 16 bit floats costs

| format            | per frame | at 8 images/s | at 16 images/s |
| ----------------- | --------- | ------------- | -------------- |
| float16 (default) | 1 552 B   | 12 KB/s       | 25 KB/s        |
| float32           | 3 088 B   | 25 KB/s       | 49 KB/s        |

25 KB/s (about 200 kbit/s) is trivial for both USB CDC and WiFi, which is why
this works without a display, without a second core, and without any compression.

### Why float16 is enough

An MLX90640-BAA has a noise level of 0.14 K RMS at 1 Hz (see
[Framerate](#framerate)), and the emissivity and ambient temperature calibration
that the host still has to apply are worth several degrees. Half precision
quantises these temperatures in steps of 0.016 °C at room temperature, rising to
0.25 °C at the sensor's 300 °C maximum, so it is far below the sensor's own noise
at every temperature it can measure — free accuracy-wise, while halving the
payload. The frame minimum and maximum are sent as float32 so the displayed
range is exact.

## Hardware

- Raspberry Pi Pico (RP2040), Pico 2 (RP2350), Pico W or Pico 2 W
- MLX90640 Thermal Camera Breakout (55º or 110º), e.g.
  [Pimoroni](https://shop.pimoroni.com/products/mlx90640-thermal-camera-breakout)
- Optional: battery module, e.g.
  [LiPo Charger/Booster module](https://www.sparkfun.com/products/14411)

### Wiring

Connect the MLX90640 to the 3.3 V Pin 36 of the Raspberry Pi Pico.

| MLX90640 | RP2040   | GPIO | Pin |
| -------- | -------- | ---- | --- |
| SDA      | I2C1 SDA | 26   | 31  |
| SDC      | I2C1 SCL | 27   | 32  |

GPIO 26 and 27 are on **I2C1** (GPIO 16 and 17 would be I2C0). The firmware
defaults to 26/27 and the I2C bus is chosen to match the pins, because those
pins cannot reach I2C0. To use different pins, configure them at build time:

```sh
cmake -DPIN_I2C_SDA=16 -DPIN_I2C_SCL=17 ..
```

That is all the wiring that is needed: there is no display and no touch button
any more. The I2C bus runs at 1 MHz; if your jumper wires are long and the
sensor is not detected, lower it in `MLX90640_PICO_I2C_Driver.cpp`.

## Building

Make sure the [Pico SDK](https://www.raspberrypi.com/documentation/microcontrollers/c_sdk.html)
is installed and that the environment variable `PICO_SDK_PATH` points at it.

```sh
git clone https://github.com/weinand/thermal-imaging-camera
cd thermal-imaging-camera
git submodule init
git submodule update
mkdir build
cd build
```

Then configure and build for the board you have.

### Pico or Pico 2 (USB)

```sh
cmake -DPICO_BOARD=pico2 ..        # or -DPICO_BOARD=pico
make thermocam
```

### Pico W or Pico 2 W (USB and WiFi)

Boards with a wireless chip additionally need the SDKs `lwip` and `cyw43-driver`
submodules:

```sh
cd "$PICO_SDK_PATH"
git submodule update --init lib/lwip lib/cyw43-driver
cd -
```

Then build, passing the network credentials. Note the quoting: the values are
compiled into the firmware as C string literals.

```sh
cmake -DPICO_BOARD=pico_w \
      -DSTREAM_WIFI_SSID='"my-network"' \
      -DSTREAM_WIFI_PASSWORD='"my-password"' \
      ..
make thermocam
```

Leaving `STREAM_WIFI_SSID` empty builds a firmware that streams over USB only,
which is a convenient way to check the wiring first.

## Using it

Put the board in BOOTSEL mode (hold the BOOTSEL button while plugging in the USB
cable, then release) and copy `thermocam.uf2` onto the `RP2350`/`RPI-RP2` drive
that appears, or flash it with `picotool`:

```sh
picotool load build/thermocam.uf2 && picotool reboot
```

The board then comes back up as a USB serial device. The easiest way to view the
feed is the launcher script, which sets up the Python environment on first use
and finds the serial port by itself:

```sh
./view.sh
```

`view.sh` is a thin wrapper around the shared viewer in
[`../receiver/`](../receiver/README.md), which all four firmware variants use.
For example, to start with a different colour map:

```sh
./view.sh --colormap turbo
```

To run the viewer directly instead, install the dependencies into a virtual
environment (a virtual environment is needed because many distributions ship
Python without `pip` access to system packages):

```sh
python3 -m venv ../receiver/.venv
../receiver/.venv/bin/pip install -r ../receiver/requirements.txt
```

### Over USB

```sh
../receiver/view.sh --device /dev/ttyACM0
```

On Linux you may need to be in the `dialout` group (log out and back in for it
to take effect):

```sh
sudo usermod -aG dialout "$USER"
```

### Over WiFi

The firmware prints its address on the USB serial console when it connects;
alternatively look for the board in your router's list of clients.

```sh
./view.sh --host 192.168.1.42 --port 4242
```

The port can be changed at build time with `-DSTREAM_WIFI_PORT=...`.

### Viewer keys and options

The viewer's keys, its command line options and how it renders the image are
documented in [`../receiver/README.md`](../receiver/README.md).

## Framerate

The MLX90640 has a 3 bit refresh rate field, so there are exactly eight rates.
The sensor measures one *subpage* per interval, and a complete 32 x 24 image
takes two subpages, so the image rate is always half the refresh rate:

| refresh rate | new subpage every | complete images per second |
| ------------ | ----------------- | -------------------------- |
| 0.5 Hz       | 2 s               | 0.25                       |
| 1 Hz         | 1 s               | 0.5                        |
| 2 Hz         | 500 ms            | 1                          |
| 4 Hz         | 250 ms            | 2                          |
| 8 Hz         | 125 ms            | 4                          |
| 16 Hz        | 62.5 ms           | 8                          |
| 32 Hz        | 31.25 ms          | 16                         |
| 64 Hz        | 15.6 ms           | 32                         |

### Noise

The datasheet specifies the noise at a **1 Hz refresh rate** as NETD (Noise
Equivalent Temperature Difference), in table 14:

| device        | average | min | standard deviation |
| ------------- | ------- | --- | ------------------ |
| MLX90640-BAA  | 0.14 K  | 0.1 K | 0.05 K           |
| MLX90640-BAB  | 0.25 K  | 0.2 K | 0.05 K           |

The BAA is the 110° x 75° version and the BAB the 55° x 35° one, so the wider
lens is the quieter of the two. The datasheet also shows the noise varying over
the frame (figures 20 to 23): because of the lens, pixels in the corners are
noisier than those in the middle, and noise rises at low temperatures and falls
at high ones.

Note that the underlying thermopile noise is fixed; what changes with the
refresh rate is how long each measurement integrates. Longer integration averages
more of that noise away, which is why the specification quotes the rate
explicitly. Treat the figures above as the 1 Hz reference point rather than as
what you will see at 32 Hz.

### Resolution

Control register 1 has a separate 2 bit field selecting the ADC resolution:
16, 17, 18 or 19 bit. The datasheet (section 12.3) states only that "increasing
the resolution decreases the quantization noise and improves the overall noise
performance" — more bits is strictly better for noise, with no stated cost in
frame rate or timing.

The default is 18 bit, which is also what the device is calibrated at. Changing
it requires the VDD correction described in section 11.2.2.1; the Melexis driver
in this repository handles that in `MLX90640_GetVdd`.

### The rate and resolution pairing

Changing the resolution does not change how often a subpage is measured, but the
ADC needs longer to convert at the higher settings, and drivers in the wild agree
on one pairing in which each extra bit doubles the integration time:

| refresh rate | integration time | maximum resolution |
| ------------ | ---------------- | ------------------ |
| 0.5 Hz       | 64 ms            | 19 bit             |
| 1 Hz         | 32 ms            | 19 bit             |
| 2 Hz         | 16 ms            | 19 bit             |
| 4 Hz         | 8 ms             | 19 bit             |
| 8 Hz         | 4 ms             | 19 bit             |
| 16 Hz        | 2 ms             | 18 bit             |
| 32 Hz        | 1 ms             | 17 bit             |
| 64 Hz        | 0.5 ms           | 16 bit             |

Written as a formula, the integration time is `32 / refresh rate` milliseconds.
The table is self-consistent: 16 bit needs 0.5 ms and 19 bit needs 4 ms, and 2^3
= 8 times the integration time buys exactly the 3 extra bits. Stay inside it, and
always take the highest resolution the chosen rate allows.

This constraint is **not stated in the datasheet** — do not go looking for it
there. The datasheet lists the eight rates and the four resolutions as
independent register fields, with no table relating them. The pairing above is
the convention the Melexis, Pimoroni and Adafruit drivers follow, it is
consistent with the fact that a device only needs to finish one subpage per
interval, and it is the safe choice. If you want to deviate, measure it: the
sensor is documented to misbehave rather than to report an error.

### Choosing a setting

This firmware defaults to **18 bit at 16 Hz**: 8 complete images per second,
with twice the integration time of the previous 32 Hz default.

| refresh rate | resolution | image rate | integration time |
| ------------ | ---------- | ---------- | ---------------- |
| 32 Hz        | 17 bit     | 16 /s      | 1 ms             |
| 16 Hz        | 18 bit     | 8 /s       | 2 ms             |
| 8 Hz         | 19 bit     | 4 /s       | 4 ms             |
| 2 Hz         | 19 bit     | 1 /s       | 16 ms            |

Change `RESOLUTION` and `REFRESH_RATE` in `main.cpp` and rebuild. Lowering the
frame rate is a better way to reduce noise than averaging frames on the host,
because a longer integration time captures more signal in every frame, whereas
host-side averaging (`--temporal` in the viewer) buys the same reduction by
smearing motion across frames. The two combine.

Two other things follow from the table:

- **At 32 Hz the I2C bus is already the limit.** A 1 MHz bus manages about 32
  reads per second, i.e. 16 complete images, which is exactly what 32 Hz yields,
  so that setting has no headroom left. Lower rates relieve the bus as well.
- **Half of the bandwidth is untouched by any of this.** The payload is 1 552
  bytes per image either way, so 8 images per second is about 12 KB/s rather
  than 25 KB/s.

### Where pedantry is due

The eight refresh rates and their register encoding are from the datasheet
(table "Control register 1", section 10.4), as is the 1 Hz NETD table above and
the statement that the datasheet's own noise figures are given at 1 Hz. The
integration time column is the documented convention rather than a datasheet
specification — see the warning above.

## Why the image needs two readings

The MLX90640 delivers each 32 x 24 image in two interleaved halves, called
subpages. One call to `MLX90640_GetFrameData` fills only the pixels of a single
subpage, and `MLX90640_CalculateTo` then only converts those same pixels.

Using one reading per frame therefore produces an image that is half fresh and
half left over from the previous frame, which is visible as a chequerboard that
flips with every frame. The firmware instead takes two readings, one per
subpage, and converts each one with the compensation data that was captured
alongside it. A complete image therefore costs two readings, which is why the
image rate is half the sensor's refresh rate — see
[Framerate](#framerate).

## The frame protocol

Every frame is a 16 byte little-endian header followed by the temperatures. The
host finds the start of a frame by searching for the magic number, so any
diagnostic text printed by the firmware is skipped automatically. The frame
counter increments once per transmitted frame, which is how the viewer knows
that frames were dropped.

| offset | size  | field                                              |
| ------ | ----- | -------------------------------------------------- |
| 0      | 2     | magic number, `0xAA55`                             |
| 2      | 1     | protocol version (currently 1)                     |
| 3      | 1     | flags: bit 0 = payload is float16, bit 1 = float32 |
| 4      | 4     | frame counter                                      |
| 8      | 4     | minimum temperature of the frame (°C, float32)     |
| 12     | 4     | maximum temperature of the frame (°C, float32)     |
| 16     | 1536  | 768 temperatures (°C, float16, row major 24 x 32)  |

This firmware always sets flag bit 0 and sends the compact 1552 byte form. Flag
bit 1 selects a float32 payload instead (3088 bytes), which the other firmware
variants in this repository use because their languages convert to half
precision less conveniently. The viewer accepts both and tells them apart from
the flags byte, so the layout leaves room for further payload formats (8 bit
quantised images, for instance) without breaking existing hosts.

## Code based on

- the unmodified Melexis driver: https://github.com/melexis/mlx90640-library/
- heat map colours inspired by:
  http://www.andrewnoske.com/wiki/Code_-_heatmaps_and_color_gradients
- the [Pico SDK](https://www.raspberrypi.com/documentation/microcontrollers/c_sdk.html)
