"""Example client for calling the FastAPI service (fastapi_app/main.py) from another PC.

Run this from a different machine on the same network as the server (replace SERVER_HOST
below, or pass --host, with the server PC's IP address - e.g. the one shown when you start
run_fastapi.bat). The server must be started with a network-reachable bind address, which
is the default (0.0.0.0) when using run_fastapi.bat or `python fastapi_app/main.py`.

Usage:
    python client_example.py --host 192.168.1.50 --port 8000
"""
import argparse
import time

import httpx


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="192.168.1.50", help="IP address of the PC running run_fastapi.bat")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main():
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"

    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        print("bus summary:", client.get("/api/vcellpack/buses").json())
        print("status before start:", client.get("/api/vcellpack/status").json())

        response = client.post(
            "/api/vcellpack/start",
            json={"interface": "virtual", "bitrate": 500000, "pack_current_a": -10.0, "cell_voltage_v": 3.7, "period": 0.2},
        )
        response.raise_for_status()
        print("start:", response.json())

        time.sleep(1)
        print("status while running:", client.get("/api/vcellpack/status").json())

        print("stop:", client.post("/api/vcellpack/stop").json())

        devices = client.get("/api/ao/devices").json()
        print("AO devices:", devices)


if __name__ == "__main__":
    main()
