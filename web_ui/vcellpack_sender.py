"""Continuous vPack_Current/vPack_Voltage/vAFE_CellVoltage_* sender for the web UI's
"Cell/Pack Sim" tab.

Mirrors pcan/write_vCell_vPack_dbc.py (jittered cell voltages -> derived pack voltage,
fixed pack current, incrementing AliveCounter) but runs on a single background thread
that can be started/stopped from the UI instead of a CLI loop.

Uses the same external DBC as write_vCell_vPack_dbc.py (not the repo's pcan/BMS_demo.dbc,
which is an older revision that lacks the vPack_Current/vPack_Voltage messages and uses
unpadded Cell1Voltage-style signal names instead of Cell01Voltage).
"""
import collections
import pathlib
import random
import threading
import time

import can
import cantools
import nidaqmx
from nidaqmx.constants import LineGrouping

DBC_PATH = pathlib.Path(r"C:\S32K344\workspace\BMS_demo\DBC\BMS_demo.dbc")

# Matches pcan/write_vCell_vPack_dbc.py: forces pack current to 0 when both lines read LOW.
DI_CHANNEL = "Dev1/port1/line0:3"

# Same fixed PCAN device IDs as pcan/write_vCell_vPack_dbc.py.
PACK_DEVICE_ID = 4294967295
CELL_DEVICE_ID = 1
PACK_MESSAGES = ["vPack_Current", "vPack_Voltage"]

CELL_VOLTAGE_COUNTER_ID = 0x405  # not defined in the dbc, sent as a raw frame

CELL_VOLTAGE_MESSAGES = [
    ("vAFE_CellVoltage_01_04", ["Cell01Voltage", "Cell02Voltage", "Cell03Voltage", "Cell04Voltage"]),
    ("vAFE_CellVoltage_05_08", ["Cell05Voltage", "Cell06Voltage", "Cell07Voltage", "Cell08Voltage"]),
    ("vAFE_CellVoltage_09_12", ["Cell09Voltage", "Cell10Voltage", "Cell11Voltage", "Cell12Voltage"]),
    ("vAFE_CellVoltage_13_16", ["Cell13Voltage", "Cell14Voltage", "Cell15Voltage", "Cell16Voltage"]),
]
CELL_COUNT = 16
MAX_PACK_LOG = 100

_db = None


def _database():
    global _db
    if _db is None:
        _db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
    return _db


def bus_message_summary():
    """Which DBC messages go out on which fixed PCAN device, for display in the UI."""
    return [
        {"role": "Pack bus", "device_id": PACK_DEVICE_ID, "messages": PACK_MESSAGES},
        {
            "role": "Cell bus",
            "device_id": CELL_DEVICE_ID,
            "messages": [hex(CELL_VOLTAGE_COUNTER_ID)] + [name for name, _ in CELL_VOLTAGE_MESSAGES],
        },
    ]


def _open_bus(interface, bitrate, device_id, virtual_channel):
    """pcan selects the bus by fixed device ID; other interfaces (e.g. virtual) need a channel name."""
    if interface == "pcan":
        return can.Bus(interface=interface, device_id=device_id, bitrate=bitrate)
    return can.Bus(interface=interface, channel=virtual_channel, bitrate=bitrate)


def decode_message(arbitration_id, data):
    """Best-effort decode of a raw frame against this module's external/newer DBC; returns
    (message_name, signals) or (None, None)."""
    try:
        message_def = _database().get_message_by_frame_id(arbitration_id)
        signals = _database().decode_message(arbitration_id, data, decode_choices=False)
    except Exception:
        return None, None
    return message_def.name, signals


class VCellVPackSender:
    """Owns a background thread that repeatedly sends jittered cell voltages plus the
    derived pack current/voltage messages across two (possibly identical) buses until
    stopped."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = None
        self._thread = None
        self._count = 0
        self._error = None
        # Live-adjustable while running; guarded by _lock so _run always sees a consistent value.
        self._pack_current_a = 0.0
        self._cell_voltage_v = 0.0
        self._period = 0.2
        # Rolling log of frames sent on the Pack bus, for the UI's "Pack bus messages" view.
        self._pack_log = collections.deque(maxlen=MAX_PACK_LOG)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_status(self):
        with self._lock:
            return {
                "running": self.is_running(),
                "count": self._count,
                "error": self._error,
                "pack_current_a": self._pack_current_a,
                "cell_voltage_v": self._cell_voltage_v,
                "period": self._period,
                "pack_messages": list(self._pack_log),
            }

    def _log_pack_message(self, message):
        """Record a frame just sent on the Pack bus, decoded the same way an incoming frame
        would be on the CAN Receiver tab, for display in the UI."""
        message_name, signals = decode_message(message.arbitration_id, message.data)
        entry = {
            "timestamp": time.time(),
            "arbitration_id": message.arbitration_id,
            "is_extended_id": message.is_extended_id,
            "dlc": message.dlc,
            "data": message.data.hex(" "),
            "message": message_name,
            "signals": signals,
        }
        with self._lock:
            self._pack_log.appendleft(entry)

    def start(self, interface, bitrate, pack_current_a, cell_voltage_v, period):
        with self._lock:
            if self.is_running():
                raise RuntimeError("cell/pack sim is already running - stop it first")
            if period <= 0:
                raise ValueError("period must be greater than 0")
            self._count = 0
            self._error = None
            self._pack_current_a = pack_current_a
            self._cell_voltage_v = cell_voltage_v
            self._period = period
            self._pack_log.clear()
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._thread = threading.Thread(
                target=self._run,
                args=(interface, bitrate, stop_event),
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            if self._stop_event:
                self._stop_event.set()

    def update(self, pack_current_a=None, cell_voltage_v=None, period=None):
        """Change current/voltage/period on the fly without stopping the sender thread."""
        with self._lock:
            if not self.is_running():
                raise RuntimeError("cell/pack sim is not running")
            if period is not None:
                if period <= 0:
                    raise ValueError("period must be greater than 0")
                self._period = period
            if pack_current_a is not None:
                self._pack_current_a = pack_current_a
            if cell_voltage_v is not None:
                self._cell_voltage_v = cell_voltage_v

    def _run(self, interface, bitrate, stop_event):
        db = _database()
        current_message_def = db.get_message_by_name("vPack_Current")
        voltage_message_def = db.get_message_by_name("vPack_Voltage")
        cell_message_defs = [(db.get_message_by_name(name), signals) for name, signals in CELL_VOLTAGE_MESSAGES]

        try:
            pack_bus = _open_bus(interface, bitrate, PACK_DEVICE_ID, "vcp-pack-sim")
        except Exception as error:
            self._error = str(error)
            return
        try:
            cell_bus = _open_bus(interface, bitrate, CELL_DEVICE_ID, "vcp-cell-sim")
        except Exception as error:
            self._error = str(error)
            pack_bus.shutdown()
            return

        # NI hardware is optional: if it can't be opened (or later disconnects), keep sending
        # CAN traffic with pack current forced to 0 instead of stopping the whole sim.
        try:
            di_task = nidaqmx.Task()
            di_task.di_channels.add_di_chan(DI_CHANNEL, line_grouping=LineGrouping.CHAN_PER_LINE)
        except Exception as error:
            self._error = str(error)
            di_task = None

        try:
            counter = 0
            cell_voltage_counter = 0
            while not stop_event.is_set():
                with self._lock:
                    pack_current_a = self._pack_current_a
                    cell_voltage_v = self._cell_voltage_v
                    period = self._period

                if di_task is None:
                    current_a = 0.0
                else:
                    try:
                        line0, _, line2, _ = di_task.read()
                        current_a = 0.0 if not line0 and not line2 else pack_current_a
                    except Exception as error:
                        self._error = str(error)  # NI disconnected mid-run: fail safe to zero current
                        di_task.close()
                        di_task = None
                        current_a = 0.0

                cell_bus.send(can.Message(
                    arbitration_id=CELL_VOLTAGE_COUNTER_ID,
                    data=[cell_voltage_counter, 0, 0, 0, 0, 0, 0, 0],
                    is_extended_id=False,
                ))
                self._count += 1

                jittered_voltages = [cell_voltage_v * random.uniform(0.98, 1.02) for _ in range(CELL_COUNT)]
                for i, (message_def, signal_names) in enumerate(cell_message_defs):
                    values = dict(zip(signal_names, jittered_voltages[i * 4:(i + 1) * 4]))
                    cell_bus.send(can.Message(
                        arbitration_id=message_def.frame_id,
                        data=message_def.encode(values),
                        is_extended_id=message_def.is_extended_frame,
                    ))
                    self._count += 1

                pack_voltage = sum(jittered_voltages)
                current_message = can.Message(
                    arbitration_id=current_message_def.frame_id,
                    data=current_message_def.encode({
                        "Pack1Current_mA": current_a * 1000,
                        "Shunt1Voltage_uV": 0,
                        "AliveCounter": counter,
                        "Status": 0,
                    }),
                    is_extended_id=current_message_def.is_extended_frame,
                )
                voltage_message = can.Message(
                    arbitration_id=voltage_message_def.frame_id,
                    data=voltage_message_def.encode({
                        "Pack1VoltageSim": pack_voltage,
                        "BusVoltage_mV": pack_voltage,
                    }),
                    is_extended_id=voltage_message_def.is_extended_frame,
                )
                pack_bus.send(current_message)
                pack_bus.send(voltage_message)
                self._log_pack_message(current_message)
                self._log_pack_message(voltage_message)
                self._count += 2
                counter = (counter + 1) % 0x10
                cell_voltage_counter = (cell_voltage_counter + 1) % 256
                stop_event.wait(period)
        except Exception as error:
            self._error = str(error)
        finally:
            if di_task is not None:
                di_task.close()
            pack_bus.shutdown()
            cell_bus.shutdown()


sender = VCellVPackSender()
