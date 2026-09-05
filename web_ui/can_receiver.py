"""Background CAN receiver for the web UI's "CAN Receiver" tab.

Opens a python-can bus - by fixed PCAN device ID (default 0), same pattern as
vcellpack_sender._open_bus, or by an OS channel name for other interfaces -
reads every incoming frame on a background thread, decodes it and keeps a
rolling buffer of the most recent frames for the UI to poll.

Decoding tries vcellpack_sender's external/newer DBC (C:\\S32K344\\workspace\\BMS_demo\\DBC\\
BMS_demo.dbc - has vPack_Current/vPack_Voltage and Cell01Voltage-style names, matches the
Cell/Pack Sim tab and real hardware) first, then falls back to can_sender's repo-checked-in
pcan/BMS_demo.dbc (older revision, matches the CAN Sender tab) for anything the external DBC
doesn't know about.
"""
import collections
import threading
import time

import can

import can_sender
import vcellpack_sender

DEFAULT_DEVICE_ID = 0
DEFAULT_BITRATE = 500000  # matches the hardware's actual bus speed (pcan/read_message.py etc.)
MAX_LOG = 200


def _open_bus(interface, channel, bitrate, device_id):
    """pcan can select the bus by fixed device ID instead of an OS channel name."""
    if interface == "pcan" and device_id is not None:
        return can.Bus(interface=interface, device_id=device_id, bitrate=bitrate)
    return can.Bus(interface=interface, channel=channel, bitrate=bitrate)


def _decode(arbitration_id, data):
    """Try the external/newer DBC first (real hardware traffic), then the repo's older DBC."""
    message_name, signals = vcellpack_sender.decode_message(arbitration_id, data)
    if message_name is not None:
        return message_name, signals
    return can_sender.decode_message(arbitration_id, data)


class CanReceiver:
    """Owns a single background thread that reads and decodes frames off one bus until stopped."""

    def __init__(self):
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()  # serializes bus.recv()/bus.send() across threads - PCAN-Basic isn't safe for concurrent calls on one handle
        self._stop_event = None
        self._thread = None
        self._error = None
        self._count = 0
        self._log = collections.deque(maxlen=MAX_LOG)
        self._bus = None
        self._listeners = []

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def add_listener(self, callback):
        """Register a callback(entry) to receive every decoded frame, e.g. so another feature can
        reuse this bus instead of opening a second handle on the same PCAN device."""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self, entry):
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(entry)
            except Exception:
                pass  # a broken listener must not take down the receiver thread

    def send(self, arbitration_id, data, is_extended_id=False):
        """Send on the receiver's already-open bus instead of opening a second handle on the same channel."""
        with self._lock:
            bus = self._bus if self.is_running() else None
        if bus is None:
            raise RuntimeError("receiver is not running - start listening first")
        message = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=is_extended_id)
        with self._io_lock:
            bus.send(message)
        return message

    def get_status(self):
        with self._lock:
            return {
                "running": self.is_running(),
                "count": self._count,
                "error": self._error,
                "messages": list(self._log),
            }

    def start(self, interface, channel, bitrate, device_id):
        with self._lock:
            if self.is_running():
                raise RuntimeError("a receiver is already running - stop it first")
            self._count = 0
            self._error = None
            self._log.clear()
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._thread = threading.Thread(
                target=self._run,
                args=(interface, channel, bitrate, device_id, stop_event),
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            if self._stop_event:
                self._stop_event.set()

    def _run(self, interface, channel, bitrate, device_id, stop_event):
        try:
            bus = _open_bus(interface, channel, bitrate, device_id)
        except Exception as error:
            self._error = str(error)
            return
        with self._lock:
            self._bus = bus
        try:
            while not stop_event.is_set():
                with self._io_lock:
                    message = bus.recv(timeout=0.2)
                if message is None:
                    continue
                try:
                    message_name, signals = _decode(message.arbitration_id, message.data)
                except Exception:
                    message_name, signals = None, None  # malformed/unexpected frame - keep listening
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
                    self._log.appendleft(entry)
                    self._count += 1
                self._notify(entry)
        except Exception as error:
            self._error = str(error)
        finally:
            with self._lock:
                self._bus = None
            bus.shutdown()


receiver = CanReceiver()
