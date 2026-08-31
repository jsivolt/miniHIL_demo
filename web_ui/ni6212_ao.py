"""NI 6212 analog output helper for the web UI (mirrors ni6212/write_voltage.py)."""
import nidaqmx
import nidaqmx.system

DEFAULT_DEVICE = "Dev1"
DEFAULT_CHANNEL = "ao0"
MIN_VOLTAGE = 0.0
MAX_VOLTAGE = 3.3


def list_devices():
    """Return NI-DAQmx devices detected on this system, with their AO channel names."""
    system = nidaqmx.system.System.local()
    devices = []
    for device in system.devices:
        ao_channels = [chan.name.split("/")[-1] for chan in device.ao_physical_chans]
        devices.append({"name": device.name, "product_type": device.product_type, "ao_channels": ao_channels})
    return devices


def write_voltage(device, channel, voltage):
    """Write a single voltage sample to an AO channel, opening and closing the task per call."""
    voltage = float(voltage)
    if not MIN_VOLTAGE <= voltage <= MAX_VOLTAGE:
        raise ValueError(f"voltage {voltage} out of range [{MIN_VOLTAGE}, {MAX_VOLTAGE}]")
    physical_channel = f"{device}/{channel}"
    with nidaqmx.Task() as task:
        task.ao_channels.add_ao_voltage_chan(physical_channel)
        task.write(voltage)
    return physical_channel
