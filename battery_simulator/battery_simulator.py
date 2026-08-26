"""Real-time battery simulator: 16 PyBaMM Thevenin ECM cells published as vAFE_CellVoltage_* frames.

The solver is far slower than the 100ms CAN cycle, so it runs on its own thread and steps by the real
elapsed time. Simulated time still tracks the wall clock; only the voltage refresh rate is lower.
"""
import argparse
import pathlib
import sys
import threading
import time

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


def scaled_r0(base_r0, scale):
    """Wrap the ECM's R0 lookup, which is a callable of (T_cell, current, soc), with a fixed scale."""
    return lambda T_cell, current, soc: base_r0(T_cell, current, soc) * scale


def create_cells(count, capacity_ah, current_a, initial_soc, seed):
    """Build one independent Thevenin ECM per cell, with a seeded spread in capacity, R0 and SoC."""
    rng = np.random.default_rng(seed)
    cells = []
    for _ in range(count):
        model = pybamm.equivalent_circuit.Thevenin()
        params = model.default_parameter_values
        params["R0 [Ohm]"] = scaled_r0(params["R0 [Ohm]"], 1.0 + rng.uniform(-R0_SPREAD, R0_SPREAD))
        params["Cell capacity [A.h]"] = capacity_ah * (1.0 + rng.uniform(-CAPACITY_SPREAD, CAPACITY_SPREAD))
        params["Nominal cell capacity [A.h]"] = capacity_ah
        params["Current function [A]"] = current_a  # PyBaMM sign convention: positive discharges
        params["Initial SoC"] = float(np.clip(initial_soc + rng.uniform(-SOC_SPREAD, SOC_SPREAD), 0.0, 1.0))
        simulation = pybamm.Simulation(model, parameter_values=params, solver=pybamm.IDAKLUSolver())
        cells.append({"sim": simulation, "voltage": 0.0, "soc": 0.0, "exhausted": False})
    return cells


def step_cells(cells, dt):
    """Advance every live cell by dt seconds; cells that hit a cut-off hold their last voltage."""
    for cell in cells:
        if cell["exhausted"]:
            continue
        solution = cell["sim"].step(dt, save=False)
        cell["voltage"] = float(solution["Voltage [V]"].entries[-1])
        cell["soc"] = float(solution["SoC"].entries[-1])
        cell["exhausted"] = solution.termination != "final time"


def snapshot(cells, sim_time, steps):
    return {
        "voltages": [cell["voltage"] for cell in cells],
        "socs": [cell["soc"] for cell in cells],
        "exhausted": sum(1 for cell in cells if cell["exhausted"]),
        "sim_time": sim_time,
        "steps": steps,
    }


def run_physics(cells, state, stop_event):
    """Step the cells by the real time that has passed, publishing a snapshot the CAN loop can read."""
    sim_time = 0.0
    steps = 0
    previous = time.perf_counter()
    try:
        while not stop_event.is_set():
            now = time.perf_counter()
            dt = now - previous
            if dt < MIN_STEP_S:
                stop_event.wait(MIN_STEP_S - dt)
                continue
            previous = now
            step_cells(cells, dt)
            sim_time += dt
            steps += 1
            state["snapshot"] = snapshot(cells, sim_time, steps)  # rebinding is atomic, so no lock is needed
    except Exception as error:
        state["error"] = error
        stop_event.set()


def clamp(value):
    return min(max(value, SIGNAL_MIN_V), SIGNAL_MAX_V)


def print_status(elapsed, current_a, snap, overruns):
    voltages = snap["voltages"]
    socs = snap["socs"]
    print(
        f"t={elapsed:7.1f}s  I={current_a:6.2f}A  SoC={sum(socs) / len(socs):5.3f}  "
        f"V min={min(voltages):.3f} mean={sum(voltages) / len(voltages):.3f} max={max(voltages):.3f}  "
        f"pack={sum(voltages):7.3f}V  cutoff={snap['exhausted']}/{len(voltages)}  "
        f"sim={snap['sim_time']:7.1f}s  solver={snap['steps'] / max(elapsed, 1e-9):4.1f}Hz  overruns={overruns}"
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=float, default=5.0, help="constant cell current in A, positive discharges")
    parser.add_argument("--capacity", type=float, default=5.0, help="nominal cell capacity in Ah")
    parser.add_argument("--initial-soc", type=float, default=0.9, help="initial state of charge, 0..1")
    parser.add_argument("--period", type=float, default=0.1, help="CAN transmit period in s")
    parser.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    parser.add_argument("--seed", type=int, default=0, help="seed for the per-cell parameter spread")
    parser.add_argument("--interface", default="pcan", help="python-can interface, use 'virtual' to run without hardware")
    parser.add_argument("--channel", default="PCAN_USBBUS1")
    parser.add_argument("--bitrate", type=int, default=500000)
    return parser.parse_args()


def main():
    args = parse_args()
    db = cantools.database.load_file(DBC_PATH, strict=False)  # dbc has overlapping bit/mask signals

    print(f"Building {CELL_COUNT} Thevenin cells ({args.capacity} Ah, {args.current} A, SoC {args.initial_soc})...")
    cells = create_cells(CELL_COUNT, args.capacity, args.current, args.initial_soc, args.seed)

    print("Warming up solvers...")
    step_cells(cells, args.period)  # the first step per cell compiles, far too slow to leave in the loop

    state = {"snapshot": snapshot(cells, 0.0, 0), "error": None}
    stop_event = threading.Event()
    physics = threading.Thread(target=run_physics, args=(cells, state, stop_event), daemon=True)
    physics.start()

    bus = can.Bus(interface=args.interface, channel=args.channel, bitrate=args.bitrate)
    overruns = 0
    try:
        print(f"Sending cell voltages every {args.period * 1000:.0f}ms on {args.channel} (Ctrl+C to stop)...")
        start = time.perf_counter()
        next_deadline = start + args.period
        next_status = start
        while not stop_event.is_set():
            snap = state["snapshot"]
            send_cell_voltages(bus, db, [clamp(v) for v in snap["voltages"]], verbose=False)

            now = time.perf_counter()
            elapsed = now - start
            if now >= next_status:
                print_status(elapsed, args.current, snap, overruns)
                next_status += 1.0
            if args.duration is not None and elapsed >= args.duration:
                break

            remaining = next_deadline - now
            if remaining > 0:
                time.sleep(remaining)
                next_deadline += args.period
            else:
                overruns += 1
                next_deadline = now + args.period  # resync rather than trying to catch up
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        physics.join(timeout=5.0)
        bus.shutdown()
        print(f"Stopped after {overruns} deadline overrun(s).")

    if state["error"] is not None:
        raise state["error"]


if __name__ == "__main__":
    main()
