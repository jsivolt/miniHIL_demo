"""Read all digital input lines on Dev1/port1 with the NI 6212."""
import nidaqmx
from nidaqmx.constants import LineGrouping


def main():
    with nidaqmx.Task() as task:
        task.di_channels.add_di_chan("Dev1/port1/line0:3", line_grouping=LineGrouping.CHAN_PER_LINE)
        states = task.read()
        for line, state in enumerate(states):
            print(f"Dev1/port1/line{line}: {'HIGH' if state else 'LOW'}")


if __name__ == "__main__":
    main()
