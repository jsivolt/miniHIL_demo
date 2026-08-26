"""List PCAN channels detected on this system."""
import can


def main():
    configs = can.detect_available_configs(interfaces="pcan")

    if not configs:
        print("No PCAN devices found.")
        return

    for config in configs:
        print(f"Channel: {config['channel']}")
        print(f"Interface: {config['interface']}")
        print("-" * 40)


if __name__ == "__main__":
    main()
