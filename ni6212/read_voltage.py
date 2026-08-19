"""Read a single voltage sample from Dev1/ai0."""
import nidaqmx


def main():
    with nidaqmx.Task() as task:
        task.ai_channels.add_ai_voltage_chan("Dev1/ai0")
        voltage = task.read()
        print(f"Voltage on Dev1/ai0: {voltage:.4f} V")


if __name__ == "__main__":
    main()
