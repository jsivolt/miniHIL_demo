"""Output a single voltage sample on Dev1/ao0."""
import sys

import nidaqmx


def main():
    voltage = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0

    with nidaqmx.Task() as task:
        task.ao_channels.add_ao_voltage_chan("Dev1/ao0")
        task.write(voltage)
        print(f"Wrote {voltage:.4f} V to Dev1/ao0")


if __name__ == "__main__":
    main()
