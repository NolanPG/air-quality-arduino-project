import asyncio
import datetime
import struct
import aiofiles
import os
from collections import OrderedDict
from bleak import BleakScanner, BleakClient

# The same UUIDs defined in the ESP32 code
SERVICE_UUID = os.getenv('SERVICE_UUID')
CHARACTERISTIC_UUID = os.getenv('CHARACTERISTIC_UUID')
TARGET_NAME = os.getenv('TARGET_NAME')
# fallback MAC (overwrite if found by name)
MAC_ADDRESS = os.getenv('MAC_ADDRESS')


# Binary data structure format (matches C++ struct) - 14 bytes total
# I: unsigned int (4 bytes) - timestamp
# h: short (2 bytes) - co2
# h: short (2 bytes) - temperature (0.1°C resolution)
# H: unsigned short (2 bytes) - pm1_0
# H: unsigned short (2 bytes) - pm2_5
# H: unsigned short (2 bytes) - pm10_0
SENSOR_DATA_FORMAT = "IhhHHH"  # Total: 14 bytes
SENSOR_DATA_SIZE = struct.calcsize(SENSOR_DATA_FORMAT)


class BinarySensorProcessor:
    def __init__(self, csv_filename="sensor_data.csv"):
        self.csv_filename = csv_filename
        self.data_count = 0
        self.last_reception_time = None
        self.need_header = not os.path.isfile(self.csv_filename)

    async def setup_csv(self):
        """Create CSV with header if needed (safe to call repeatedly)."""
        if self.need_header:
            try:
                async with aiofiles.open(self.csv_filename, "w") as f:
                    headers = [
                        "local_timestamp",
                        "device_timestamp",
                        "co2_ppm",
                        "temperature_c",
                        "pm1_0_ug_m3",
                        "pm2_5_ug_m3",
                        "pm10_0_ug_m3",
                    ]
                    await f.write(",".join(headers) + "\n")
                print(f"Created new CSV file: {self.csv_filename}")
            except Exception as e:
                print(f"Error creating CSV file: {e}")
            finally:
                self.need_header = False

    async def save_to_csv(self, sensor_dict, local_timestamp):
        """Append one CSV line (async)."""
        try:
            # Convert device timestamp to readable format
            device_time = datetime.datetime.fromtimestamp(sensor_dict["timestamp"])
            device_timestamp_str = device_time.strftime("%Y-%m-%d %H:%M:%S")

            temperature_c = sensor_dict["temperature_c"]

            row = [
                local_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                device_timestamp_str,
                str(sensor_dict["co2_ppm"]),
                f"{temperature_c:.1f}",
                str(sensor_dict["pm1_0_ug_m3"]),
                str(sensor_dict["pm2_5_ug_m3"]),
                str(sensor_dict["pm10_0_ug_m3"]),
            ]
            csv_line = ",".join(row) + "\n"

            async with aiofiles.open(self.csv_filename, "a") as f:
                await f.write(csv_line)
            return True
        except Exception as e:
            print(f"Error saving to CSV: {e}")
            return False

    def unpack_sensor_data(self, binary_data):
        if len(binary_data) != SENSOR_DATA_SIZE:
            raise ValueError(
                f"Expected {SENSOR_DATA_SIZE} bytes, got {len(binary_data)}"
            )
        unpacked = struct.unpack(SENSOR_DATA_FORMAT, binary_data)
        sensor_dict = OrderedDict(
            [
                ("timestamp", unpacked[0]),
                ("co2_ppm", unpacked[1]),
                ("temperature_c", unpacked[2]),
                ("pm1_0_ug_m3", unpacked[3]),
                ("pm2_5_ug_m3", unpacked[4]),
                ("pm10_0_ug_m3", unpacked[5]),
            ]
        )
        return sensor_dict

    async def process_sensor_data(self, binary_data):
        """Async processing: unpack, print, save to CSV (await)."""
        try:
            current_time = datetime.datetime.now()

            if self.last_reception_time:
                time_diff = (current_time - self.last_reception_time).total_seconds()
                time_diff_str = f"{time_diff:.1f}s since last data"
            else:
                time_diff_str = "First data packet"

            self.last_reception_time = current_time
            self.data_count += 1

            sensor_dict = self.unpack_sensor_data(binary_data)

            # Convert timestamp to readable format
            dt = datetime.datetime.fromtimestamp(sensor_dict["timestamp"])
            readable_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            local_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

            print(f"\n{'='*60}")
            print(f"1-MINUTE SENSOR DATA #{self.data_count}")
            print(f"Local Time: {local_time}")
            print(f"Device Time: {readable_time}")
            print(f"{time_diff_str}")
            print(f"{'-'*60}")
            print(f"CO2: {sensor_dict['co2_ppm']} ppm")
            # If your temp needs division by 10, do that above.
            print(f"Temperature: {sensor_dict['temperature_c']:.1f}°C")
            print(f"PM1.0: {sensor_dict['pm1_0_ug_m3']} μg/m³")
            print(f"PM2.5: {sensor_dict['pm2_5_ug_m3']} μg/m³")
            print(f"PM10.0: {sensor_dict['pm10_0_ug_m3']} μg/m³")
            print(f"Data Size: {len(binary_data)} bytes")
            print(f"Next update in ~60 seconds...")
            print(f"{'='*60}")

            saved = await self.save_to_csv(sensor_dict, current_time)
            if not saved:
                print("Warning: CSV save failed for this packet.")
            return sensor_dict
        except Exception as e:
            print(f"Error processing binary data: {e}")
            try:
                print(f"Raw data (hex): {binary_data.hex()}")
            except Exception:
                pass
            return None


async def main():

    print("Searching for BLE Sensor Station...")
    print(f"Expected data size: {SENSOR_DATA_SIZE} bytes")
    print(f"Update interval: 60 seconds")

    # Discover nearby BLE devices
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        print(f"Found device: {d.name}, Address: {d.address}")
        if d.name and TARGET_NAME in d.name:
            MAC_ADDRESS = d.address
            print(f"Found target device: {d.name} at {MAC_ADDRESS}")
            break

    if MAC_ADDRESS is None:
        print("Could not find the target device.")
        return

    # Initialize sensor processor & queue
    timestamp = datetime.datetime.now().strftime("%Y%m%d")
    sensor_processor = BinarySensorProcessor(f"sensor_data_{timestamp}.csv")
    await sensor_processor.setup_csv()

    queue: asyncio.Queue = asyncio.Queue(maxsize=20)

    async def data_consumer():
        """Consumer handles and awaits process + CSV writes."""
        while True:
            binary = await queue.get()
            try:
                await sensor_processor.process_sensor_data(binary)
            except Exception as e:
                print(f"Unhandled error in consumer: {e}")
            finally:
                queue.task_done()

    consumer_task = asyncio.create_task(data_consumer())

    async with BleakClient(MAC_ADDRESS) as client:
        print("Connected...")

        def notification_handler(sender, data):
            # This callback may run on a background thread; do minimal work here.
            try:
                # Try to put into queue without blocking the callback thread.
                queue.put_nowait(data)
                print(f"Notification queued ({len(data)} bytes). Queue size: {queue.qsize()}")
            except asyncio.QueueFull:
                # If queue full, drop packet (or handle differently)
                print("Warning: queue full — dropping notification")

        await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
        print("Subscribed to sensor notifications")
        print("Receiving data every 60 seconds...")
        print("Press Ctrl+C to stop\n")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nDisconnecting...")
            await client.stop_notify(CHARACTERISTIC_UUID)
            # Optionally wait for queue to drain:
            if not queue.empty():
                print("Waiting for queue to drain...")
                await queue.join()
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
            print("Disconnected")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")