cmake_minimum_required(VERSION 3.13)

# ---- WiFi configuration ----
#
# These are only used when the board has a wireless chip (PICO_BOARD=pico_w,
# pico2_w, ...). Pass them on the cmake command line, e.g.
#
#   cmake -DPICO_BOARD=pico_w \
#         -DSTREAM_WIFI_SSID='"my-ssid"' \
#         -DSTREAM_WIFI_PASSWORD='"my-password"' ..
#
# Note the quoting: the values are compiled into the firmware as C string
# literals. An empty SSID leaves WiFi disabled and the firmware streams over
# USB only.
#
set(STREAM_WIFI_SSID "" CACHE STRING "WiFi SSID to join (empty disables WiFi)")
set(STREAM_WIFI_PASSWORD "" CACHE STRING "WiFi password")
set(STREAM_WIFI_PORT 4242 CACHE STRING "TCP port to listen on")

# initialize the SDK based on PICO_SDK_PATH
# note: this must happen before project()
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(thermocam C CXX ASM)

# initialize the Raspberry Pi Pico SDK
pico_sdk_init()

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# PICO_CYW43_SUPPORTED is set by the board header (boards/pico_w.h,
# boards/pico2_w.h, ...) whenever the board carries a wireless chip, so the
# firmware automatically gains WiFi support on those boards without any change
# here.
if (PICO_CYW43_SUPPORTED)
    message("Board has a wireless chip: WiFi streaming enabled")
else()
    message("Board has no wireless chip: USB streaming only")
endif()

add_executable(thermocam
    main.cpp
    MLX90640_PICO_I2C_Driver.cpp
    ./mlx90640-library/functions/MLX90640_API.c
)

target_include_directories(thermocam PUBLIC
    ./mlx90640-library/headers
)

# I2C pins for the MLX90640. GPIO 26/27 are on I2C1; GPIO 16/17 would be I2C0.
# Change these if your sensor is wired to different pins.
set(PIN_I2C_SDA 26 CACHE STRING "GPIO used for I2C SDA")
set(PIN_I2C_SCL 27 CACHE STRING "GPIO used for I2C SCL")

target_compile_definitions(thermocam PRIVATE
    PIN_I2C_SDA=${PIN_I2C_SDA}
    PIN_I2C_SCL=${PIN_I2C_SCL}
)

target_link_libraries(thermocam
    pico_stdlib
    hardware_i2c
    hardware_gpio
)

if (PICO_CYW43_SUPPORTED)
    target_link_libraries(thermocam
        pico_cyw43_arch_lwip_threadsafe_background
    )
    target_compile_definitions(thermocam PRIVATE
        STREAM_WIFI=1
        STREAM_WIFI_SSID="${STREAM_WIFI_SSID}"
        STREAM_WIFI_PASSWORD="${STREAM_WIFI_PASSWORD}"
        STREAM_WIFI_PORT=${STREAM_WIFI_PORT}
    )
endif()

# Enable USB output (the frames are streamed over USB CDC) and disable the
# UART so that the stdio driver list contains only the one driver we write to.
pico_enable_stdio_usb(thermocam 1)
pico_enable_stdio_uart(thermocam 0)

# create map/bin/hex/uf2 file in addition to ELF.
pico_add_extra_outputs(thermocam)