/**
 * Copyright 2023 André Weinand
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

/**
 * Thermal camera firmware that streams raw temperature frames to a host PC.
 *
 * The OLED display has been removed; instead each frame captured from the
 * MLX90640 is sent to the host as a compact binary packet over
 *
 *   - USB CDC, which is always available, and
 *   - a TCP socket, when the board has a wireless chip (Pico W, Pico 2 W, ...).
 *
 * The host (see ../receiver/viewer.py) performs the bilinear interpolation and
 * the colour mapping, so the firmware no longer needs a second core nor an SPI
 * display.
 *
 * Both transports are best effort: when a frame cannot be handed over (no host
 * connected, or the host is not draining fast enough) it is dropped instead of
 * blocking the sensor, and the frame counter lets the host notice the gap.
 */

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include <pico/stdlib.h>
#include <pico/stdio.h>
#include <pico/stdio/driver.h>
#include <pico/stdio_usb.h>
#include <pico/time.h>

extern "C"{
#include <MLX90640_I2C_Driver.h>
#include <MLX90640_API.h>
}

#ifdef STREAM_WIFI
#include <pico/cyw43_arch.h>
extern "C" {
#include <lwip/ip_addr.h>
#include <lwip/netif.h>
#include <lwip/tcp.h>
}
#endif

// tud_cdc_write_available() lets us drop a frame instead of letting the SDK
// block until the host drains the USB endpoint.
extern "C" {
#include <tusb.h>
}

// ---- configuration ----

// MLX90640 32 x 24 Thermopile Array
//
// The sensor delivers a frame in two interleaved halves ("subpages"): one call
// to MLX90640_GetFrameData only fills the pixels of a single subpage, leaving
// the other half holding whatever the previous subpage put there. Combining the
// two subpages therefore needs two reads, which is what MLX90640_GetFrameData_
// and the merge below do.
//
// RESOLUTION and REFRESH_RATE must form a valid pair - the sensor silently
// misbehaves otherwise (see the MLX90640 datasheet):
//
//   resolution   maximum refresh rate
//   16 bit       64 Hz
//   17 bit       32 Hz
//   18 bit       16 Hz
//   19 bit        8 Hz
//
// 18 bit at 16 Hz is a good compromise: at the 32 Hz refresh rate a full
// 32 x 24 image only arrives 16 times per second anyway, so the higher ADC
// resolution costs nothing in effective frame rate but noticeably reduces noise.
//
// Note that a complete image needs two readings (one per subpage), so the image
// rate is half the refresh rate. 17 bit at 32 Hz therefore gives 16 images per
// second, which is the fastest a complete 32 x 24 image can be obtained.
constexpr uint8_t RESOLUTION = 1;               // 0: 16 bit, 1: 17 bit, 2: 18 bit, 3: 19 bit
constexpr uint8_t REFRESH_RATE = 6;             // 0: 0.5 Hz, 1: 1 Hz, 2: 2 Hz, 3: 4 Hz, 4: 8 Hz, 5: 16 Hz, 6: 32 Hz, 7: 64 Hz
constexpr float EMISSIVITY = 0.95;              // the emissivity of the measured object (1.0 = black body)
constexpr float OPENAIR_TA_SHIFT = -8.0;        // for a MLX90640 in the open air the shift is -8 deg Celsius

constexpr uint8_t MLX_I2C_ADDR = 0x33;          // I2C address of the MLX90640

// ---- WiFi configuration ----

// The credentials can be overridden from the CMake command line, e.g.
//
//   cmake -DPICO_BOARD=pico_w \
//         -DSTREAM_WIFI_SSID='"my-ssid"' \
//         -DSTREAM_WIFI_PASSWORD='"my-password"'
//
// An empty SSID disables WiFi; USB streaming keeps working either way.
#ifndef STREAM_WIFI_SSID
#define STREAM_WIFI_SSID ""
#endif
#ifndef STREAM_WIFI_PASSWORD
#define STREAM_WIFI_PASSWORD ""
#endif
#ifndef STREAM_WIFI_PORT
#define STREAM_WIFI_PORT 4242
#endif

// ---- frame protocol ----

// Every frame is a 16 byte little-endian header followed by 768 IEEE 754
// binary16 ("half precision") temperatures in degrees Celsius:
//
//   offset  size  field
//   0       2     magic number (0xAA55)
//   2       1     protocol version
//   3       1     flags (bit 0: payload is made of 16 bit floats)
//   4       4     frame counter (incremented once per transmitted frame)
//   8       4     minimum temperature of the frame (float32, degrees Celsius)
//   12      4     maximum temperature of the frame (float32, degrees Celsius)
//   16      1536  temperatures (float16 degrees Celsius, row major 24 x 32)
//
// The host locates the start of a frame by searching for the magic number, so
// any textual log output emitted by this firmware is simply skipped. The frame
// counter lets the host detect frames that were dropped.

constexpr uint16_t STREAM_MAGIC = 0xAA55;
constexpr uint8_t STREAM_VERSION = 1;
constexpr uint8_t STREAM_FLAG_FLOAT16 = 0x01;

constexpr int STREAM_HEADER_SIZE = 16;
constexpr int STREAM_PIXEL_SIZE = 2;                                        // IEEE 754 binary16
constexpr int STREAM_PAYLOAD_SIZE = MLX90640_PIXEL_NUM * STREAM_PIXEL_SIZE;
constexpr int STREAM_FRAME_SIZE = STREAM_HEADER_SIZE + STREAM_PAYLOAD_SIZE; // 1552 bytes

static_assert(STREAM_FRAME_SIZE == 1552, "unexpected frame size");

// ---- little endian helpers ----

static inline void put_u16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static inline void put_u32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
}

static inline void put_f32(uint8_t *p, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    put_u32(p, bits);
}

// Convert a float to the bit pattern of an IEEE 754 binary16 value, rounding
// half to even exactly like a hardware conversion would.
//
// This is done by hand rather than with the __fp16 type so that the firmware
// does not depend on -mfp16-format (the RP2040 cannot compute with half
// precision at all, and on the RP2350 __fp16 only supports conversions).
// 768 conversions per frame are negligible compared to the I2C transfers.
static inline uint16_t float_to_half(float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));

    const uint32_t sign = (bits >> 16) & 0x8000u;
    const int32_t exponent = (int32_t)((bits >> 23) & 0xFFu) - 127 + 15;    // re-bias to binary16
    uint32_t mantissa = bits & 0x7FFFFFu;

    if (exponent >= 31) {
        // overflows binary16 (also catches infinities and NaNs)
        return (uint16_t)(sign | 0x7C00u);
    }

    if (exponent <= 0) {
        if (exponent < -10) {
            return (uint16_t)sign;                                         // underflows to zero
        }
        // Too small to be a normal binary16 value: restore the implicit leading
        // 1 and shift it down into the subnormal range.
        const uint32_t shift = (uint32_t)(14 - exponent);
        mantissa |= 0x800000u;
        const uint32_t round = (mantissa >> (shift - 1)) & 1u;
        const uint32_t sticky = (mantissa & ((1u << (shift - 1)) - 1u)) != 0;
        uint32_t half = mantissa >> shift;
        if (round && (sticky || (half & 1u))) {
            half++;                                                        // round half to even
        }
        return (uint16_t)(sign | half);
    }

    // Normal range: keep 10 mantissa bits, rounding half to even.
    const uint32_t round = (mantissa >> 12) & 1u;
    const uint32_t sticky = (mantissa & 0xFFFu) != 0;
    uint32_t half = ((uint32_t)exponent << 10) | (mantissa >> 13);
    if (round && (sticky || (half & 1u))) {
        half++;                                                            // a carry correctly bumps the exponent
    }
    return (uint16_t)(sign | half);
}

// ---- output ----

static uint8_t frame_buffer[STREAM_FRAME_SIZE];
static uint32_t frame_counter = 0;

// Write to the USB CDC device directly, bypassing the C library's buffering and
// the LF -> CRLF translation of the stdio layer: the payload is binary and some
// of its bytes are 0x0A.
//
// The CDC FIFO only holds 64 bytes, so a frame cannot be handed over in one
// call. The data is therefore pushed in whatever chunks the FIFO can take, and
// the whole write gives up after a short budget so that a host which has stopped
// reading costs a dropped frame rather than a stalled sensor. (Left to itself
// the SDK would block for up to PICO_STDIO_USB_STDOUT_TIMEOUT_US trying to push
// the data out, which is far longer than a frame interval.)
constexpr uint32_t USB_WRITE_BUDGET_US = 20000;     // 20 ms

static void usb_write(const void *data, size_t length) {
    if (length == 0 || !stdio_usb_connected()) {
        return;
    }

    const uint8_t *cursor = (const uint8_t *)data;
    const uint8_t *end = cursor + length;
    const absolute_time_t deadline = make_timeout_time_us(USB_WRITE_BUDGET_US);

    while (cursor < end && !time_reached(deadline)) {
        const uint32_t available = tud_cdc_write_available();
        if (available == 0) {
            // Give the stdio background task a chance to drain the FIFO. It is
            // not called directly because it must be run under the stdio mutex,
            // which out_chars() below takes care of.
            sleep_us(20);
            continue;
        }
        size_t chunk = (size_t)(end - cursor);
        if (chunk > available) {
            chunk = available;
        }
        stdio_usb.out_chars((const char *)cursor, (int)chunk);
        cursor += chunk;
    }
}

// Diagnostics interleaved with the binary frames; the host skips them.
static void stream_log(const char *format, ...) {
    char buffer[128];
    va_list args;
    va_start(args, format);
    const int length = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    if (length > 0) {
        const size_t used = (size_t)length < sizeof(buffer) ? (size_t)length : sizeof(buffer) - 1;
        usb_write(buffer, used);
    }
}

#ifdef STREAM_WIFI

// ---- WiFi / TCP streaming ----

static struct tcp_pcb *wifi_server_pcb = nullptr;
static struct tcp_pcb *wifi_client_pcb = nullptr;
static bool wifi_configured = false;
static bool wifi_listening = false;
static uint64_t wifi_next_connect_us = 0;

// The lwIP callbacks below run in interrupt context, where printing to USB is
// not allowed (it takes a mutex that the foreground may hold). They only record
// what happened and the main loop does the logging.
static volatile bool wifi_client_connected = false;
static volatile bool wifi_client_lost = false;
static ip_addr_t wifi_client_address;
static u16_t wifi_client_port = 0;

constexpr uint64_t WIFI_RETRY_INTERVAL_US = 5ull * 1000 * 1000;

static void wifi_close_server() {
    if (wifi_server_pcb != nullptr) {
        tcp_arg(wifi_server_pcb, nullptr);
        tcp_accept(wifi_server_pcb, nullptr);
        tcp_close(wifi_server_pcb);
        wifi_server_pcb = nullptr;
    }
}

static void wifi_close_client() {
    if (wifi_client_pcb != nullptr) {
        tcp_arg(wifi_client_pcb, nullptr);
        tcp_recv(wifi_client_pcb, nullptr);
        tcp_err(wifi_client_pcb, nullptr);
        if (tcp_close(wifi_client_pcb) != ERR_OK) {
            tcp_abort(wifi_client_pcb);                                    // e.g. out of memory: kill it the hard way
        }
        wifi_client_pcb = nullptr;
    }
    wifi_client_connected = false;
}

// Called by lwIP when the client sent something. The protocol is one-way, so the
// data is simply consumed; a null pbuf means the client closed the connection.
static err_t wifi_recv_cb(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err) {
    (void)arg;
    (void)err;
    if (p == nullptr) {
        wifi_close_client();
        return ERR_OK;
    }
    tcp_recved(tpcb, p->tot_len);
    pbuf_free(p);
    return ERR_OK;
}

// Called by lwIP when the connection broke; the pcb has already been freed by
// lwIP, so the pointer has to be dismissed rather than closed.
static void wifi_err_cb(void *arg, err_t err) {
    (void)arg;
    (void)err;
    wifi_client_pcb = nullptr;
    wifi_client_lost = true;
}

// Called by lwIP when a client connects. Only a single client is served.
static err_t wifi_accept_cb(void *arg, struct tcp_pcb *new_pcb, err_t err) {
    (void)arg;
    if (err != ERR_OK || new_pcb == nullptr) {
        return ERR_VAL;
    }
    if (wifi_client_pcb != nullptr) {
        tcp_abort(new_pcb);                                                // busy: refuse the additional client
        return ERR_ABRT;
    }
    wifi_client_pcb = new_pcb;
    tcp_arg(new_pcb, nullptr);
    tcp_recv(new_pcb, wifi_recv_cb);
    tcp_err(new_pcb, wifi_err_cb);

    // Remember the peer; the logging happens in the main loop.
    wifi_client_address = new_pcb->remote_ip;
    wifi_client_port = new_pcb->remote_port;
    wifi_client_connected = true;
    return ERR_OK;
}

static bool wifi_link_is_up() {
    return cyw43_wifi_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP;
}

static void wifi_open_server() {
    cyw43_arch_lwip_begin();
    struct tcp_pcb *pcb = tcp_new_ip_type(IPADDR_TYPE_ANY);
    if (pcb == nullptr) {
        cyw43_arch_lwip_end();
        stream_log("Error: cannot allocate a TCP server\n");
        return;
    }
    if (tcp_bind(pcb, IP_ANY_TYPE, STREAM_WIFI_PORT) != ERR_OK) {
        tcp_close(pcb);
        cyw43_arch_lwip_end();
        stream_log("Error: cannot bind TCP port %u\n", (unsigned)STREAM_WIFI_PORT);
        return;
    }
    wifi_server_pcb = tcp_listen_with_backlog(pcb, 1);
    if (wifi_server_pcb == nullptr) {
        tcp_close(pcb);
        cyw43_arch_lwip_end();
        stream_log("Error: cannot listen on TCP port %u\n", (unsigned)STREAM_WIFI_PORT);
        return;
    }
    tcp_arg(wifi_server_pcb, nullptr);
    tcp_accept(wifi_server_pcb, wifi_accept_cb);
    wifi_listening = true;
    // Copy the address while the lock is held, then log once it is released.
    char address[24];
    snprintf(address, sizeof(address), "%s", ip4addr_ntoa(netif_ip4_addr(netif_list)));
    cyw43_arch_lwip_end();

    stream_log("Listening on %s:%u\n", address, (unsigned)STREAM_WIFI_PORT);
}

// Bring up the wireless chip. Returns false if the board has no usable WiFi.
static bool wifi_start() {
    if (STREAM_WIFI_SSID[0] == '\0') {
        stream_log("No WiFi credentials configured, streaming over USB only\n");
        return false;
    }
    if (cyw43_arch_init() != 0) {
        stream_log("Error: cannot initialise the wireless chip\n");
        return false;
    }
    cyw43_arch_enable_sta_mode();
    wifi_configured = true;
    return true;
}

// Called from the main loop: keeps WiFi connected and the server listening
// without ever blocking the sensor for more than a moment.
static void wifi_task() {
    if (!wifi_configured) {
        return;
    }

    // Report connections and disconnections noticed by the lwIP callbacks.
    if (wifi_client_connected) {
        wifi_client_connected = false;
        stream_log("Client %s:%u connected\n",
                   ip4addr_ntoa(&wifi_client_address), (unsigned)wifi_client_port);
    }
    if (wifi_client_lost) {
        wifi_client_lost = false;
        stream_log("Client disconnected\n");
    }

    if (!wifi_link_is_up()) {
        if (wifi_listening) {
            cyw43_arch_lwip_begin();
            wifi_close_client();
            wifi_close_server();
            cyw43_arch_lwip_end();
            wifi_listening = false;
            stream_log("WiFi link lost\n");
        }
        if (time_us_64() >= wifi_next_connect_us) {
            wifi_next_connect_us = time_us_64() + WIFI_RETRY_INTERVAL_US;
            stream_log("Connecting to WiFi \"%s\"...\n", STREAM_WIFI_SSID);
            cyw43_arch_wifi_connect_async(STREAM_WIFI_SSID, STREAM_WIFI_PASSWORD, CYW43_AUTH_WPA2_AES_PSK);
        }
        return;
    }

    if (!wifi_listening) {
        wifi_open_server();
    }
}

// All or nothing: the frame is dropped when the TCP send buffer cannot take it,
// so a slow or stalled client never holds up the sensor.
static void wifi_write(const void *data, size_t length) {
    if (wifi_client_pcb == nullptr) {
        return;
    }

    cyw43_arch_lwip_begin();
    err_t err = ERR_MEM;
    if (wifi_client_pcb != nullptr && tcp_sndbuf(wifi_client_pcb) >= (u16_t)length) {
        err = tcp_write(wifi_client_pcb, data, (u16_t)length, TCP_WRITE_FLAG_COPY);
        if (err == ERR_OK) {
            err = tcp_output(wifi_client_pcb);
        }
    }
    cyw43_arch_lwip_end();

    if (err == ERR_OK) {
        return;
    }
    if (err == ERR_CONN) {
        cyw43_arch_lwip_begin();
        wifi_close_client();
        cyw43_arch_lwip_end();
    }
    // anything else (typically ERR_MEM) is a dropped frame; the frame counter
    // tells the host about it.
}

#endif // STREAM_WIFI

// ---- frame assembly ----

// Read one complete 32 x 24 image into "values".
//
// A single MLX90640_GetFrameData fills only the pixels of one subpage and
// leaves the other half of the array untouched, and MLX90640_CalculateTo then
// only writes the temperatures of that same subpage. Building an image from a
// single reading therefore mixes fresh pixels with pixels left over from the
// previous image, which shows up as a chequerboard that flips on every frame.
//
// So two readings are taken, one per subpage, and each is converted into
// "values" on its own. Converting them separately matters: the sensor's
// compensation pixel and ambient reading belong to a specific subpage
// (MLX90640_CalculateTo picks irDataCP[subpage]), so each half has to be
// compensated with the data captured alongside it.
//
// The readings are repeated until the subpage number changes, which is what
// tells us the other half has arrived. The two halves are then one refresh
// period apart (~31 ms at 32 Hz), which is as close together as the sensor can
// deliver them.
static int read_merged_frame(paramsMLX90640 *params, int pattern_mode, float *values) {
    uint16_t frame[834];
    int converted_subpage = -1;

    for (int attempt = 0; attempt < 6; attempt++) {

        const int status = MLX90640_GetFrameData(MLX_I2C_ADDR, frame);
        if (status < 0) {
            stream_log("Error: MLX90640_GetFrameData returned %d\n", status);
            continue;                           // skip this reading
        }
        const int subpage = status;             // 0 or 1

        const float eta = MLX90640_GetTa(frame, params) + OPENAIR_TA_SHIFT;
        MLX90640_CalculateTo(frame, params, EMISSIVITY, eta, values);

        if (converted_subpage < 0) {
            converted_subpage = subpage;        // first half of the image done
        } else if (subpage != converted_subpage) {
            // Both halves are now refreshed from consecutive captures.
            MLX90640_BadPixelsCorrection(params->brokenPixels, values, pattern_mode, params);
            MLX90640_BadPixelsCorrection(params->outlierPixels, values, pattern_mode, params);
            return subpage;
        }
        // Same half twice (the partner was not ready): convert again and keep
        // looking for the other one.
    }

    if (converted_subpage < 0) {
        return -1;                              // nothing readable at all
    }
    // Best effort: the other half of "values" still holds the previous image,
    // which keeps the stream flowing rather than stalling it.
    MLX90640_BadPixelsCorrection(params->brokenPixels, values, pattern_mode, params);
    MLX90640_BadPixelsCorrection(params->outlierPixels, values, pattern_mode, params);
    return converted_subpage;
}

static void pack_frame(float min, float max, const float *values) {
    uint8_t *header = frame_buffer;
    put_u16(header + 0, STREAM_MAGIC);
    header[2] = STREAM_VERSION;
    header[3] = STREAM_FLAG_FLOAT16;
    put_u32(header + 4, frame_counter++);
    put_f32(header + 8, min);
    put_f32(header + 12, max);

    uint8_t *payload = frame_buffer + STREAM_HEADER_SIZE;
    for (int i = 0; i < MLX90640_PIXEL_NUM; i++) {
        put_u16(payload + 2 * i, float_to_half(values[i]));
    }
}

static void stream_frame() {
    usb_write(frame_buffer, STREAM_FRAME_SIZE);
#ifdef STREAM_WIFI
    wifi_write(frame_buffer, STREAM_FRAME_SIZE);
#endif
}

// ---- main ----

int main() {

    stdio_init_all();
    stdio_set_translate_crlf(&stdio_usb, false);    // frames are binary, 0x0A must not become 0x0D 0x0A

#ifdef STREAM_WIFI
    wifi_start();
#endif

    stream_log("MLX90640 thermal camera: streaming %d byte frames\n", STREAM_FRAME_SIZE);

    sleep_ms(40 + 500); // after Power-On wait a bit for the MLX90640 to initialize

    MLX90640_I2CInit();
    MLX90640_SetResolution(MLX_I2C_ADDR, RESOLUTION);
    MLX90640_SetRefreshRate(MLX_I2C_ADDR, REFRESH_RATE);
    MLX90640_SetChessMode(MLX_I2C_ADDR);

    uint16_t *eeMLX90640 = new uint16_t[832];       // too large for allocating on stack
    MLX90640_DumpEE(MLX_I2C_ADDR, eeMLX90640);
    paramsMLX90640 *params = new paramsMLX90640;    // too large for allocating on stack
    MLX90640_ExtractParameters(eeMLX90640, params);
    delete[] eeMLX90640;

    float *values = new float[MLX90640_PIXEL_NUM];  // too large for allocating on stack
    const int patternMode = MLX90640_GetCurMode(MLX_I2C_ADDR);

    stream_log("Sensor: resolution %d, refresh rate %d, mode %d\n",
               MLX90640_GetCurResolution(MLX_I2C_ADDR),
               MLX90640_GetRefreshRate(MLX_I2C_ADDR),
               patternMode);

    while (true) {

#ifdef STREAM_WIFI
        wifi_task();
#endif

        // read a complete image (both subpages merged) from the MLX90640
        if (read_merged_frame(params, patternMode, values) < 0) {
            continue;
        }

        // find the min and max temperature values of the frame
        float min, max;
        min = max = values[0];
        for (int i = 1; i < MLX90640_PIXEL_NUM; i++) {
            float value = values[i];
            if (value > max)
                max = value;
            if (value < min)
                min = value;
        }

        pack_frame(min, max, values);
        stream_frame();
    }

    return 0;
}