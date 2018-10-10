
.. _intro:

Introduction to jaeger
======================

`jaeger <http://pacificrim.wikia.com/wiki/Jaeger>`_ provides high level control for the SDSS-V `Focal Plane System <https://wiki.sdss.org/display/FPS>`__. Some of the things that jaeger does are:

- Wraps the low level CAN commands for simpler use.
- Provides a framework that is independent of the CAN interface used (by using the `python-can <https://github.com/hardbyte/python-can>`__ library).
- Interfaces with `kaiju <https://github.com/csayres/kaiju>`_ to provide anticollision path planning for trajectories.
- Implements status and position update loops.
- Provides implementations for commonly used tasks (e.g., go to position, send trajectory).
- Stores last known positions and provide a system to recover from unexpected errors.
- Implements the Field View Camera control and feedback loop.
- Provides a TCP/IP interface to send commands and output keywords using the SDSS- standard formatting.

The code for jaeger is developed in `GitHub <https://github.com/sdss/jaeger>`__ and can be installed using `sdss_install <https://github.com/sdss/sdss_install>`__ or by running ::

    pip install --upgrade sdss-jaeger

jaeger is developed as an `asyncio <https://docs.python.org/3/library/asyncio.html>`__ library and a certain familiarity with asynchronous programming. The actor functionality (TCP/IP connection, command parser, inter-actor communication) is built on top of `asyncioActor <https://github.com/albireox/asyncioActor>`__.


A simple jaeger program
-----------------------

.. code-block:: python

    import asyncio
    from jaeger import FPS, log

    async def main(loop):

        # Set logging level to DEBUG
        log.set_level(0)

        # Initialise the FPS instance.
        fps = FPS()
        await fps.initialise()

        # Initialise positioner 4
        pos = fps.positioners[4]
        await pos.initialise()

        # Send positioner 4 to alpha=90, beta=45
        await pos.goto(alpha=90, beta=45)

        # Cleanly finish all pending tasks and exit
        await fps.shutdown()

    asyncio.run(main())

This code runs the `coroutine <https://docs.python.org/3/library/asyncio-task.html#coroutines>`__ ``main`` until it completes. First we create an instance of `~jaeger.fps.FPS` , the main jaeger class that contains information about all the positioners and the `CAN bus <jaeger.can.JaegerCAN>`. When called without extra parameters, `~jaeger.fps.FPS` loads the default CAN interface and positioner layout. The real initialisation happens when calling `fps.initialise <jaeger.fps.FPS.initialise>`. Note that `~jaeger.fps.FPS.initialise` is a coroutine and needs to be awaited until completion. During initialisation, all the robots in the layout are queried by their status and firmware, and `~jaeger.positioner.Positioner` instances are added to `fps.positioners <jaeger.fps.FPS.positioners>`.

Next we initialise one of the positioners. This make sure that the positioner is not moving, initialises the datums if necessary, and starts a loop for polling status and position. It also sets the default motor speeds. Finally, we command the positioner to go to a certain position in alpha and beta. The `Positioner.goto <jaeger.positioner.Positioner.goto>` coroutine finishes once the move has been completed and the status reaches `~jaeger.utils.maskbits.PositionerStatus.DISPLACEMENT_COMPLETED`.

.. note:: At this time we do not autoinitialise the positioners because initialising datums in robots that do not know their position can result in large movements that, without path planning, could cause collisions. This will be improved and streamlined once the interface with kaiju_ has been implemented.


Using jaeger from IPython
-------------------------

Since its 7.0 version, IPython provides `experimental support for asyncio <https://blog.jupyter.org/ipython-7-0-async-repl-a35ce050f7f7>`__. This means that it is possible to run the statements within the ``main()`` function from the example above directly in IPython interactively. Note that the support for asyncio is still tentative and should not be use for production, but it is a useful feature for quick control of the positioners and debugging.
