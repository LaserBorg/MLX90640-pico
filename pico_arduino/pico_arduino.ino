// https://learn.adafruit.com/adafruit-mlx90640-ir-thermal-camera/arduino-thermal-camera
// https://github.com/adafruit/Adafruit_MLX90640/blob/master/examples/MLX90640_arcadaCam/MLX90640_arcadaCam.ino

#include <Wire.h>
#include <Adafruit_MLX90640.h>

#define WIDTH 32
#define HEIGHT 24
float frame[WIDTH * HEIGHT];

#define SDA_PIN 16
#define SCL_PIN 17
TwoWire myWire = TwoWire(SDA_PIN, SCL_PIN);

Adafruit_MLX90640 mlx;

uint16_t frame_marker = 0xAAAA;

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
    
    // Send a frame marker and frame
    Serial.write((byte*)&frame_marker, sizeof(uint16_t));
    for (int i = 0; i < WIDTH * HEIGHT; i++) {
        Serial.write((byte*)&frame[i], sizeof(float));
    }
}
