"""Battery simulator engine: PyBaMM Thevenin ECM cells driven onto CAN as vAFE_CellVoltage_* frames.

Shared by the CLI script (battery_simulator.py) and the web UI (web_ui/app.py). The physics solve
runs on its own thread, stepped by real elapsed time, while a separate thread sends the latest
snapshot on the CAN bus every period. This lets a Flask request thread start/stop/monitor the
simulation without blocking, and lets the drive current be changed live via a pybamm.InputParameter.
"""
import threading
import time

import can
import cantools
import numpy as np
import pybamm

from write_cell_voltages import DBC_PATH, send_cell_voltages

CELL_COUNT = 16
SIGNAL_MIN_V = 0.0
SIGNAL_MAX_V = 6.5535  # vAFE cell voltage signals are 16-bit unsigned at 0.001 V/bit

CAPACITY_SPREAD = 0.03
R0_SPREAD = 0.10
SOC_SPREAD = 0.02
MIN_STEP_S = 0.01
CURRENT_INPUT_NAME = "Current function [A]"

DEFAULT_PARAMS = {
    "current_a": 5.0,
    "capacity_ah": 5.0,
    "initial_soc": 0.9,
    "period": 0.1,
    "seed": 0,
    "interface": "virtual",
    "channel": "PCAN_USBBUS1",
    "bitrate": 500000,
}


def scaled_r0(base_r0, scale):
    """Wrap the ECM's R0 lookup, which is a callable of (T_cell, current, soc), with a fixed scale."""
    return lambda T_cell, current, soc: base_r0(T_cell, current, soc) * scale


def create_cells(count, capacity_ah, initial_soc, seed):
    """Build one independent Thevenin ECM per cell, with a seeded spread in capacity, R0 and SoC.

    The drive current is left as a pybamm.InputParameter so it can be changed between steps
    without rebuilding the simulation.
    """
    rng = np.random.default_rng(seed)
    cells = []
    for _ in range(count):
        model = pybamm.equivalent_circuit.Thevenin()
        params = model.default_parameter_values
        params["R0 [Ohm]"] = scaled_r0(params["R0 [Ohm]"], 1.0 + rng.uniform(-R0_SPREAD, R0_SPREAD))
        params["Cell capacity [A.h]"] = capacity_ah * (1.0 + rng.uniform(-CAPACITY_SPREAD, CAPACITY_SPREAD))
        params["Nominal cell capacity [A.h]"] = capacity_ah
        params[CURRENT_INPUT_NAME] = pybamm.InputParameter(CURRENT_INPUT_NAME)
        params["Initial SoC"] = float(np.clip(initial_soc + rng.uniform(-SOC_SPREAD, SOC_SPREAD), 0.0, 1.0))
        simulation = pybamm.Simulation(model, parameter_values=params, solver=pybamm.IDAKLUSolver())
        cells.append({"sim": simulation, "voltage": 0.0, "soc": 0.0, "exhausted": False})
    return cells


def step_cells(cells, dt, current_a):
    """Advance every live cell by dt seconds; cells that hit a cut-off hold their last voltage."""
    inputs = {CURRENT_INPUT_NAME: current_a}
    for cell in cells:
        if cell["exhausted"]:
            continue
        solution = cell["sim"].step(dt, inputs=inputs, save=False)
        cell["voltage"] = float(solution["Voltage [V]"].entries[-1])
        cell["soc"] = float(solution["SoC"].entries[-1])
        cell["exhausted"] = solution.termination != "final time"


def snapshot(cells, sim_time, steps, current_a):
    voltages = [cell["voltage"] for cell in cells]
    socs = [cell["soc"] for cell in cells]
    return {
        "voltages": voltages,
        "socs": socs,
        "exhausted": sum(1 for cell in cells if cell["exhausted"]),
        "sim_time": sim_time,
        "steps": steps,
        "current_a": current_a,
        "pack_voltage": sum(voltages),
        "mean_voltage": sum(voltages) / len(voltages),
        "min_voltage": min(voltages),
        "max_voltage": max(voltages),
        "mean_soc": sum(socs) / len(socs),
    }


def clamp(value):
    return min(max(value, SIGNAL_MIN_V), SIGNAL_MAX_V)


class BatterySimulator:
    """Owns the simulator's background threads; safe to start/stop/monitor from Flask request handlers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = None
        self._state = {"snapshot": None, "error": None}
        self._current_a = DEFAULT_PARAMS["current_a"]
        self._status = "idle"
        self._overruns = 0
        self._params = dict(DEFAULT_PARAMS)

    def get_status(self):
        error = self._state.get("error")
        return {
            "status": self._status,
            "error": str(error) if error else None,
            "overruns": self._overruns,
            "params": self._params,
            "snapshot": self._state.get("snapshot"),
        }

    def start(self, **params):
        with self._lock:
            if self._status in ("starting", "running"):
                raise RuntimeError("simulator is already running")
            self._params = {**DEFAULT_PARAMS, **params}
            self._current_a = self._params["current_a"]
            self._overruns = 0
            self._state = {"snapshot": None, "error": None}
            self._stop_event = threading.Event()
            self._status = "starting"
            stop_event = self._stop_event
        threading.Thread(target=self._run, args=(stop_event,), daemon=True).start()

    def stop(self):
        with self._lock:
            if self._status not in ("starting", "running"):
                return
            self._status = "stopping"
            stop_event = self._stop_event
        if stop_event:
            stop_event.set()

    def set_current(self, current_a):
        self._current_a = float(current_a)  # rebinding is atomic, read by the physics thread each step

    def _run(self, stop_event):
        bus = None
        physics_thread = None
        try:
            db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals
            cells = create_cells(CELL_COUNT, self._params["capacity_ah"], self._params["initial_soc"], self._params["seed"])
            step_cells(cells, self._params["period"], self._current_a)  # first step compiles, too slow for the loop
            self._state["snapshot"] = snapshot(cells, 0.0, 0, self._current_a)

            bus = can.Bus(interface=self._params["interface"], channel=self._params["channel"], bitrate=self._params["bitrate"])
            physics_thread = threading.Thread(target=self._physics_loop, args=(cells, stop_event), daemon=True)
            physics_thread.start()
            self._status = "running"
            self._can_loop(db, bus, stop_event)
        except Exception as error:
            self._state["error"] = error
            self._status = "error"
        finally:
            stop_event.set()
            if physics_thread:
                physics_thread.join(timeout=5.0)
            if bus:
                bus.shutdown()
            if self._status != "error":
                self._status = "idle"

    def _physics_loop(self, cells, stop_event):
        sim_time = 0.0
        steps = 0
        previous = time.perf_counter()
        while not stop_event.is_set():
            now = time.perf_counter()
            dt = now - previous
            if dt < MIN_STEP_S:
                stop_event.wait(MIN_STEP_S - dt)
                continue
            previous = now
            try:
                step_cells(cells, dt, self._current_a)
            except Exception as error:
                self._state["error"] = error
                stop_event.set()
                return
            sim_time += dt
            steps += 1
            self._state["snapshot"] = snapshot(cells, sim_time, steps, self._current_a)  # atomic rebind, no lock needed

    def _can_loop(self, db, bus, stop_event):
        period = self._params["period"]
        next_deadline = time.perf_counter() + period
        while not stop_event.is_set():
            snap = self._state["snapshot"]
            if snap is not None:
                send_cell_voltages(bus, db, [clamp(v) for v in snap["voltages"]], verbose=False)
            now = time.perf_counter()
            remaining = next_deadline - now
            if remaining > 0:
                stop_event.wait(remaining)
                next_deadline += period
            else:
                self._overruns += 1
                next_deadline = now + period  # resync rather than trying to catch up


simulator = BatterySimulator()
