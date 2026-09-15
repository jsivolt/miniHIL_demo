"""Battery simulator engine: PyBaMM cells driven onto CAN as vAFE_CellVoltage_* frames.

Shared by the CLI script (battery_simulator.py) and the web UI (web_ui/app.py). The physics solve
runs on its own thread, stepped by real elapsed time, while a separate thread sends the latest
snapshot on the CAN bus every period. This lets a Flask request thread start/stop/monitor the
simulation without blocking, and lets the drive current be changed live via a pybamm.InputParameter.

The cell model (Thevenin ECM or a lithium-ion electrochemical model) and its parameter set are
selectable through CELL_MODELS; the two families need different parameter keys, so each entry
carries its own build/prepare hooks.
"""
import pathlib
import sys
import threading
import time
from functools import partial

import can
import cantools
import numpy as np
import pybamm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pcan"))

from write_cell_voltages import DBC_PATH, send_cell_voltages  # noqa: E402

CELL_COUNT = 16
SIGNAL_MIN_V = 0.0
SIGNAL_MAX_V = 6.5535  # vAFE cell voltage signals are 16-bit unsigned at 0.001 V/bit

CAPACITY_SPREAD = 0.03
R0_SPREAD = 0.10
SOC_SPREAD = 0.02
MIN_STEP_S = 0.01
CURRENT_INPUT_NAME = "Current function [A]"

# The simulator's own cell-voltage CAN output has no UI controls - always virtual, no PCAN hardware.
SIM_BUS_INTERFACE = "virtual"
SIM_BUS_CHANNEL = "battery-sim-cellv"
SIM_BUS_BITRATE = 1000000

RC2_R_SCALE = 0.5  # ECM_Example only defines one RC branch, so derive a slower second one from it
RC2_C_SCALE = 10.0
CONTACT_RESISTANCE_OHM = 0.01  # lithium-ion sets ship with 0 Ohm, which would leave no spread to scale

ECM_PARAMETER_SETS = ["ECM_Example"]
LITHIUM_ION_PARAMETER_SETS = [
    "Chen2020",
    "Marquis2019",
    "Ecker2015",
    "OKane2022",
    "Mohtat2020",
    "NCA_Kim2011",
    "Prada2013",
    "Ramadass2004",
]

# Chemistry summaries for the UI, taken from each set's docstring and OCP functions in
# pybamm/input/parameters. The capacity shown is PyBaMM's nominal value, which for some
# sets describes a single layer rather than a full cell.
PARAMETER_SET_INFO = {
    "ECM_Example": "Generic demo lookup tables (R0/R1/C1) - 100 Ah - 3.2-4.2 V",
    "Chen2020": "Graphite / NMC811 - LG M50 21700 - 5.0 Ah - 2.5-4.2 V",
    "OKane2022": "Graphite / NMC811 - LG M50 with degradation - 5.0 Ah - 2.5-4.2 V",
    "Marquis2019": "Graphite MCMB / LCO - Kokam pouch - 0.68 Ah - 3.1-4.1 V",
    "Ramadass2004": "Graphite MCMB / LCO - Kokam pouch with SEI - 1.0 Ah - 2.8-4.2 V",
    "Ecker2015": "Graphite / NCO - Kokam power pouch - 0.16 Ah - 2.5-4.2 V",
    "Mohtat2020": "Graphite / NMC532 - pouch - 5.0 Ah - 2.8-4.2 V",
    "NCA_Kim2011": "Graphite / NCA - pouch - 0.43 Ah - 2.7-4.2 V",
    "Prada2013": "Graphite / LFP - flat OCV plateau - 2.3 Ah - 2.0-3.6 V",
}

DEFAULT_PARAMS = {
    "model": "thevenin_1rc",
    "parameter_set": "ECM_Example",
    "current_a": 0.0,
    "capacity_ah": 30.0,
    "initial_soc": 0.5,
    "period": 0.1,
    "seed": 0,
    "interface": "pcan",
    "channel": "PCAN_USBBUS1",
    "bitrate": 1000000,
}


def scaled_lookup(base, scale):
    """Wrap an ECM lookup, which is a callable of (T_cell, current, soc), with a fixed scale."""
    return lambda T_cell, current, soc: base(T_cell, current, soc) * scale


def build_thevenin(rc_elements):
    return pybamm.equivalent_circuit.Thevenin(options={"number of rc elements": rc_elements})


def build_lithium_ion(model_cls):
    return model_cls(options={"contact resistance": "true"})


def prepare_ecm(model, params, rng, capacity_ah, initial_soc, rc_elements=1):
    """Apply the per-cell spread to an ECM parameter set and return the cell's initial SoC."""
    if rc_elements > 1:
        params.update(
            {
                "R2 [Ohm]": scaled_lookup(params["R1 [Ohm]"], RC2_R_SCALE),
                "C2 [F]": scaled_lookup(params["C1 [F]"], RC2_C_SCALE),
                "Element-2 initial overpotential [V]": 0,
            },
            check_already_exists=False,
        )
    params["R0 [Ohm]"] = scaled_lookup(params["R0 [Ohm]"], 1.0 + rng.uniform(-R0_SPREAD, R0_SPREAD))
    params["Cell capacity [A.h]"] = capacity_ah * (1.0 + rng.uniform(-CAPACITY_SPREAD, CAPACITY_SPREAD))
    params["Nominal cell capacity [A.h]"] = capacity_ah
    soc0 = float(np.clip(initial_soc + rng.uniform(-SOC_SPREAD, SOC_SPREAD), 0.0, 1.0))
    params["Initial SoC"] = soc0
    return soc0


def prepare_lithium_ion(model, params, rng, capacity_ah, initial_soc):
    """Same idea as prepare_ecm, but through the electrochemical model's own parameter keys."""
    # capacity scales roughly linearly with electrode thickness, which has no packing limit to clip against
    scale = capacity_ah / float(params["Nominal cell capacity [A.h]"]) * (1.0 + rng.uniform(-CAPACITY_SPREAD, CAPACITY_SPREAD))
    for key in ("Negative electrode thickness [m]", "Positive electrode thickness [m]"):
        params[key] = float(params[key]) * scale
    params["Nominal cell capacity [A.h]"] = capacity_ah
    params["Contact resistance [Ohm]"] = CONTACT_RESISTANCE_OHM * (1.0 + rng.uniform(-R0_SPREAD, R0_SPREAD))
    soc0 = float(np.clip(initial_soc + rng.uniform(-SOC_SPREAD, SOC_SPREAD), 0.0, 1.0))
    params.set_initial_state(soc0, param=model.param, options=model.options)
    return soc0


# soc_kind "state" reads the ECM's own SoC variable; "coulomb" integrates current, as the
# lithium-ion models only expose stoichiometries and a discharge capacity.
CELL_MODELS = {
    "thevenin_1rc": {
        "label": "Thevenin ECM (1 RC)",
        "build_model": partial(build_thevenin, 1),
        "prepare": prepare_ecm,
        "parameter_sets": ECM_PARAMETER_SETS,
        "soc_kind": "state",
        "realtime": True,
    },
    "thevenin_2rc": {
        "label": "Thevenin ECM (2 RC)",
        "build_model": partial(build_thevenin, 2),
        "prepare": partial(prepare_ecm, rc_elements=2),
        "parameter_sets": ECM_PARAMETER_SETS,
        "soc_kind": "state",
        "realtime": True,
    },
    "spm": {
        "label": "SPM (single particle)",
        "build_model": partial(build_lithium_ion, pybamm.lithium_ion.SPM),
        "prepare": prepare_lithium_ion,
        "parameter_sets": LITHIUM_ION_PARAMETER_SETS,
        "soc_kind": "coulomb",
        "realtime": True,
    },
    "spme": {
        "label": "SPMe (with electrolyte)",
        "build_model": partial(build_lithium_ion, pybamm.lithium_ion.SPMe),
        "prepare": prepare_lithium_ion,
        "parameter_sets": LITHIUM_ION_PARAMETER_SETS,
        "soc_kind": "coulomb",
        "realtime": True,
    },
    "dfn": {
        "label": "DFN (Doyle-Fuller-Newman)",
        "build_model": partial(build_lithium_ion, pybamm.lithium_ion.DFN),
        "prepare": prepare_lithium_ion,
        "parameter_sets": LITHIUM_ION_PARAMETER_SETS,
        "soc_kind": "coulomb",
        "realtime": False,
    },
}


def model_catalog():
    """Model list for the UI: id, label, the parameter sets it accepts and whether it can keep up."""
    return [
        {
            "id": model_id,
            "label": spec["label"],
            "parameter_sets": spec["parameter_sets"],
            "realtime": spec["realtime"],
        }
        for model_id, spec in CELL_MODELS.items()
    ]


def resolve_model(model_id, parameter_set):
    spec = CELL_MODELS.get(model_id)
    if spec is None:
        raise ValueError(f"unknown model '{model_id}', expected one of {sorted(CELL_MODELS)}")
    if parameter_set not in spec["parameter_sets"]:
        raise ValueError(f"parameter set '{parameter_set}' is not valid for model '{model_id}'")
    return spec


def create_cells(count, model_id, parameter_set, capacity_ah, initial_soc, seed):
    """Build one independent cell simulation per cell, with a seeded spread in capacity, R and SoC.

    The drive current is left as a pybamm.InputParameter so it can be changed between steps
    without rebuilding the simulation. It is injected after the spread so that the lithium-ion
    initial-state solve still sees a plain number.
    """
    spec = resolve_model(model_id, parameter_set)
    rng = np.random.default_rng(seed)
    cells = []
    for _ in range(count):
        model = spec["build_model"]()
        params = pybamm.ParameterValues(parameter_set)
        soc0 = spec["prepare"](model, params, rng, capacity_ah, initial_soc)
        params[CURRENT_INPUT_NAME] = pybamm.InputParameter(CURRENT_INPUT_NAME)
        simulation = pybamm.Simulation(model, parameter_values=params, solver=pybamm.IDAKLUSolver())
        cells.append(
            {
                "sim": simulation,
                "voltage": 0.0,
                "soc": soc0,
                "exhausted": False,
                "soc_kind": spec["soc_kind"],
                "soc0": soc0,
                "capacity_ah": capacity_ah,
            }
        )
    return cells


def read_soc(cell, solution):
    if cell["soc_kind"] == "state":
        return float(solution["SoC"].entries[-1])
    discharged = float(solution["Discharge capacity [A.h]"].entries[-1])
    return float(np.clip(cell["soc0"] - discharged / cell["capacity_ah"], 0.0, 1.0))


def step_cells(cells, dt, current_a):
    """Advance every live cell by dt seconds; cells that hit a cut-off hold their last voltage."""
    inputs = {CURRENT_INPUT_NAME: -current_a}  # flip to this module's convention: positive current_a charges
    for cell in cells:
        if cell["exhausted"]:
            continue
        solution = cell["sim"].step(dt, inputs=inputs, save=False)
        cell["voltage"] = float(solution["Voltage [V]"].entries[-1])
        cell["soc"] = read_soc(cell, solution)
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
            requested = {**DEFAULT_PARAMS, **params}
            resolve_model(requested["model"], requested["parameter_set"])  # fail before anything is committed
            self._params = requested
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
            cells = create_cells(
                CELL_COUNT,
                self._params["model"],
                self._params["parameter_set"],
                self._params["capacity_ah"],
                self._params["initial_soc"],
                self._params["seed"],
            )
            step_cells(cells, self._params["period"], self._current_a)  # first step compiles, too slow for the loop
            self._state["snapshot"] = snapshot(cells, 0.0, 0, self._current_a)

            bus = can.Bus(interface=SIM_BUS_INTERFACE, channel=SIM_BUS_CHANNEL, bitrate=SIM_BUS_BITRATE)
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
