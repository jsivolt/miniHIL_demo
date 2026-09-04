"""One-shot and repeating CAN message sending for the web UI's "CAN Sender" tab.

One-shot sends open a bus, send a single message, and shut the bus down again -
mirroring the standalone pcan/write_message.py and pcan/write_dbc_message.py
scripts, just parameterized from the web UI instead of hardcoded. Repeating
sends are handled by a single background thread (RepeatSender) that keeps the
bus open and re-sends the same message on a fixed period until stopped.
"""
import pathlib
import threading
import time

import can
import cantools

DBC_PATH = pathlib.Path(__file__).resolve().parent.parent / "pcan" / "BMS_demo.dbc"

_db = None


def _database():
    global _db
    if _db is None:
        _db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
    return _db


def list_dbc_messages():
    """Describe every message in BMS_demo.dbc for building a send form in the UI."""
    messages = []
    seen_names = set()
    for message in sorted(_database().messages, key=lambda m: m.frame_id):
        if message.name in seen_names:
            continue  # some cell-voltage messages share a masked frame id and are loaded twice
        seen_names.add(message.name)
        signals = []
        for signal in message.signals:
            choices = None
            if signal.choices:
                choices = [{"value": int(value), "label": str(label)} for value, label in signal.choices.items()]
            signals.append({
                "name": signal.name,
                "unit": signal.unit or "",
                "minimum": signal.minimum,
                "maximum": signal.maximum,
                "default": signal.initial or 0,
                "choices": choices,
            })
        messages.append({
            "name": message.name,
            "frame_id": message.frame_id,
            "sender": (message.senders or ["?"])[0],
            "is_extended_id": message.is_extended_frame,
            "signals": signals,
        })
    return messages


def encode_dbc_message(message_name, signal_values):
    """Encode a named DBC message with the given signal values, returning (id, data, is_extended)."""
    message_def = _database().get_message_by_name(message_name)
    data = message_def.encode(signal_values)
    return message_def.frame_id, data, message_def.is_extended_frame


def decode_message(arbitration_id, data):
    """Best-effort decode of a raw frame against BMS_demo.dbc; returns (message_name, signals) or (None, None)."""
    try:
        message_def = _database().get_message_by_frame_id(arbitration_id)
        signals = _database().decode_message(arbitration_id, data, decode_choices=False)
    except Exception:
        # unknown frame id, wrong data length (cantools DecodeError), or any other decode
        # failure - fall back to showing the frame as raw hex instead of crashing the caller
        return None, None
    return message_def.name, signals


def list_pcan_devices():
    """Detect available PCAN channels along with each one's configured device ID."""
    devices = []
    for config in can.detect_available_configs(interfaces="pcan"):
        channel = config["channel"]
        device_id = None
        try:
            bus = can.Bus(interface="pcan", channel=channel)
            try:
                device_id = bus.get_device_number()
            finally:
                bus.shutdown()
        except Exception:
            pass  # channel is busy or unreadable; still list it, just without a device ID
        devices.append({"channel": channel, "device_id": device_id})
    return devices


def send_message(interface, channel, bitrate, arbitration_id, data, is_extended_id=False):
    """Open a bus, send one message, and shut it down. Raises on any failure."""
    message = can.Message(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=is_extended_id,
    )
    bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
    try:
        bus.send(message)
    finally:
        bus.shutdown()
    return message


class RepeatSender:
    """Owns a single background thread that re-sends one CAN message on a fixed period until stopped."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = None
        self._thread = None
        self._count = 0
        self._error = None
        self._mode = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_status(self):
        return {
            "running": self.is_running(),
            "mode": self._mode if self.is_running() else None,
            "count": self._count,
            "error": self._error,
        }

    def start(self, interface, channel, bitrate, arbitration_id, data, is_extended_id, period, mode, counter_byte_index=None):
        with self._lock:
            if self.is_running():
                raise RuntimeError("a repeating send is already running - stop it first")
            self._count = 0
            self._error = None
            self._mode = mode
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._thread = threading.Thread(
                target=self._run,
                args=(interface, channel, bitrate, arbitration_id, data, is_extended_id, period, counter_byte_index, stop_event),
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            if self._stop_event:
                self._stop_event.set()

    def _run(self, interface, channel, bitrate, arbitration_id, data, is_extended_id, period, counter_byte_index, stop_event):
        payload = bytearray(data)
        try:
            bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
        except Exception as error:
            self._error = str(error)
            return
        try:
            next_deadline = time.perf_counter()
            while not stop_event.is_set():
                message = can.Message(arbitration_id=arbitration_id, data=bytes(payload), is_extended_id=is_extended_id)
                bus.send(message)
                self._count += 1
                if counter_byte_index is not None:
                    payload[counter_byte_index] = (payload[counter_byte_index] + 1) % 0x10  # wraps like the counter in write_message.py
                next_deadline += period
                remaining = next_deadline - time.perf_counter()
                if remaining > 0:
                    stop_event.wait(remaining)
                else:
                    next_deadline = time.perf_counter()  # resync rather than trying to catch up
        except Exception as error:
            self._error = str(error)
        finally:
            bus.shutdown()


repeat_sender = RepeatSender()
