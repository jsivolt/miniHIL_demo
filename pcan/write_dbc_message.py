"""Send a BMS_ControlCommand message on PCAN_USBBUS1 using signal values from BMS_demo.dbc."""
import pathlib

import can
import cantools

DBC_PATH = pathlib.Path(__file__).parent / "BMS_demo.dbc"


def main():
    db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
    message_def = db.get_message_by_name("BMS_ControlCommand")

    data = message_def.encode({"ControlCommand": 1})  # 1 = Enable
    message = can.Message(
        arbitration_id=message_def.frame_id,
        data=data,
        is_extended_id=message_def.is_extended_frame,
    )

    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    try:
        bus.send(message)
        print(f"Sent: {message}")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
