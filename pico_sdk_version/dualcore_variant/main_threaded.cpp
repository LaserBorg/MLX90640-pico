/**
 * Copyright 2023 André Weinand, 
 * forked and modified 2024 Philip Gutjahr
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#include <cstdio>
#include <ctime>
#include <math.h>
#include <pico/stdlib.h>
#include <pico/multicore.h>
#include "tusb_config.h"
#include <tusb.h> // TinyUSB

#include <pico/mutex.h>
#include <mutex>  // REMOVE THAT THEN?

mutex_t mtx; // Mutex for synchronizing access to the values array

extern "C"{
#include <MLX90640_I2C_Driver.h>
#include <MLX90640_API.h>
}

extern "C" void core1_entry() {
    while (true) {
        tud_task(); // TinyUSB task. Must be called in the main loop

        mutex_enter_blocking(&mtx); // Lock the mutex before accessing the values array

        // send frame marker
        uint8_t marker[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        tud_cdc_write(marker, 4);

        // send temperature values
        tud_cdc_write(values, MLX90640_PIXEL_NUM * sizeof(float));

        mutex_exit(&mtx); // Unlock the mutex after we're done with the values array
    }
}


// ---- configuration ----

// MLX90640 32 x 24 Thermopile Array
constexpr uint8_t REFRESH_RATE = 6;             // 0: 0.5 Hz, 1: 1 Hz, 2: 2 Hz, 3: 4 Hz, 4: 8 Hz, 5: 16 Hz, 6: 32 Hz, 7: 64 Hz
constexpr float EMISSIVITY = 0.95;              // the emissivity of the measured object (1.0 = black body)
constexpr float OPENAIR_TA_SHIFT = -8.0;        // for a MLX90640 in the open air the shift is -8 deg Celsius

constexpr uint8_t MLX_I2C_ADDR = 0x33;          // I2C address of the MLX90640

float values[MLX90640_PIXEL_NUM];            // array for the temperature values of the MLX90640 pixels

int main() {

    stdio_init_all();
    tusb_init();

    sleep_ms(40 + 500); // after Power-On wait a bit for the MLX90640 to initialize

    MLX90640_I2CInit();
    MLX90640_SetResolution(MLX_I2C_ADDR, 3);    // 0: 16 bit, 1: 17 bit, 2 = 18 bit, 3 = 19 bit
    MLX90640_SetRefreshRate(MLX_I2C_ADDR, REFRESH_RATE);
    MLX90640_SetChessMode(MLX_I2C_ADDR);

    uint16_t *eeMLX90640 = new uint16_t[832];       // too large for allocating on stack 
    MLX90640_DumpEE(MLX_I2C_ADDR, eeMLX90640);
    paramsMLX90640 *params = new paramsMLX90640;    // too large for allocating on stack 
    MLX90640_ExtractParameters(eeMLX90640, params);
    delete eeMLX90640;

    uint16_t *captureFrame = new uint16_t[834];     // too large for allocating on stack 
    int patternMode = MLX90640_GetCurMode(MLX_I2C_ADDR);

    multicore_launch_core1(core1_entry);

    while (true) {
        // read pages (half frames) from the MLX90640
        int status = MLX90640_GetFrameData(MLX_I2C_ADDR, captureFrame);
        if (status < 0) {
            printf("Error: MLX90640_GetFrameData returned %d\n", status);
            continue;   // skip this frame
        }
        float eTa = MLX90640_GetTa(captureFrame, params) + OPENAIR_TA_SHIFT;

        mutex_enter_blocking(&mtx); // Lock the mutex before accessing the values array

        MLX90640_CalculateTo(captureFrame, params, EMISSIVITY, eTa, values);
        MLX90640_BadPixelsCorrection(params->brokenPixels, values, patternMode, params);
        MLX90640_BadPixelsCorrection(params->outlierPixels, values, patternMode, params);

        mutex_exit(&mtx); // Unlock the mutex after we're done with the values array
    }

    delete captureFrame;
    delete params;
    return 0;
}
