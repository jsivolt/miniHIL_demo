"""Send the vPack_Current (0x410) and vPack_Voltage (0x411) messages on one PCAN device, and the
vAFE_CellVoltage_* (0x401-0x404) messages on a second PCAN device, using the S32K344 project's BMS_demo.dbc."""
import pathlib
import random
import time

import can
import cantools
import nidaqmx
from nidaqmx.constants import LineGrouping

DBC_PATH = pathlib.Path(r"C:\S32K344\workspace\BMS_demo\DBC\BMS_demo.dbc")

PACK_CURRENT = 7.0  # amps (negative = discharge)
CELL_VOLTAGES = [3.7] * 16  # volts

PCAN_DEVICE_ID = 4294967295
CELL_PCAN_DEVICE_ID = 1

CELL_VOLTAGE_COUNTER_ID = 0x405  # not defined in the dbc, sent as a raw frame

CELL_VOLTAGE_MESSAGES = [
    ("vAFE_CellVoltage_01_04", ["Cell01Voltage", "Cell02Voltage", "Cell03Voltage", "Cell04Voltage"]),
    ("vAFE_CellVoltage_05_08", ["Cell05Voltage", "Cell06Voltage", "Cell07Voltage", "Cell08Voltage"]),
    ("vAFE_CellVoltage_09_12", ["Cell09Voltage", "Cell10Voltage", "Cell11Voltage", "Cell12Voltage"]),
    ("vAFE_CellVoltage_13_16", ["Cell13Voltage", "Cell14Voltage", "Cell15Voltage", "Cell16Voltage"]),
]


def send_message(bus, message_def, values):
    data = message_def.encode(values)
    message = can.Message(
        arbitration_id=message_def.frame_id,
        data=data,
        is_extended_id=message_def.is_extended_frame,
    )
    bus.send(message)
    print(f"Sent: {message}")


def send_cell_voltage_counter(cell_bus, counter):
    message = can.Message(
        arbitration_id=CELL_VOLTAGE_COUNTER_ID,
        data=[counter, 0, 0, 0, 0, 0, 0, 0],
        is_extended_id=False,
    )
    cell_bus.send(message)
    print(f"Sent: {message}")


def send_cell_voltages(cell_bus, db, cell_voltages, counter):
    send_cell_voltage_counter(cell_bus, counter)
    jittered_voltages = [v * random.uniform(0.98, 1.02) for v in cell_voltages]
    for i, (message_name, signal_names) in enumerate(CELL_VOLTAGE_MESSAGES):
        message_def = db.get_message_by_name(message_name)
        values = dict(zip(signal_names, jittered_voltages[i * 4:(i + 1) * 4]))
        send_message(cell_bus, message_def, values)
    return jittered_voltages


def main():
    db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
    current_message_def = db.get_message_by_name("vPack_Current")
    voltage_message_def = db.get_message_by_name("vPack_Voltage")

    pack_bus = can.Bus(interface="pcan", device_id=PCAN_DEVICE_ID, bitrate=1000000)
    cell_bus = can.Bus(interface="pcan", device_id=CELL_PCAN_DEVICE_ID, bitrate=1000000)
    di_task = nidaqmx.Task()
    di_task.di_channels.add_di_chan("Dev1/port1/line0:3", line_grouping=LineGrouping.CHAN_PER_LINE)
    try:
        print("Sending pack current/voltage/cell voltages every 100ms (Ctrl+C to stop)...")
        alive_counter = 0
        cell_voltage_counter = 0
        while True:
            line0, _, line2, _ = di_task.read()
            pack_current = 0.0 if not line0 and not line2 else PACK_CURRENT

            jittered_voltages = send_cell_voltages(cell_bus, db, CELL_VOLTAGES, cell_voltage_counter)
            pack_voltage = sum(jittered_voltages)
            send_message(
                pack_bus,
                current_message_def,
                {
                    "Pack1Current_mA": pack_current * 1000,
                    "Shunt1Voltage_uV": 0,
                    "AliveCounter": alive_counter,
                    "Status": 0,
                },
            )
            send_message(pack_bus, voltage_message_def, {"Pack1VoltageSim": pack_voltage})
            alive_counter = (alive_counter + 1) % 0x10
            cell_voltage_counter = (cell_voltage_counter + 1) % 256
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        di_task.close()
        pack_bus.shutdown()
        cell_bus.shutdown()


if __name__ == "__main__":
    main()
