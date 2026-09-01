"""FastAPI service exposing vcellpack_sender and ni6212_ao (see web_ui/app.py for the
equivalent Flask routes this mirrors).

Run directly with: python main.py [--host 0.0.0.0] [--port 8000]
(defaults to 0.0.0.0 so other PCs on the same network can reach it - see
client_example.py; there is no authentication, so only run this on a trusted network.)
Or from the repo root: run_fastapi.bat, or `uvicorn fastapi_app.main:app --host 0.0.0.0 --port 8000`.
"""
import argparse
import pathlib
import sys

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web_ui"))

import ni6212_ao  # noqa: E402
import vcellpack_sender  # noqa: E402

# Matches battery_simulator.battery_sim_core.DEFAULT_PARAMS (not imported here to avoid
# pulling in pybamm just for these two endpoints).
DEFAULT_INTERFACE = "virtual"
DEFAULT_BITRATE = 1000000

app = FastAPI(title="miniHIL vcellpack/AO API")


class VCellPackStartRequest(BaseModel):
    interface: str = DEFAULT_INTERFACE
    bitrate: int = DEFAULT_BITRATE
    pack_current_a: float = -70.0
    cell_voltage_v: float = 3.7
    period: float = 0.2


class VCellPackUpdateRequest(BaseModel):
    pack_current_a: float | None = None
    cell_voltage_v: float | None = None
    period: float | None = None


class AoWriteRequest(BaseModel):
    device: str = ni6212_ao.DEFAULT_DEVICE
    channel: str = ni6212_ao.DEFAULT_CHANNEL
    voltage: float


@app.get("/api/vcellpack/buses")
def api_vcellpack_buses():
    return vcellpack_sender.bus_message_summary()


@app.get("/api/vcellpack/status")
def api_vcellpack_status():
    return vcellpack_sender.sender.get_status()


@app.post("/api/vcellpack/start")
def api_vcellpack_start(body: VCellPackStartRequest):
    try:
        vcellpack_sender.sender.start(
            body.interface, body.bitrate, body.pack_current_a, body.cell_voltage_v, body.period
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid parameters: {error}")
    return {"ok": True}


@app.post("/api/vcellpack/update")
def api_vcellpack_update(body: VCellPackUpdateRequest):
    try:
        vcellpack_sender.sender.update(body.pack_current_a, body.cell_voltage_v, body.period)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid parameters: {error}")
    return {"ok": True}


@app.post("/api/vcellpack/stop")
def api_vcellpack_stop():
    vcellpack_sender.sender.stop()
    return {"ok": True}


@app.get("/api/ao/devices")
def api_ao_devices():
    try:
        devices = ni6212_ao.list_devices()
    except Exception as error:  # NI-DAQmx driver/transport errors
        raise HTTPException(status_code=500, detail=str(error))
    return {"ok": True, "devices": devices}


@app.post("/api/ao/write")
def api_ao_write(body: AoWriteRequest):
    try:
        physical_channel = ni6212_ao.write_voltage(body.device, body.channel, body.voltage)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"invalid parameters: {error}")
    except Exception as error:  # nidaqmx driver/hardware errors
        raise HTTPException(status_code=500, detail=f"write failed: {error}")
    return {"ok": True, "channel": physical_channel, "voltage": body.voltage}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind address, defaults to all interfaces")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = parse_args()
    uvicorn.run(app, host=args.host, port=args.port)

