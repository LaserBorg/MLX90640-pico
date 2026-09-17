// https://learn.adafruit.com/adafruit-mlx90640-ir-thermal-camera/arduino-thermal-camera
// https://github.com/adafruit/Adafruit_MLX90640/blob/master/examples/MLX90640_arcadaCam/MLX90640_arcadaCam.ino
//
// Streams MLX90640 frames to a host over USB CDC.
//
// The frame format is the one shared by every variant in this repository: a 16
// byte little-endian header followed by the 768 temperatures. See
// ../receiver/README.md for the protocol and ../receiver/viewer.py for the
// host side.
//
// The temperatures are sent as 32 bit floats (flag bit 1), which is what the
// library hands out, so no conversion is needed.

#include <Wire.h>
#include <Adafruit_MLX90640.h>

#define WIDTH 32
#define HEIGHT 24
#define NUM_PIXELS (WIDTH * HEIGHT)

#define SDA_PIN 16
#define SCL_PIN 17
TwoWire myWire = TwoWire(SDA_PIN, SCL_PIN);

Adafruit_MLX90640 mlx;

float frame[NUM_PIXELS];

uint8_t header[16];
uint32_t frame_counter = 0;

static void put_u16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
}

static void put_f32(uint8_t *p, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    put_u32(p, bits);
}

// 16 byte little-endian header: magic, version, flags, counter, min, max
static void pack_header(float minimum, float maximum) {
    put_u16(header + 0, 0xAA55);
    header[2] = 1;                  // protocol version
    header[3] = 0x02;               // flag bit 1: payload is 32 bit floats
    put_u32(header + 4, frame_counter++);
    put_f32(header + 8, minimum);
    put_f32(header + 12, maximum);
}

void setup() {
    // initialize serial communication
    Serial.begin(921600);

    // initialize the sensor
    myWire.begin();
    if (! mlx.begin(MLX90640_I2CADDR_DEFAULT, &myWire)) {
        // Serial.println("MLX90640 not found!");
        while (1);
    }
    
    // Set the necessary parameters
    mlx.setMode(MLX90640_CHESS);           // MLX90640_CHESS or MLX90640_INTERLEAVED
    mlx.setResolution(MLX90640_ADC_19BIT); // 16 - 19 bit
    mlx.setRefreshRate(MLX90640_4_HZ);     // 0.5 - 64 Hz
    Wire.setClock(1000000);                // 400000 - 1000000 Hz
}

void loop() {
    // Read the frame from the sensor
    if (mlx.getFrame(frame) != 0) {
        // Serial.println("Failed to get frame");
        return;
    }

    // find the min and max temperature values of the frame
    float minimum = frame[0];
    float maximum = frame[0];
    for (int i = 1; i < NUM_PIXELS; i++) {
        if (frame[i] > maximum)
            maximum = frame[i];
        if (frame[i] < minimum)
            minimum = frame[i];
    }

    // Send the header and the frame
    pack_header(minimum, maximum);
    Serial.write(header, sizeof(header));
    Serial.write((byte*)frame, sizeof(frame));
}
