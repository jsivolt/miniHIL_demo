"""List NI-DAQmx devices detected on this system."""
import nidaqmx.system


def main():
    system = nidaqmx.system.System.local()
    devices = list(system.devices)

    if not devices:
        print("No NI-DAQmx devices found.")
        return

    for device in devices:
        print(f"Name: {device.name}")
        print(f"Product Type: {device.product_type}")
        print(f"Serial Number: {device.serial_num}")
        print("-" * 40)


if __name__ == "__main__":
    main()
