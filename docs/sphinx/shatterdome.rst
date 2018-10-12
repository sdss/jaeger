
The shatterdome
===============

In the `shatterdome <http://pacificrim.wikia.com/wiki/Shatterdome>`__ we'll have a closer look at some of the internal mechanics of the jaeger.

.. _can-bus:

The `CAN bus <.JaegerCAN>`
--------------------------

The `.JaegerCAN` class provides the lowest level access to the positioners via the `CAN <https://en.wikipedia.org/wiki/CAN_bus>`__ bus. `.JaegerCAN` is simply a class factory that allows to subclass from the appropriate python-can_ `~can.BusABC` subclass, while also adding specific jaeger functionality. Normally `.JaegerCAN` is instantiated when `.FPS` is and you won't have to use it unless you want to access the bus directly.

`.JaegerCAN` can be instantiated by passing it an ``interface`` and the parameters necessary to instantiate the corresponding python-can_ bus. ``interface`` must be one of `~.can.VALID_INTERFACES`, which defines the correlation between interfaces and python-can buses. For instance, to create a `slcan <can.interfaces.slcan.slcanBus>` bus we do ::

    >>> bus = JaegerCAN('slcan', channel='/dev/tty.usbserial-LW1FJ8ZR', ttyBaudrate=1000000 bitrate=1000000)
    >>> isinstance(bus, slcanBus)
    True

Loading from a profile
^^^^^^^^^^^^^^^^^^^^^^

The `configuration file <config-files>`_ contains a section in which multiple bus interfaces can be defined. An example of bus interfaces is

.. code-block:: YAML

    interfaces:
        default:
            interface: slcan
            channel: /dev/tty.usbserial-LW1FJ8ZR
            ttyBaudrate: 1000000
            bitrate: 1000000
        test:
            interface: test
            channel: none
            ttyBaudrate: 1000000
            bitrate: 1000000

These configurations can be loaded by using the `.JaegerCAN.from_profile` classmethod ::

    >>> bus = JaegerCAN.from_profile('test')
    >>> bus
    <jaeger.can.JaegerCAN at 0x117594128>

The ``default`` interface can be loaded by calling `~.JaegerCAN.from_profile` without arguments.

.. _can-queue:

The command queue
^^^^^^^^^^^^^^^^^

Because we need to be able to associate replies from the bus with the command that triggered them, and given that commands and replies don't have unique identifiers beyond the command and positioner ids, we do not allow more than one instance the pair (`command_id <jaeger.commands.CommandID>`, positioner_id) to run at the same time. When a command is executed (ultimately by calling `.Command.send`), the command is put in a queue. When a new command is available in the queue, the code checks that no other command with the same ``command_id`` and ``positioner_id`` are `already running <.JaegerCAN.running_commands>`. If no identical command is running, all the messages from the command are sent to the bus and the command remains in `~.JaegerCAN.running_commands` until it has been completed (see the command-done_ section for more details). If a command is running, the new command is re-queued until the previous command has finished.

Broadcast commands are a bit special: when a broadcast command (``positioner_id=0``) is running no other command with the same ``command_id`` will run until the broadcast has finished, regardless of ``positioner_id``.


The `.FPS` class
----------------

The `.FPS` class is the main entry point to monitor and command the focal plane system and usually it will be the first thing you instantiate. It contains a `CAN bus <can-bus>`_, a `dictionary <.FPS.positioners>` of all the positioners included in the layout (a layout is a list of the positioners that compose the FPS, with their associated ``positioner_id`` and central position; it can be stored as a file or in a database) and high level methods to perform operations that affect multiple positioners (e.g., `send a trajectory <send-trajectory>`_).

To instantiate with the default options, simply do ::

    >>> from jaeger import FPS
    >>> fps = FPS()

This will create a new CAN bus (accessible as `.FPS.bus`) using the ``default`` interface profile and will use the default layout stored in the configuration file under ``config['fps']['default_layout']`` to add instances of `.Positioner` to `.FPS.positioners`.

Initialisation
^^^^^^^^^^^^^^

Once we have created a `.FPS` object we'll need to initialise it by calling and awaiting `.FPS.initialise`. This will issue two broadcast commands: `~.commands.GetStatus` and `.commands.GetFirmwareVersion`. The replies to these commands are used to determine which positioners are connected and sets their status.

.. important:: At this time running `.FPS.initialise` does not ensure that each one of the positioners will have their datums initialised. This is because initialising datums will move the positioners which, without path planning, could induce collisions. Instead, each positioner needs to be manually initialised with `.Positioner.initialise`. See the `positioner initialisation <positioner-initialise>`_ section for more details.

Sending commands
^^^^^^^^^^^^^^^^

The preferred way to send a command to the bus is by using the `.FPS.send_command` method which accepts a `.commands.CommandID` (either as a flag, integer, or string), the ``positioner_id`` that must listen to the command, and additional arguments to be passed to the command associated with the `~.commands.CommandID`. For example, to broadcast a `~.commands.CommandID.GET_ID` command ::

    >>> await fps.send_command('GET_ID', positioner_id=0)

Note that you need to ``await`` the command, which will return the execution to the event loop until the `command has finished <command-done>`_.

Some commands, such as `~.commands.SetActualPosition` take multiple attributes ::

    >>> cmd = await fps.send_command(CommandID.SET_ACTUAL_POSITION, positioner_id=4, alpha=10, beta=100)
    >>> cmd
    <Command SET_ACTUAL_POSITION (positioner_id=4, status='DONE')>

When a command is send `.FPS` puts it in the `bus command queue <can-queue>`_ and, once it gets processed, starts listening for replies from the bus. When it gets a reply with the same ``command_id`` and ``positioner_id`` the bus sends it to the command for further processing.

Shutting down the FPS
^^^^^^^^^^^^^^^^^^^^^

`Positioner pollers <positioner-pollers>`_ and queue watchers are built as `Tasks <asyncio.Task>` that run forever. If you are executing your code with `asyncio.run <https://docs.python.org/3/library/asyncio-task.html#asyncio.run>`__ or `~asyncio.AbstractEventLoop.run_until_complete`, your funcion will never finish and you'll need to cancel the execution. To cancel all pending tasks and close the `.FPS` object cleanly, run ::

    await fps.shutdown()

.. _send-trajectory:

Sending trajectories
^^^^^^^^^^^^^^^^^^^^

Trajectories can be sent either a `YAML <http://yaml.org>`__ file or a dictionary. In both cases the trajectory must include, for each positioner, a list of positions and times for the ``'alpha'`` arm in the format :math:`\rm [(\alpha_1, t_1), (\alpha_2, t_2), ...]`, and a similar dictionary for ``'beta'``. An example of YAML file with a valid trajectory for positioners 1 and 4 is

.. code-block:: yaml

    1:
        alpha: [[20, 5], [100, 10], [50, 15]]
        beta: [[90, 15], [85, 18]]
    4:
        alpha: [[200, 3], [100, 15]]
        beta: [[50, 5]]

And it can be commanded by doing ::

    >>> await fps.send_trajectory('my_trajectory.yaml')

Unless `~.FPS.send_trajectory` is called with ``kaiju_check=False`` (DANGER! Do not do that unless you are sure of what you are doing), jaeger will check with kaiju_ to confirm that the trajectory is safe to execute.

.. warning:: The kaiju check feature is not yet available and all trajectories are currently sent without any anti-collision check.

Aborting all trajectories
^^^^^^^^^^^^^^^^^^^^^^^^^

Trajectories or `go to <positioner-goto>`_ commands can be cancelled for all positioners by using the `.FPS.abort` method ::

    >>> await fps.send_trajectory('my_trajectory.yaml')
    >>> await fps.abort()  # Cancel the trajectory

Note that the `~.FPS.abort` method creates and returns a `~asyncio.Task` and will be executed even without it being awaited, as long as there is a running event loop. However, it is safer to await the returned task.


`.Positioner`, status, and position
-----------------------------------

The `.Positioner` class stores information about a single positioner, its `status <.maskbits.PositionerStatus>` and position, and provides high level methods to command the positioner. `.Positioner` objects need to be linked to a `.FPS` instance and are usually created when the `.FPS` class is instantiated.

.. _positioner-initialise:

Initialisation
^^^^^^^^^^^^^^

When a `.Positioner` is instantiated it contains no information about its position (angle of the alpha and beta arms) and its status is set to `~.maskbits.PositionerStatus.UNKNOWN`. By calling and awaiting `.Positioner.initialise`, the following steps are executed:

- The status is updated by calling `.Positioner.update_status`.
- If the `~.maskbits.PositionerStatus.DATUM_INITIALIZED` flag is not in the status, issues a `~.commands.InitialiseDatums` command and waits until it completes and the bit has been set. This will move the positioner to its home position.
- If the `~.maskbits.PositionerStatus.DISPLACEMENT_COMPLETED` bit is not found, it issues `~.commands.StopTrajectory` and waits until the positioner has stopped and the bit is set.
- Starts the `position and status pollers <position-pollers>`_.
- Sets the alpha and beta arm speeds to the default value (stored in the configuration file as ``motor_speed``).

After this sequence, the positioner is ready to be commanded.

.. _positioner-pollers:

Position and status pollers
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The status of the positioner, given as a `maskbit <maskbits>`_ `~.maskbits.PositionerStatus` (or `.maskbits.BootloaderStatus` if the positioner is in `bootloader <bootloader-mode>`_ mode) can be accessed via the ``status`` attribute and updated by calling the `~.Positioner.update_status` coroutine. Similarly, the current position of the positioner is stored in the ``alpha`` and ``beta`` attributes, in degrees, and updated via `~.Positioner.update_position`.

As we initialise the positioner, two `~.utils.helpers.Poller` instances are created: `~.Positioner.status_poller` and `~.Positioner.position_poller`. These tasks simply call `~.Positioner.update_status`. and `~.Positioner.update_position` every second and update the corresponding attribute. The delay between polls can be set via the `~.utils.helpers.Poller.set_delay` method.

.. _positioner-goto:

Sending a positioner to a position
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The `.Positioner.goto` coroutine allows to easily send the positioner to a position or set the speed of either arm ::

    await positioner.goto(alpha=30, beta=90, alpha_speed=1000, beta_speed=1200)

    # Only set speed
    await positioner.goto(alpha_speed=500, beta_speed=500)

    # Only go to position using the speed we just set
    await positioner.goto(alpha=100, beta=154)

Awaiting `.Positioner.goto` blocks until the positioner has arrived to the desired position and `~.maskbits.PositionerStatus.DISPLACEMENT_COMPLETED` is set.

Waiting for a status
^^^^^^^^^^^^^^^^^^^^

In many cases it's convenient to asynchronously block the execution of a coroutine while we wait until certain bits appear in the status. To do that one can use `~.Positioner.wait_for_status` ::

    # Wait until DISPLACEMENT_COMPLETED appears
    await positioner.wait_for_status(PositionerStatus.DISPLACEMENT_COMPLETED)

    # Wait untils SYSTEM_INITIALIZATION and DATUM_INITIALISED are set. Time-out in 3 seconds if that doesn't happen.
    await positioner.wait_for_status([PositionerStatus.SYSTEM_INITIALIZATION, PositionerStatus.DATUM_INITIALISED], timeout=3)

While `~.Positioner.wait_for_status` is running the interval at which `~.Positioner.status_poller` updates the status is increased (to 0.1 seconds by default, but this can be set when calling the coroutine) and the default value is restored when the status is reached or the time-out happens.


Commands
--------

Creating a new command class
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Replies
^^^^^^^

.. _command-done:

When is a command marked done?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Time-outs
^^^^^^^^^

.. _config-files:

Configuration files
-------------------


Logging
-------


.. _bootloader-mode:

The bootloader mode
-------------------

Upgrading firmware
^^^^^^^^^^^^^^^^^^


.. _kaiju: https://github.com/csayres/kaiju
.. _python-can: https://github.com/hardbyte/python-can
