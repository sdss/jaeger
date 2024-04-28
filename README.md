# jaeger

![Versions](https://img.shields.io/badge/python->=3.10-blue)
[![Documentation Status](https://readthedocs.org/projects/sdss-jaeger/badge/?version=latest)](https://sdss-jaeger.readthedocs.io/en/latest/?badge=latest)
[![Tests Status](https://github.com/sdss/jaeger/workflows/Test/badge.svg)](https://github.com/sdss/jaeger/actions)
[![codecov](https://codecov.io/gh/sdss/jaeger/branch/main/graph/badge.svg)](https://codecov.io/gh/sdss/jaeger)

[jaeger](http://pacificrim.wikia.com/wiki/Jaeger>) provides high level control for the SDSS-V [Focal Plane System](https://wiki.sdss.org/display/FPS). Some of the features that jaeger provide are:

- Wraps the low level CAN commands for simpler use.
- Provides a framework that is independent of the CAN interface used (by using the [python-can](https://python-can.readthedocs.io/en/master/) library).
- Interfaces with [kaiju](https://github.com/sdss/kaiju) to provide anticollision path planning for trajectories.
- Implements status and position update loops.
- Provides implementations for commonly used tasks (e.g., go to position, send trajectory).
- Interfaces with the Instrument Electronics Box modbus PLC controller.
- Provides a TCP/IP interface to send commands and output keywords using the SDSS-standard formatting.

The code for jaeger is developed in [GitHub](https://github.com/sdss/jaeger) and can be installed using [sdss_install](https://github.com/sdss/sdss_install) or by running

```console
pip install --upgrade sdss-jaeger
```

To check out the development version do

```console
git clone https://github.com/sdss/jaeger.git
```

jaeger is developed as an [asyncio](https://docs.python.org/3/library/asyncio.html) library and a certain familiarity with asynchronous programming is required. The actor functionality (TCP/IP connection, command parser, inter-actor communication) is built on top of [CLU](https://github.com/sdss/clu).

## A simple jaeger program

```python
import asyncio
from jaeger import FPS, log

async def main():

    # Set logging level to DEBUG
    log.set_level(0)

    # Initialise the FPS instance.
    fps = FPS()
    await fps.initialise()

    # Print the status of positioner 4
    print(fps[4].status)

    # Send positioner 4 to alpha=90, beta=45
    await pos.goto(alpha=90, beta=45)

    # Cleanly finish all pending tasks and exit
    await fps.shutdown()

asyncio.run(main())
```
