/* Sensors */

#include <RTClib.h>
#include "PMS.h"
#include "MHZ19.h"

// DS1302 rtc(ce_pin, sck_pin, io_pin);
DS1302 rtc(21, 18, 19);  // RST, CLK, DAT

HardwareSerial pmsSerial(2);   // UART-2 for PMS5003
PMS pms(pmsSerial);
PMS::DATA pmsData;

MHZ19 myMHZ19;                // Constructor for library
HardwareSerial co2Serial(1);   // UART-1 for MH-Z19C

// buffer for DateTime.tostr
char buf[20];

/* BLE */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>

// Define the service and characteristic UUIDs
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

BLEServer *pServer;
BLECharacteristic *pCharacteristic;
bool deviceConnected = false;
uint32_t value = 0;

// Server callbacks to track connection status
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      Serial.println("Device connected");
    }

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println("Device disconnected");
      
      // Restart advertising to allow reconnection
      pServer->getAdvertising()->start();
      Serial.println("Advertising restarted");
    }
};

// Timing 
unsigned long prevMillis = 0;
const long interval = 60000;  // 60 seconds = 1 minute

// Binary data structure
// Using a struct to send the data through Bluetooth to a client with a Python handler
// Using #pragman push and pop to avoid memory padding
#pragma pack(push, 1)
struct SensorData {
  uint32_t timestamp;   // 4 bytes - Unix timestamp
  int16_t co2;          // 2 bytes - CO2 in ppm
  int16_t temp;         // 2 bytes - Temperature in Celsius degrees
  uint16_t pm1_0;       // 2 bytes - PM1.0
  uint16_t pm2_5;       // 2 bytes - PM2.5
  uint16_t pm10_0;      // 2 bytes - PM10.0
};
#pragma pack(pop)

/* Initialize data at 0 */
SensorData data = {0};

void setup() {
  Serial.begin(115200);
  
  /* DS1302 RTC Initialization*/
  rtc.begin();
  if (!rtc.isrunning()) {
    Serial.println("RTC is NOT running!");
    /* Next line sets the RTC to the date & time the sketch was compiled, it can be uncommented if date & time in the RTC is reset to set it correctly using the system clock */
    //rtc.adjust(DateTime(__DATE__, __TIME__));
  };

  /* Particle Sensor PMS5003 Initialization*/
  pmsSerial.begin(9600, SERIAL_8N1, 33, 32);  // RX=33, TX=32
  pms.activeMode();

  /* CO2 Sensor MH-Z19C Initialization*/
  co2Serial.begin(9600, SERIAL_8N1, 27, 26);  // RX=27, TX=26
  myMHZ19.begin(co2Serial); 
  myMHZ19.autoCalibration(); 
  
  /*BLE Initialization*/
  BLEDevice::init("ESP32-Sensor-Station");

  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  
  BLEService *pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ |
                      BLECharacteristic::PROPERTY_NOTIFY
                    );

  pCharacteristic->setValue("Sensor Station Ready");
  pService->start();
  
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);
  BLEDevice::startAdvertising();
  
  Serial.println("BLE Sensor Station Ready, Awaiting connections...");
  Serial.println("Device name: ESP32-Sensor-Station");
  Serial.println("Current data structure size: " + String(sizeof(SensorData)) + " bytes");
  Serial.println("Update interval: " + String(interval / 1000) + " seconds");
}

void readSensorsData() {
  DateTime now = rtc.now();
  
  // Pack timestamp (Unix timestamp)
  data.timestamp = now.unixtime();
  
  // Read and pack MH-Z19 data
  data.co2 = myMHZ19.getCO2();
  data.temp = myMHZ19.getTemperature(); 
  
  if(pms.read(pmsData)) {
    data.pm1_0 = pmsData.PM_AE_UG_1_0;
    data.pm2_5 = pmsData.PM_AE_UG_2_5;
    data.pm10_0 = pmsData.PM_AE_UG_10_0;
  };
}

void loop() {
  DateTime now = rtc.now();

  readSensorsData();

  unsigned long currentMillis = millis();
  if (deviceConnected && currentMillis - prevMillis >= interval) {
    prevMillis = currentMillis;
    
    // Convert struct to byte array for BLE transmission
    uint8_t dataBuffer[sizeof(SensorData)];
    memcpy(dataBuffer, &data, sizeof(SensorData));
    
    // Send via BLE
    pCharacteristic->setValue(dataBuffer, sizeof(SensorData));
    pCharacteristic->notify();

  Serial.println(now.tostr(buf));

  int CO2 = myMHZ19.getCO2();

  Serial.print("CO2 (ppm): ");                      
  Serial.println(data.co2);

  int8_t Temp = myMHZ19.getTemperature();                     // Request Temperature (as Celsius)
  Serial.print("Temperature (C): ");                  
  Serial.println(data.temp);    

  Serial.print("PM 1.0 (ug/m3): ");
  Serial.println(data.pm1_0);

  Serial.print("PM 2.5 (ug/m3): ");
  Serial.println(data.pm2_5);

  Serial.print("PM 10.0 (ug/m3): ");
  Serial.println(data.pm10_0);

  Serial.println();
  }

  delay(1000);
}
