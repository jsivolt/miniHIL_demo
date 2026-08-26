"""Listen for and print incoming CAN messages on PCAN_USBBUS1."""
import can


def main():
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)

    try:
        print("Listening for CAN messages (Ctrl+C to stop)...")
        for message in bus:
            print(message)
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
