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
import can_receiver  # noqa: E402
import can_sender  # noqa: E402
import current_source  # noqa: E402
import ni6212_ao  # noqa: E402
import vcellpack_sender  # noqa: E402

app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "index.html",
        cell_count=CELL_COUNT,
        defaults=DEFAULT_PARAMS,
        models=model_catalog(),
        parameter_set_info=PARAMETER_SET_INFO,
        can_messages=can_sender.list_dbc_messages(),
        ao_defaults={"device": ni6212_ao.DEFAULT_DEVICE, "channel": ni6212_ao.DEFAULT_CHANNEL},
        vcellpack_buses=vcellpack_sender.bus_message_summary(),
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
        )
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    simulator.stop()
    current_source.source.stop()
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


@app.route("/api/current-source/start", methods=["POST"])
def api_current_source_start():
    try:
        current_source.source.start()
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except Exception as error:  # python-can driver/transport errors
        return jsonify({"ok": False, "error": str(error)}), 500
    return jsonify({"ok": True})


@app.route("/api/current-source/stop", methods=["POST"])
def api_current_source_stop():
    current_source.source.stop()
    return jsonify({"ok": True})


@app.route("/api/current-source/status")
def api_current_source_status():
    return jsonify(current_source.source.get_status())


@app.route("/api/can/devices")
def api_can_devices():
    try:
        devices = can_sender.list_pcan_devices()
    except Exception as error:  # python-can driver/transport errors
        return jsonify({"ok": False, "error": str(error)}), 500
    return jsonify({"ok": True, "devices": devices})


def _parse_can_message(body):
    """Pull interface/channel/bitrate and an encoded (id, data, is_extended) message out of a request body."""
    interface = str(body.get("interface", DEFAULT_PARAMS["interface"]))
    channel = str(body.get("channel", DEFAULT_PARAMS["channel"]))
    bitrate = int(body.get("bitrate", DEFAULT_PARAMS["bitrate"]))
    mode = str(body.get("mode", "raw"))

    if mode == "dbc":
        message_name = str(body["message"])
        signal_values = dict(body.get("signals", {}))
        arbitration_id, data, is_extended_id = can_sender.encode_dbc_message(message_name, signal_values)
    else:
        arbitration_id = int(str(body["arbitration_id"]), 0)
        data = bytes(int(b) for b in body.get("data", []))
        is_extended_id = bool(body.get("extended", False))
    return interface, channel, bitrate, arbitration_id, data, is_extended_id


@app.route("/api/can/send", methods=["POST"])
def api_can_send():
    body = request.get_json(silent=True) or {}
    try:
        interface, channel, bitrate, arbitration_id, data, is_extended_id = _parse_can_message(body)
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    except Exception as error:  # cantools encode errors (missing/out-of-range signal, etc.)
        return jsonify({"ok": False, "error": f"encode failed: {error}"}), 400

    try:
        message = can_sender.send_message(interface, channel, bitrate, arbitration_id, data, is_extended_id)
    except Exception as error:  # python-can bus/transport errors
        return jsonify({"ok": False, "error": f"send failed: {error}"}), 500
    return jsonify({"ok": True, "sent": str(message)})


@app.route("/api/can/repeat/start", methods=["POST"])
def api_can_repeat_start():
    body = request.get_json(silent=True) or {}
    try:
        interface, channel, bitrate, arbitration_id, data, is_extended_id = _parse_can_message(body)
        period = float(body.get("period", 1.0))
        if period <= 0:
            raise ValueError("period must be greater than 0")
        counter_byte_index = body.get("counter_byte_index")
        if counter_byte_index is not None:
            counter_byte_index = int(counter_byte_index)
            if not 0 <= counter_byte_index < len(data):
                raise ValueError("counter_byte_index is out of range for the message data")
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    except Exception as error:  # cantools encode errors (missing/out-of-range signal, etc.)
        return jsonify({"ok": False, "error": f"encode failed: {error}"}), 400

    try:
        can_sender.repeat_sender.start(
            interface, channel, bitrate, arbitration_id, data, is_extended_id, period,
            str(body.get("mode", "raw")), counter_byte_index,
        )
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    return jsonify({"ok": True})


@app.route("/api/can/repeat/stop", methods=["POST"])
def api_can_repeat_stop():
    can_sender.repeat_sender.stop()
    return jsonify({"ok": True})


@app.route("/api/can/repeat/status")
def api_can_repeat_status():
    return jsonify(can_sender.repeat_sender.get_status())


@app.route("/api/can/receive/start", methods=["POST"])
def api_can_receive_start():
    body = request.get_json(silent=True) or {}
    try:
        interface = str(body.get("interface", DEFAULT_PARAMS["interface"]))
        channel = str(body.get("channel", DEFAULT_PARAMS["channel"]))
        bitrate = int(body.get("bitrate", can_receiver.DEFAULT_BITRATE))
        device_id = body.get("device_id", can_receiver.DEFAULT_DEVICE_ID)
        device_id = int(device_id) if device_id is not None else None
        can_receiver.receiver.start(interface, channel, bitrate, device_id)
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    return jsonify({"ok": True})


@app.route("/api/can/receive/stop", methods=["POST"])
def api_can_receive_stop():
    can_receiver.receiver.stop()
    return jsonify({"ok": True})


@app.route("/api/can/receive/status")
def api_can_receive_status():
    return jsonify(can_receiver.receiver.get_status())


@app.route("/api/can/receive/send", methods=["POST"])
def api_can_receive_send():
    """Send on the CAN Receiver tab's already-open bus, avoiding a second PCAN handle on the same channel."""
    body = request.get_json(silent=True) or {}
    try:
        _, _, _, arbitration_id, data, is_extended_id = _parse_can_message(body)
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    except Exception as error:  # cantools encode errors (missing/out-of-range signal, etc.)
        return jsonify({"ok": False, "error": f"encode failed: {error}"}), 400

    try:
        message = can_receiver.receiver.send(arbitration_id, data, is_extended_id)
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except Exception as error:  # python-can bus/transport errors
        return jsonify({"ok": False, "error": f"send failed: {error}"}), 500
    return jsonify({"ok": True, "sent": str(message)})


@app.route("/api/vcellpack/start", methods=["POST"])
def api_vcellpack_start():
    body = request.get_json(silent=True) or {}
    try:
        interface = str(body.get("interface", DEFAULT_PARAMS["interface"]))
        bitrate = int(body.get("bitrate", DEFAULT_PARAMS["bitrate"]))
        pack_current_a = float(body.get("pack_current_a", -70.0))
        cell_voltage_v = float(body.get("cell_voltage_v", 3.7))
        period = float(body.get("period", 0.2))
        vcellpack_sender.sender.start(interface, bitrate, pack_current_a, cell_voltage_v, period)
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    return jsonify({"ok": True})


@app.route("/api/vcellpack/update", methods=["POST"])
def api_vcellpack_update():
    body = request.get_json(silent=True) or {}
    try:
        pack_current_a = float(body["pack_current_a"]) if "pack_current_a" in body else None
        cell_voltage_v = float(body["cell_voltage_v"]) if "cell_voltage_v" in body else None
        period = float(body["period"]) if "period" in body else None
        vcellpack_sender.sender.update(pack_current_a, cell_voltage_v, period)
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400
    return jsonify({"ok": True})


@app.route("/api/vcellpack/stop", methods=["POST"])
def api_vcellpack_stop():
    vcellpack_sender.sender.stop()
    return jsonify({"ok": True})


@app.route("/api/vcellpack/status")
def api_vcellpack_status():
    return jsonify(vcellpack_sender.sender.get_status())


@app.route("/api/ao/devices")
def api_ao_devices():
    try:
        devices = ni6212_ao.list_devices()
    except Exception as error:  # NI-DAQmx driver/transport errors
        return jsonify({"ok": False, "error": str(error)}), 500
    return jsonify({"ok": True, "devices": devices})


@app.route("/api/ao/write", methods=["POST"])
def api_ao_write():
    body = request.get_json(silent=True) or {}
    try:
        device = str(body.get("device", ni6212_ao.DEFAULT_DEVICE))
        channel = str(body.get("channel", ni6212_ao.DEFAULT_CHANNEL))
        voltage = float(body["voltage"])
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": f"invalid parameters: {error}"}), 400

    try:
        physical_channel = ni6212_ao.write_voltage(device, channel, voltage)
    except Exception as error:  # nidaqmx driver/hardware errors
        return jsonify({"ok": False, "error": f"write failed: {error}"}), 500
    return jsonify({"ok": True, "channel": physical_channel, "voltage": voltage})


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
