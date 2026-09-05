"""Drives the Battery Simulator tab's current live from the Pack1Current signal on PCAN
device 0, for the "Live current override" tab's "Read from PCAN" option.

Shares the CAN Receiver tab's already-open bus (via its listener hook) instead of opening a
second handle on the same PCAN device when that tab is already running; otherwise opens its
own device-0 bus, matching can_receiver's fixed defaults.
"""
import threading

import can

import can_receiver
import can_sender
import vcellpack_sender
from battery_sim_core import simulator

DEFAULT_DEVICE_ID = can_receiver.DEFAULT_DEVICE_ID
DEFAULT_BITRATE = can_receiver.DEFAULT_BITRATE


def _decode(arbitration_id, data):
    """Try the external/newer DBC first (real hardware traffic), then the repo's older DBC."""
    message_name, signals = vcellpack_sender.decode_message(arbitration_id, data)
    if message_name is not None:
        return message_name, signals
    return can_sender.decode_message(arbitration_id, data)


def _extract_current_a(signals):
    """Pack1Current is sent in mA by the external DBC; fall back to an as-is amps signal name."""
    if not signals:
        return None
    for name in ("Pack1Current_mA", "PackCurrent_mA"):
        if name in signals:
            return float(signals[name]) / 1000
    for name in ("Pack1Current", "PackCurrent"):
        if name in signals:
            return float(signals[name])
    return None


class PcanCurrentSource:
    """Feeds simulator.set_current() from Pack1Current, in "shared" mode (piggybacking on the
    CAN Receiver tab's bus) or "owned" mode (its own bus), decided once at start()."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mode = None  # None, "shared" or "owned"
        self._stop_event = None
        self._thread = None
        self._bus = None
        self._error = None
        self._count = 0
        self._last_current_a = None

    def is_running(self):
        return self._mode is not None

    def get_status(self):
        with self._lock:
            return {
                "running": self.is_running(),
                "mode": self._mode,
                "count": self._count,
                "error": self._error,
                "last_current_a": self._last_current_a,
                "receiver_running": can_receiver.receiver.is_running(),
            }

    def start(self):
        with self._lock:
            if self.is_running():
                raise RuntimeError("current source is already running - stop it first")
            self._count = 0
            self._error = None
            self._last_current_a = None
            if can_receiver.receiver.is_running():
                self._mode = "shared"
                can_receiver.receiver.add_listener(self._on_entry)
                return
            self._mode = "owned"
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._thread = threading.Thread(target=self._run, args=(stop_event,), daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            mode = self._mode
            stop_event = self._stop_event
            self._mode = None
        if mode == "shared":
            can_receiver.receiver.remove_listener(self._on_entry)
        elif mode == "owned" and stop_event:
            stop_event.set()

    def _on_entry(self, entry):
        current_a = _extract_current_a(entry.get("signals"))
        if current_a is None:
            return
        simulator.set_current(current_a)
        with self._lock:
            self._last_current_a = current_a
            self._count += 1

    def _run(self, stop_event):
        try:
            bus = can.Bus(interface="pcan", device_id=DEFAULT_DEVICE_ID, bitrate=DEFAULT_BITRATE)
        except Exception as error:
            with self._lock:
                self._error = str(error)
                self._mode = None
            return
        with self._lock:
            self._bus = bus
        try:
            while not stop_event.is_set():
                message = bus.recv(timeout=0.2)
                if message is None:
                    continue
                try:
                    _, signals = _decode(message.arbitration_id, message.data)
                except Exception:
                    continue  # malformed/unexpected frame - keep listening
                self._on_entry({"signals": signals})
        except Exception as error:
            with self._lock:
                self._error = str(error)
        finally:
            with self._lock:
                self._bus = None
            bus.shutdown()


source = PcanCurrentSource()
