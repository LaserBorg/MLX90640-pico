cmake_minimum_required(VERSION 3.12)

# initialize the SDK based on PICO_SDK_PATH
# note: this must happen before project()
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(MLX90640_Pico C CXX ASM)

# initialize the Raspberry Pi Pico SDK
pico_sdk_init()

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

add_executable(thermocam
    pico_sdk_version/main.cpp
    pico_sdk_version/MLX90640_PICO_I2C_Driver.cpp
    pico_sdk_version/mlx90640-library/functions/MLX90640_API.c
)

target_include_directories(thermocam PUBLIC
    pico_sdk_version/mlx90640-library/headers
)

# Add pico_stdlib library which aggregates commonly used features
target_link_libraries(thermocam
    pico_stdlib
    hardware_i2c
    hardware_spi
    hardware_gpio
)

# Enable USB, disable UART output
pico_enable_stdio_usb(thermocam 1)
pico_enable_stdio_uart(thermocam 0)

# create map/bin/hex/uf2 file in addition to ELF.
pico_add_extra_outputs(thermocam)