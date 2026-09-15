# miniHIL_demo

A desktop **Hardware-in-the-Loop (HIL) demo** that simulates a 16-cell automotive battery pack
and publishes it onto a real CAN bus (PCAN) while driving NI‑DAQmx I/O on a **NI USB‑6212**.
It is aimed at exercising a BMS (e.g. an S32K344 project) without needing a real battery.

It ships two servers that expose the same hardware behind a small HTTP API:

- a **Flask web UI** (`web_ui/`) with a browser dashboard, and
- a **FastAPI service** (`fastapi_app/`) meant to be called from other PCs on the network.

Everything is driven by `python-can`, `cantools` (DBC decoding/encoding), `pybamm`
(battery physics), and `nidaqmx`.

---

## Repository layout

| Path | Purpose |
|---|---|
| `battery_simulator/battery_sim_core.py` | Reusable battery simulator engine: 16 PyBaMM cells stepped on a background thread, published as `vAFE_CellVoltage_*` CAN frames. Shared by the CLI and the web UI. |
| `battery_simulator/battery_simulator.py` | Standalone CLI version of the battery simulator (Thevenin ECM). |
| `web_ui/app.py` | Flask app serving the dashboard (`http://127.0.0.1:5000`). |
| `web_ui/templates/index.html` | Single-page dashboard with 5 tabs: **Battery Simulator**, **CAN Sender**, **CAN Receiver**, **NI 6212 Analog Output**, **Cell/Pack Sim**. |
| `web_ui/can_sender.py` | One-shot & repeating CAN message sending (raw or DBC-encoded, with an optional auto-incrementing counter byte) for the CAN Sender tab. |
| `web_ui/can_receiver.py` | Background CAN listener/decoder (raw + DBC) for the CAN Receiver tab; also exposes a `send()` hook so other modules can piggyback on its open bus instead of opening a second PCAN handle. |
| `web_ui/current_source.py` | Feeds the Battery Simulator's live current from the `Pack1Current` signal read off PCAN device 0 ("Live current override"); shares the CAN Receiver tab's bus when it's already running, else opens its own. |
| `web_ui/vcellpack_sender.py` | Continuous `vPack_Current` / `vPack_Voltage` / `vAFE_CellVoltage_*` sender on two PCAN buses for the Cell/Pack Sim tab. |
| `web_ui/ni6212_ao.py` | NI 6212 analog-output helper (mirrors `ni6212/write_voltage.py`) for the NI 6212 Analog Output tab. |
| `fastapi_app/main.py` | FastAPI service exposing the vcellpack sender + NI 6212 AO (not the battery sim or CAN sender). |
| `fastapi_app/client_example.py` | Example `httpx` client for calling the FastAPI service from another PC. |
| `pcan/` | Standalone PCAN scripts: find devices, read/listen, raw/DBC write, cell-voltage and pack-voltage senders, and `BMS_demo.dbc`. |
| `ni6212/` | Standalone NI 6212 scripts: list devices, AI/AO, digital I/O, counter/timer. |
| `logic2/` | Saleae Logic 2 automation helper (lists devices). |
| `pybop_calibration/` | **Isolated** environment for PyBOP-based battery-model calibration. Has its own venv; see its `README.md`. |
| `script/close_s32ds.ps1` (+ `.bat`) | Closes S32 Design Studio (NXP) — with UAC elevation fallback. |
| `run_web_ui.bat`, `run_fastapi.bat` | One-click launchers for the two servers. |

---

## Hardware requirements

- **PCAN-USB adapter** (PEAK driver installed) — CAN output.
- **NI USB‑6212** with NI‑DAQmx driver — analog in/out, digital I/O, counter/timer.
- *(Optional)* **Saleae Logic 2** running with the `logic2-automation` package.
- A **BMS under test** listening on the CAN bus (e.g. S32K344).

> Note: the Cell/Pack Sim tab uses an **external DBC** at
> `C:\S32K344\workspace\BMS_demo\DBC\BMS_demo.dbc` (this is machine-specific). The repo's
> `pcan/BMS_demo.dbc` is an older revision used by the CAN Sender tab.

---

## Setup

```powershell
# create & activate a virtual environment (Python 3.13.x)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# install dependencies
pip install -r requirements.txt
```

`requirements.txt` includes:

- `python-can`, `cantools` — CAN bus + DBC
- `pybamm` — battery physics models
- `nidaqmx` — NI 6212 I/O
- `flask` — web UI
- `fastapi`, `uvicorn`, `httpx` — FastAPI service (+ test client)

> `pybop_calibration/` has **its own venv** and lock file — do **not** install its
> `requirements.lock` into the root `.venv`.

---

## Quick start

### 1. Web UI (Flask)

```powershell
python web_ui/app.py
# or double-click run_web_ui.bat
# open http://127.0.0.1:5000
```

By default `web_ui/app.py` binds to `127.0.0.1` (localhost only). `run_web_ui.bat` passes
`--host 0.0.0.0` so other PCs on the same LAN can reach it too — from another machine, open
`http://<this-PC's-LAN-IP>:5000` (find the IP with `ipconfig`). **No authentication** — only
do this on a trusted network, and allow inbound port 5000 through Windows Firewall if prompted.
To keep it localhost-only, run `python web_ui/app.py` directly (or `--host 127.0.0.1`) instead.

Tabs:

- **Battery Simulator** — start/stop a 16-cell PyBaMM sim (Thevenin ECM or lithium-ion
  chemistry from Chen2020 / Marquis2019 / …), set current / capacity / initial SoC / period,
  and change the drive current live without restarting. A "Live current override" toggle can
  instead drive the current from the `Pack1Current` signal read off PCAN device 0.
- **CAN Sender** — send one-shot or repeating CAN frames, either **raw**
  (arbitration id + data bytes, with an optional auto-incrementing counter byte) or **DBC**
  (pick a message and fill signal values from `BMS_demo.dbc`; `VAL_`-table signals render as
  dropdowns).
- **CAN Receiver** — listen on a PCAN device/channel, decode frames against `BMS_demo.dbc`
  (unknown frames shown as raw hex), and view either a raw message log or a "Battery values"
  summary (pack voltage/current, BMS state, per-pack temperature/power/SoC, cell voltages) with
  a control to send a `BMS_ControlCommand` (NOP/Enable/Disable/ClearFault) on the same bus.
- **NI 6212 Analog Output** — pick a device/channel and write an analog output voltage.
- **Cell/Pack Sim** — continuously stream jittered 16-cell voltages on a "cell bus" and
  derived pack voltage/current on a "pack bus" (fixed PCAN device IDs), until stopped, optionally
  mirroring the Battery Simulator's live cell voltages instead of jittered ones.

> While the simulator holds a PCAN channel open, sending on the *same* channel from the
> CAN Sender tab can fail — documented in the UI. The CAN Receiver tab's "Send" control avoids
> this by reusing its own already-open bus instead of opening a second handle.

### 2. FastAPI service (network-reachable)

```powershell
python fastapi_app/main.py            # binds 0.0.0.0:8000
# or: run_fastapi.bat
# or: uvicorn fastapi_app.main:app --host 0.0.0.0 --port 8000
```

**No authentication** — only run on a trusted network. From another PC:

```powershell
python fastapi_app\client_example.py --host <server-ip> --port 8000
```

---

## API reference

### Web UI (`web_ui/app.py`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api/status` | Simulator status |
| POST | `/api/start` | Start sim (`model`, `parameter_set`, `current_a`, `capacity_ah`, `initial_soc`, `period`, `seed`, `interface`, `channel`, `bitrate`) |
| POST | `/api/stop` | Stop sim (and the live current-source override, if running) |
| POST | `/api/current` | Set drive current live (`current_a`) |
| POST | `/api/current-source/start` | Start driving the sim's current live from PCAN device 0's `Pack1Current` signal |
| POST | `/api/current-source/stop` | Stop the live current source |
| GET | `/api/current-source/status` | Live current source status |
| GET | `/api/can/devices` | List PCAN channels |
| POST | `/api/can/send` | Send raw or DBC CAN message (opens/closes the bus per call) |
| POST | `/api/can/repeat/start` | Start repeating a raw or DBC message on a period (`period`, optional `counter_byte_index`) |
| POST | `/api/can/repeat/stop` | Stop the repeating sender |
| GET | `/api/can/repeat/status` | Repeating sender status |
| POST | `/api/can/receive/start` | Start listening/decoding (`interface`, `channel`, `bitrate`, `device_id`) |
| POST | `/api/can/receive/stop` | Stop listening |
| GET | `/api/can/receive/status` | Listener status + decoded message log |
| POST | `/api/can/receive/send` | Send raw or DBC message on the receiver's already-open bus |
| POST | `/api/vcellpack/start` | Start Cell/Pack sim (`interface`, `bitrate`, `pack_current_a`, `cell_voltage_v`, `period`, `use_simulator_voltages`) |
| POST | `/api/vcellpack/update` | Update running Cell/Pack sim |
| POST | `/api/vcellpack/stop` | Stop Cell/Pack sim |
| GET | `/api/vcellpack/status` | Cell/Pack sim status |
| GET | `/api/ao/devices` | List NI-DAQmx devices |
| POST | `/api/ao/write` | Write NI 6212 AO voltage (`device`, `channel`, `voltage`) |

### FastAPI (`fastapi_app/main.py`)

Mirrors the Flask routes 1:1, but only for the vcellpack sender and the NI 6212 AO
(via pydantic request bodies). Status-code mapping: `RuntimeError` → `409`,
`(KeyError/TypeError/ValueError)` → `400`, other `Exception` → `500`.

| Method | Endpoint |
|---|---|
| GET | `/api/vcellpack/buses`, `/api/vcellpack/status` |
| POST | `/api/vcellpack/start`, `/api/vcellpack/update`, `/api/vcellpack/stop` |
| GET | `/api/ao/devices` |
| POST | `/api/ao/write` |

---

## Standalone scripts

### PCAN (`pcan/`)

| Script | Purpose |
|---|---|
| `find_device.py` | List detected PCAN channels |
| `read_message.py` | Listen & print CAN frames on `PCAN_USBBUS1` |
| `write_message.py` | Send hardcoded raw frames (counter + cell-voltage demo) |
| `write_dbc_message.py` | Send `BMS_ControlCommand` from `BMS_demo.dbc` |
| `write_cell_voltages.py` | Send 16 cell voltages as `vAFE_CellVoltage_*` frames |
| `write_vCell_vPack_dbc.py` | Send pack current/voltage + cell voltages across two PCAN devices (external DBC) |

### NI 6212 (`ni6212/`)

| Script | Purpose |
|---|---|
| `check_device.py` | List NI-DAQmx devices |
| `read_voltage.py` | Read one sample on `Dev1/ai0` |
| `write_voltage.py` | Write one sample on `Dev1/ao0` |
| `read_digital_input.py` | Read `Dev1/port0/line1` |
| `read_di_port1.py` | Read all lines on `Dev1/port1/line0:3` |
| `write_digital_output.py` | Write `Dev1/port0/line0` |
| `counter_timer.py` | Count rising edges on `Dev1/ctr0` over 2 s |

### Saleae Logic (`logic2/`)

| Script | Purpose |
|---|---|
| `find_device.py` | List Saleae devices via a running Logic 2 instance |

---

## Battery simulator details

- **16 cells**, each an independent model with seeded spread in capacity (±3%), series
  resistance (±10%) and initial SoC (±2%), so the pack looks realistic.
- **Model families** (see `battery_sim_core.CELL_MODELS`): Thevenin ECM
  (`thevenin_1rc`, `thevenin_2rc`) and lithium-ion electrochemistry (`spm`, `spme`, `dfn`).
  ECM and lithium-ion parameter sets are **not interchangeable** — `start()` validates the
  combination and returns HTTP 400 if mismatched.
- The physics solve runs on its own thread stepped by real elapsed time; a second thread
  publishes the latest snapshot every `period`. The drive current is a
  `pybamm.InputParameter`, so it can change live without restarting.
- Measured on this machine (16 cells): ECM ~5.7 ms/step, SPM ~1.9 ms, SPMe ~3.3 ms,
  DFN ~4.4 ms — all near real-time.

---

## Calibration (`pybop_calibration/`)

An isolated PyBOP environment for fitting the PyBaMM models. See
[`pybop_calibration/README.md`](pybop_calibration/README.md) for solver-backend details,
lock-file regeneration, and the conda-forge alternative. Use its own interpreter:

```powershell
pybop_calibration\.venv\Scripts\python.exe ...
```

---

## Development notes

- The servers are typically tested via `app.test_client()` / `fastapi.testclient.TestClient`
  rather than launching the real dev server, which is unstable in this setup.
- `fastapi_app/main.py` and `web_ui/app.py` import sibling modules by inserting
  `<repo_root>/<folder>` onto `sys.path` (see top of each file).
- `script/close_s32ds.ps1` requires the user to confirm a UAC prompt when it needs to
  elevate to close another user's `s32ds` process.
