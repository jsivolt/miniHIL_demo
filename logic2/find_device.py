"""List Saleae Logic devices detected by a running Logic 2 instance."""
from saleae.automation import Manager


def main():
    with Manager.connect() as manager:
        devices = manager.get_devices(include_simulation_devices=True)

        if not devices:
            print("No Saleae devices found.")
            return

        for device in devices:
            print(f"Device ID: {device.device_id}")
            print(f"Device Type: {device.device_type.name}")
            print(f"Simulation: {device.is_simulation}")
            print("-" * 40)


if __name__ == "__main__":
    main()
