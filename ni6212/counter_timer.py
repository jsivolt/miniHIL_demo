"""Count edges on Dev1/ctr0 over a fixed sample period."""
import time

import nidaqmx
from nidaqmx.constants import Edge

SAMPLE_PERIOD_S = 2.0


def main():
    with nidaqmx.Task() as task:
        task.ci_channels.add_ci_count_edges_chan("Dev1/ctr0", edge=Edge.RISING)
        task.start()
        time.sleep(SAMPLE_PERIOD_S)
        count = task.read()
        task.stop()

    print(f"Counted {count} rising edges on Dev1/ctr0 over {SAMPLE_PERIOD_S:.1f} s")


if __name__ == "__main__":
    main()
