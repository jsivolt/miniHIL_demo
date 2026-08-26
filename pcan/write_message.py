"""Send a single CAN message on PCAN_USBBUS1."""
import can


def main():
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    message = can.Message(
        arbitration_id=0x123,
        data=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08],
        is_extended_id=False,
    )

    try:
        bus.send(message)
        print(f"Sent: {message}")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
