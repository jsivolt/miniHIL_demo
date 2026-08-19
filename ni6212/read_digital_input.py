"""Read a single digital input value from Dev1/port0/line1."""
import nidaqmx


def main():
    with nidaqmx.Task() as task:
        task.di_channels.add_di_chan("Dev1/port0/line1")
        state = task.read()
        print(f"Dev1/port0/line1: {'HIGH' if state else 'LOW'}")


if __name__ == "__main__":
    main()
