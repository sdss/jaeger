
.. _intro:

Introduction to jaeger
======================

`jaeger <http://pacificrim.wikia.com/wiki/Jaeger>`_ provides high level control for the SDSS-V `Focal Plane System <https://wiki.sdss.org/display/FPS>`__. Some of the features that jaeger provide are:

- Wraps the low level CAN commands for simpler use.
- Provides a framework that is independent of the CAN interface used.
- Interfaces with kaiju_ to provide anticollision path planning for trajectories.
- Implements status and position update loops.
- Provides implementations for commonly used tasks (e.g., go to position, send trajectory).
- Interfaces with the Instrument Electronics Box modbus PLC controller.
- Provides a TCP/IP interface to send commands and output keywords using the SDSS-standard formatting.

The code for jaeger is developed in `GitHub <https://github.com/sdss/jaeger>`__ and can be installed using `sdss_install <https://github.com/sdss/sdss_install>`__ or by running ::

    pip install --upgrade sdss-jaeger

To check out the development version do ::

    git clone git://github.com/sdss/jaeger.git

jaeger is developed as an `asyncio <https://docs.python.org/3/library/asyncio.html>`__ library and a certain familiarity with asynchronous programming is required. The actor functionality (TCP/IP connection, command parser, inter-actor communication) is built on top of `CLU <https://github.com/sdss/clu>`__.


.. _intro-simple:

A simple jaeger program
-----------------------

.. code-block:: python

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

This code runs the `coroutine <https://docs.python.org/3/library/asyncio-task.html#coroutines>`__ ``main()`` until it completes. First we create an instance of `~jaeger.fps.FPS` , the main jaeger class that contains information about all the positioners and the `CAN bus <jaeger.can.JaegerCAN>`. When called without extra parameters, `~jaeger.fps.FPS` loads the default CAN interface and positioner layout. We then call `~jaeger.fps.FPS.start` to open the CAN interface (this step can be skipped for some CAN interfaces. Otherwise it will be executed when calling `~jaeger.fps.FPS.initialise` but a warning will be issued). The real positioner initialisation happens when calling `fps.initialise <jaeger.fps.FPS.initialise>`. Note that `~jaeger.fps.FPS.initialise` is a coroutine and needs to be awaited until completion. During initialisation, all the robots in the layout are queried by their status and firmware, and `~jaeger.positioner.Positioner` instances are added to `fps <jaeger.fps.FPS>` (which is a dictionary of positioners).

Once the initialisation is complete we command the positioner to go to a certain position in alpha and beta. The `Positioner.goto <jaeger.positioner.Positioner.goto>` coroutine finishes once the move has been completed and the status reaches `~jaeger.maskbits.PositionerStatusV4_1.DISPLACEMENT_COMPLETED`.


Using jaeger from IPython
-------------------------

Since its 7.0 version, IPython provides `experimental support for asyncio <https://blog.jupyter.org/ipython-7-0-async-repl-a35ce050f7f7>`__. This means that it is possible to run the statements within the ``main()`` function from the example above directly in IPython interactively. Note that the support for asyncio is still tentative and should not be use for production, but it is a useful feature for quick control of the positioners and debugging.


Scheduling commands
-------------------

To schedule a command you must use the `.FPS.send_command` method, which returns a `.Command` instance. Note that the command does *not* get executed until `~.FPS.send_command` is awaited ::

    >>> fps = await FPS().start()
    # We don't need to initialise the FPS to send low-level commands.
    >>> cmd = fps.send_command('GO_TO_ABSOLUTE_POSITION', positioner_ids=4, alpha=100, beta=30)
    >>> cmd
    <Command GO_TO_ABSOLUTE_POSITION (positioner_ids=4, status='READY')>
    >>> await cmd

The replies to the command are stored in the `~.Command.replies` attribute. Here we send a broadcast command to get the status from all the connected positioners: ::

    >>> status_cmd = GetStatus(positioner_ids=0)
    >>> fps.send_command(status_cmd)
    >>> await status_cmd
    >>> reply = status_cmd.replies[0]
    >>> reply
    <Reply (command_id='GET_STATUS', positioner_id=4, response_code='COMMAND_ACCEPTED')>
    >>> reply.data
    bytearray(b"\'\xc0\x00\x01")
    >>> PositionerStatusV4_1(status_cmd.get_positioner_status())
    [<PositionerStatusV4_1.DATUM_INITIALIZED|BETA_DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|DISPLACEMENT_COMPLETED|DATUM_BETA_INITIALIZED|DATUM_ALPHA_INITIALIZED|SYSTEM_INITIALIZED: 666894337>]


Moving positioners and sending trajectories
-------------------------------------------

Moving positioners can be done either by using the `.Positioner.goto` method for a given positioner, or by sending a series of trajectories to multiple positioners with `.FPS.send_trajectory`.

To move positioner 8 to :math:`\alpha=85,\,\beta=30` at a speed of 1500 RPM, you can do ::

    >>> await fps.initialise()
    >>> positioner = fps.positioners[8]
    >>> positioner
    <Positioner (id=8, status='DATUM_INITIALIZED|BETA_DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|DISPLACEMENT_COMPLETED|DATUM_BETA_INITIALIZED|DATUM_ALPHA_INITIALIZED|SYSTEM_INITIALIZED', initialised=False)>
    >>> await positioner.goto(alpha=85, beta=30, speed_alpha=1500, speed_beta=1500)

The command will asynchronously block until the position has been reached and the status is again `~jaeger.maskbits.PositionerStatusV4_1.DISPLACEMENT_COMPLETED`.

Trajectories can be sent either through a `YAML <http://yaml.org>`__ file or a dictionary. In both cases the trajectory must include, for each positioner, a list of positions and times for the ``'alpha'`` arm in the format :math:`\rm [(\alpha_1, t_1), (\alpha_2, t_2), ...]`, and a similar dictionary for ``'beta'``. An example of YAML file with a valid trajectory for positioners 1 and 4 is

.. code-block:: yaml

    1:
        alpha: [[20, 5], [100, 10], [50, 15]]
        beta: [[90, 15], [85, 18]]
    4:
        alpha: [[200, 3], [100, 15]]
        beta: [[50, 5]]

And it can be commanded by doing ::

    >>> await fps.send_trajectory('my_trajectory.yaml')

.. _kaiju: https://github.com/csayres/kaiju
.. _python-can: https://github.com/hardbyte/python-can
