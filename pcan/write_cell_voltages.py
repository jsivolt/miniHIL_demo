"""Send vAFE_CellVoltage_* messages (16 cell voltages) on PCAN_USBBUS1 using BMS_demo.dbc."""
import pathlib
import time

import can
import cantools

DBC_PATH = pathlib.Path(__file__).parent / "BMS_demo.dbc"

CELL_VOLTAGE_MESSAGES = [
    ("vAFE_CellVoltage_1_4", ["Cell1Voltage", "Cell2Voltage", "Cell3Voltage", "Cell4Voltage"]),
    ("vAFE_CellVoltage_5_8", ["Cell5Voltage", "Cell6Voltage", "Cell7Voltage", "Cell8Voltage"]),
    ("vAFE_CellVoltage_9_12", ["Cell9Voltage", "Cell10Voltage", "Cell11Voltage", "Cell12Voltage"]),
    ("vAFE_CellVoltage_13_16", ["Cell13Voltage", "Cell14Voltage", "Cell15Voltage", "Cell16Voltage"]),
]


def send_cell_voltages(bus, db, cell_voltages, verbose=True):
    """Encode and send the 4 vAFE_CellVoltage_* frames for 16 cell voltages (V)."""
    if len(cell_voltages) != 16:
        raise ValueError("cell_voltages must contain exactly 16 values")

    for i, (message_name, signal_names) in enumerate(CELL_VOLTAGE_MESSAGES):
        message_def = db.get_message_by_name(message_name)
        values = dict(zip(signal_names, cell_voltages[i * 4:(i + 1) * 4]))
        data = message_def.encode(values)
        message = can.Message(
            arbitration_id=message_def.frame_id,
            data=data,
            is_extended_id=message_def.is_extended_frame,
        )
        bus.send(message)
        if verbose:
            print(f"Sent: {message}")


def main():
    db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
    cell_voltages = [3.7] * 16

    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    try:
        print("Sending cell voltages every 100ms (Ctrl+C to stop)...")
        while True:
            send_cell_voltages(bus, db, cell_voltages)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
