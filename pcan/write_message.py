"""Send CAN messages on PCAN_USBBUS1."""
import time

import can

MESSAGES = [
    (0x405, [0x0B, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),  # Counter = 11
    (0x401, [0xA1, 0x0F, 0xA2, 0x0F, 0xA3, 0x0F, 0xA4, 0x0F]),
    (0x402, [0xA5, 0x0F, 0xA6, 0x0F, 0xA7, 0x0F, 0xA8, 0x0F]),
    # 故意不发 0x403 / 0x404
    (0x405, [0x0C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),  # Counter = 12
]


def main():
    bus = can.Bus(interface="pcan", device_id=1, bitrate=1000000)

    try:
        print("Sending messages...")
        for arbitration_id, data in MESSAGES:
            message = can.Message(
                arbitration_id=arbitration_id,
                data=data,
                is_extended_id=False,
            )
            bus.send(message)
            print(f"Sent: {message}")
            time.sleep(0.2)
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
