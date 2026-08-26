"""Flask web UI to start/stop the battery simulator and monitor/control it live.

Run with: python app.py  (serves on http://127.0.0.1:5000 by default, local-only)
"""
import argparse
import pathlib
import sys

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "battery_simulator"))

from battery_sim_core import (  # noqa: E402
    CELL_COUNT,
    DEFAULT_PARAMS,
    PARAMETER_SET_INFO,
    model_catalog,
    simulator,
)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "index.html",
        cell_count=CELL_COUNT,
        defaults=DEFAULT_PARAMS,
        models=model_catalog(),
        parameter_set_info=PARAMETER_SET_INFO,
    )


@app.route("/api/status")
def api_status():
    return jsonify(simulator.get_status())


@app.route("/api/start", methods=["POST"])
def api_start():
    body = request.get_json(silent=True) or {}
    try:
        simulator.start(
            model=str(body.get("model", DEFAULT_PARAMS["model"])),
            parameter_set=str(body.get("parameter_set", DEFAULT_PARAMS["parameter_set"])),
            current_a=float(body.get("current_a", DEFAULT_PARAMS["current_a"])),
            capacity_ah=float(body.get("capacity_ah", DEFAULT_PARAMS["capacity_ah"])),
            initial_soc=float(body.get("initial_soc", DEFAULT_PARAMS["initial_soc"])),
            period=float(body.get("period", DEFAULT_PARAMS["period"])),
            seed=int(body.get("seed", DEFAULT_PARAMS["seed"])),
            interface=str(body.get("interface", DEFAULT_PARAMS["interface"])),
            channel=str(body.get("channel", DEFAULT_PARAMS["channel"])),
            bitrate=int(body.get("bitrate", DEFAULT_PARAMS["bitrate"])),
        )
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    simulator.stop()
    return jsonify({"ok": True})


@app.route("/api/current", methods=["POST"])
def api_current():
    body = request.get_json(silent=True) or {}
    try:
        current_a = float(body["current_a"])
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid current: {error}"}), 400
    simulator.set_current(current_a)
    return jsonify({"ok": True})


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind address, defaults to localhost only")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()


def main():
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
