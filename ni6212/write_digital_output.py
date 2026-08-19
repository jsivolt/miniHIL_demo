"""Write a single digital output value on Dev1/port0/line0."""
import sys

import nidaqmx


def main():
    state = sys.argv[1].lower() in ("1", "true", "on", "high") if len(sys.argv) > 1 else True

    with nidaqmx.Task() as task:
        task.do_channels.add_do_chan("Dev1/port0/line0")
        task.write(state)
        print(f"Wrote {'HIGH' if state else 'LOW'} to Dev1/port0/line0")


if __name__ == "__main__":
    main()
