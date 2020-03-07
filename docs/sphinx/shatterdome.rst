
The shatterdome
===============

In the `shatterdome <http://pacificrim.wikia.com/wiki/Shatterdome>`__ we'll have a closer look at some of the internal mechanics of the jaeger.

.. _can-bus:

The `CAN bus <.JaegerCAN>`
--------------------------

The `.JaegerCAN` class provides the lowest level access to the positioners via the `CAN <https://en.wikipedia.org/wiki/CAN_bus>`__ bus. `.JaegerCAN` provides access to the appropriate python-can_ `~can.BusABC` subclass, while also adding including jaeger functionality. Normally `.JaegerCAN` is instantiated when `.FPS` is and you won't have to use it unless you want to access the bus directly.

`.JaegerCAN` can be instantiated by passing it an ``interface`` and the parameters necessary to instantiate the corresponding python-can_ bus. ``interface`` must be one of `~.can.INTERFACES`, which defines the correlation between interfaces and python-can buses. For instance, to create a `slcan <can.interfaces.slcan.slcanBus>` bus we do ::

    >>> can = JaegerCAN('slcan', channel='/dev/tty.usbserial-LW1FJ8ZR', ttyBaudrate=1000000 bitrate=1000000)
    >>> isinstance(bus.interfaces[0], slcanBus)
    True

Loading from a profile
^^^^^^^^^^^^^^^^^^^^^^

The `configuration file <config-files>`_ contains a section in which multiple bus interfaces can be defined. An example of bus profile is

.. code-block:: YAML

    profiles:
        default: cannet
        cannet:
            interface: cannet
            channels: [192.168.0.10]
            port: 19228
            buses: [1, 2, 3, 4]
            bitrate: 1000000
        slcan:
            interface: slcan
            channel: /dev/tty.usbserial-LW3HTDSY
            ttyBaudrate: 1000000
            bitrate: 1000000

These configurations can be loaded by using the `.JaegerCAN.from_profile` classmethod ::

    >>> bus = JaegerCAN.from_profile('test')
    >>> bus
    <jaeger.can.JaegerCAN at 0x117594128>

The ``default`` profile can be loaded by calling `~.JaegerCAN.from_profile` without arguments. Note that in the case of ``cannet`` we define multiple interfaces as a list of ``channels`` instead of as a single ``channel``. We'll talk about multiple interfaces in :ref:`multibus`.

.. _can-queue:

The command queue
^^^^^^^^^^^^^^^^^

Because we need to be able to associate replies from the bus with the command that triggered them, and given that commands and replies don't have unique identifiers beyond the command and positioner ids, we do not allow more than one instance the pair (`command_id <jaeger.commands.CommandID>`, positioner_id) to run at the same time. When a command is executed (ultimately by calling `.FPS.send_command`), the command is put in a queue. When a new command is available in the queue, the code checks that no other command with the same ``command_id`` and ``positioner_id`` are `already running <.JaegerCAN.running_commands>`. If no identical command is running, all the messages from the command are sent to the bus and the command remains in `~.JaegerCAN.running_commands` until it has been completed (see the command-done_ section for more details). If a command is running, the new command is re-queued until the previous command has finished.

Broadcast commands are a bit special: when a broadcast command (``positioner_id=0``) is running no other command with the same ``command_id`` will run until the broadcast has finished, regardless of ``positioner_id``.

.. _multibus:

Multibus interfaces
^^^^^^^^^^^^^^^^^^^

Some CAN devices provide multiple buses (for example, the `Ixxat CAN\@net device <.CANNetBus>`). In addition, the positioners in the FPS may not form a single CAN network since they can be connected to different buses in different devices. `.JaegerCAN` provides support for multichannel and multibus CAN networks. Because these terms can sometimes be confusing, we assume the following nomenclature:

- A CAN device is called an *interface*, which may consist of one or multiple *buses*.
- Each interface is defined by its *channel* or route to it. The channel can be a TCP address, a device path, etc. Some interfaces require more parameters to define the connection method (for example, a TCP port). Sometimes we use channel and interface as synonyms.
- A *bus* is the minimal CAN network unit. All positioners connected to the same bus belong to the same CAN network.

In jaeger, the `.JaegerCAN` instance represents the entirety of the CAN network, even when it's composed of multiple interfaces with several buses each. The attribute `~.JaegerCAN.interfaces` contains a list of all the loaded interfaces. At this point, jaeger does not support mixing interfaces of different types.

Whether an interface is multibus or not is defined in `.INTERFACES`. The buses to be used can be defined to `.JaegerCAN` via the ``buses`` argument. An example of a multibus, python-can_ interface is `.CANNetBus`.

The mapping between positioners and buses is done in the :ref:`FPS class <fps>`. When `.FPS` is instantiated using multibus interfaces (or multiple single bus interfaces), a `.GET_ID` command is broadcast to all the available interfaces and buses. The replies from the positioners are used to create a `.positioner_to_bus` mapping. Because we need to know from what interface and bus the messages originate from, it is assumed that a multibus interface appends ``interface`` and ``bus`` attributes to the returned messages.


.. _fps:

The `.FPS` class
----------------

The `.FPS` class is the main entry point to monitor and command the focal plane system and usually it will be the first thing you instantiate. It contains a `CAN bus <can-bus>`_, a `dictionary <.BaseFPS.positioners>` of all the positioners included in the layout (a layout is a list of the positioners that compose the FPS, with their associated ``positioner_id`` and central position; it can be stored as a file or in a database), and high level methods to perform operations that affect multiple positioners (e.g., `send a trajectory <send-trajectory>`_).

To instantiate with the default options, simply do ::

    >>> from jaeger import FPS
    >>> fps = FPS()

This will create a new CAN bus (accessible as `.FPS.bus`) using the ``default`` profile and will use the default layout stored in the configuration file under ``config['fps']['default_layout']`` to add instances of `.Positioner` to `.BaseFPS.positioners`.

Initialisation
^^^^^^^^^^^^^^

Once we have created a `.FPS` object we'll need to initialise it by calling and awaiting `.FPS.initialise`. This will issue two broadcast commands: `~.commands.GetStatus` and `~.commands.GetFirmwareVersion`. The replies to these commands are used to determine which positioners are connected and sets their status. Each one of the positioners that have replied are subsequently initialised as detailed in :ref:`positioner-initialise`.

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

FPS as a context manager
^^^^^^^^^^^^^^^^^^^^^^^^

It's possible to use the `.FPS` object as an async context manager. The `.FPS` is initialised when entering the context and shut down on exit ::

    fps = FPS()
    async with fps:
        await fps[13].goto(10, 10)

.. _send-trajectory:

Sending trajectories
^^^^^^^^^^^^^^^^^^^^

Trajectories can be sent either a `YAML <http://yaml.org>`_ file or a dictionary. In both cases the trajectory must include, for each positioner, a list of positions and times for the ``'alpha'`` arm in the format :math:`\rm [(\alpha_1, t_1), (\alpha_2, t_2), ...]`, and a similar dictionary for ``'beta'``. An example of YAML file with a valid trajectory for positioners 1 and 4 is

.. code-block:: yaml

    1:
        alpha: [[20, 5], [100, 10], [50, 15]]
        beta: [[90, 15], [85, 18]]
    4:
        alpha: [[200, 3], [100, 15]]
        beta: [[50, 5]]

And it can be commanded by doing ::

    >>> await fps.send_trajectory('my_trajectory.yaml')

Aborting all trajectories
^^^^^^^^^^^^^^^^^^^^^^^^^

Trajectories or `go to <positioner-goto>`_ commands can be cancelled for all positioners by using the `.FPS.abort` method ::

    >>> await fps.send_trajectory('my_trajectory.yaml')
    >>> await fps.abort()  # Cancel the trajectory

Note that the `~.FPS.abort` method creates and returns a `~asyncio.Task` and will be executed even without it being awaited, as long as there is a running event loop. However, it is safer to await the returned task.


`.Positioner`, status, and position
-----------------------------------

The `.Positioner` class stores information about a single positioner, its `status <.maskbits.PositionerStatusV4_1>` and position, and provides high level methods to command the positioner. `.Positioner` objects need to be linked to a `.FPS` instance and are usually created when the `.FPS` class is instantiated.

.. _positioner-initialise:

Initialisation
^^^^^^^^^^^^^^

When a `.Positioner` is instantiated it contains no information about its position (angle of the alpha and beta arms) and its status is set to `~.maskbits.PositionerStatusV4_1.UNKNOWN`. By calling and awaiting `.Positioner.initialise`, the following steps are executed:

- Updates the firmware version.
- The status is updated by calling `.Positioner.update_status`.
- Stops all possible trajectories remaining in the buffer for that positioner.
- Sets the alpha and beta arm speeds to the default value (stored in the configuration file as ``motor_speed``).

After this sequence, the positioner is ready to be commanded.

.. _positioner-pollers:

Position and status pollers
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The status of the positioner, given as a `maskbit <maskbits>`_ `~.maskbits.PositionerStatusV4_1` (or `.maskbits.BootloaderStatus` if the positioner is in `bootloader <bootloader-mode>`_ mode) can be accessed via the ``status`` attribute and updated by calling the `~.Positioner.update_status` coroutine. Similarly, the current position of the positioner is stored in the ``alpha`` and ``beta`` attributes, in degrees, and updated via `~.Positioner.update_position`.

As we initialise the FPS, two `~.utils.helpers.Poller` instances are created as part of the `.PollerList` `.FPS.pollers` to track the position and status of each positioner. These tasks simply call `~.FPS.update_status`. and `~.FPS.update_position` every few seconds and update the corresponding attributes in the positioners. The delay between polls can be set via the `~.utils.helpers.Poller.set_delay` method.

.. _positioner-goto:

Sending a positioner to a position
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The `.Positioner.goto` coroutine allows to easily send the positioner to a position or set the speed of either arm ::

    await positioner.goto(alpha=30, beta=90, speed=(1000, 1200))

    # Only set speed
    await positioner.set_speed(500, 500)

    # Only go to position using the speed we just set
    await positioner.goto(alpha=100, beta=154)

Awaiting `.Positioner.goto` blocks until the positioner has arrived to the desired position and `~.maskbits.PositionerStatusV4_1.DISPLACEMENT_COMPLETED` is set.

Waiting for a status
^^^^^^^^^^^^^^^^^^^^

In many cases it's convenient to asynchronously block the execution of a coroutine while we wait until certain bits appear in the status. To do that one can use `~.Positioner.wait_for_status` ::

    # Wait until DISPLACEMENT_COMPLETED appears
    await positioner.wait_for_status(PositionerStatusV4_1.DISPLACEMENT_COMPLETED)

    # Wait untils SYSTEM_INITIALIZED and DATUM_ALPHA_INITIALIZED are set. Time-out in 3 seconds if that doesn't happen.
    await positioner.wait_for_status([PositionerStatusV4_1.SYSTEM_INITIALIZED, PositionerStatusV4_1.DATUM_ALPHA_INITIALIZED], timeout=3)

Note that `~.Positioner.wait_for_status` is independent of the status poller. While `~.Positioner.wait_for_status` is running, a `.GET_STATUS` command will be issue wach ``delay`` seconds, in addition to the normal polling.

Commands
--------

`.Command` provides a base class to implement wrappers around firmware commands. It handles the creation of messages to be passed to the bus, encodes the ``arbitration id`` from the ``command_id` and ``positioner_id``, processes replies, and keeps a record of the status of a command. Commands that accept extra data (e.g., positions of the alpha and beta arms) also do the encoding of the input parameters to the format that the firmware command understands, making them easier to use. Commands are `asyncio.Future` objects and can be awaited until complete. A list of all the available commands can be found `here <command-list>`_.

Commands can sent directly to the FPS ::

    >>> from jaeger.commands import GetStatus
    >>> status_cmd = GetStatus(positioner_id=4)
    >>> status_cmd
    <Command GET_STATUS (positioner_id=4, status='READY')>
    >>> fps.send_command(status_cmd)
    True
    >>> await status_cmd

This is what happens when you execute the above snippet:

- When created, the command has status `~.maskbits.CommandStatus.READY` and is prepared to be sent to the bus.
- When we `~.FPS.send_command` the command, it gets put in the `bus queue <can-queue>`_.
- Shortly after, the bus processes the command from the queue and checks that no other command with the same ``(command_id, positioner_id)`` is running. If that's the case the command status is changed to `~.maskbits.CommandStatus.RUNNING` and all the `~.commands.base.Message` that compose the command are sent to the bus. A `~.commands.base.Message` is just a wrapper that contains the ``arbitration_id`` and the data to send as bytes. Most command will issue just a message but some such as `~.commands.SendTrajectoryData` can send multiple messages.
- The bus listens to replies from the bus and redirects them to the command with the matching ``(command_id, positioner_id)`` where they are processed.
- Once the expected replies have been received, or when the command times out, the command is marked `~.maskbits.CommandStatus.DONE` or `~.maskbits.CommandStatus.FAILED`. See the :ref:`command-done` section for more details.
- When the command is marked done, the ``result`` of the `~asyncio.Future` is set and the event loop returns.

Replies
^^^^^^^

When a reply is received from the bus it is redirected to appropriate command, processed, and stored in the `~.commands.base.Command.replies` list as a `~.commands.base.Reply` object. `~.commands.base.Reply` instances are quite simple and contain the associated ``positioner_id`` and ``command_id`` as well as the `~.commands.base.Reply.data` returned (as a `bytearray`), and the `~.commands.base.Reply.response_code` (and instance of `~.maskbits.ResponseCode`) for the command sent.

.. _command-done:

When is a command marked done?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

There are several ways in which a command can be marked done:

- If the command is not a broadcast and it has received *as many replies as messages sent* and all those replies have the `~.maskbits.ResponseCode.COMMAND_ACCEPTED` bit, then the command is marked `~.maskbits.CommandStatus.DONE`. This happens because we expect each message sent to receive a confirmation that it has been accepted, even if the reply doesn't include any additional data.
- If any reply to the command has a `~.maskbits.ResponseCode` different from `~.maskbits.ResponseCode.COMMAND_ACCEPTED` then the command is immediately marked `~.maskbits.CommandStatus.FAILED` and all additional replies are ignored.
- If the command is a broadcast we don't know how many replies to expect. In that case the command waits until it :ref:`times out <command-timeout>` and it's marked `~.maskbits.CommandStatus.DONE` if it has received at least one reply, otherwise `~.maskbits.CommandStatus.FAILED`.
- If the command is instantiated with ``timeout=0``, the command is marked done the moment it is processed by the :ref:`bus queue <can-queue>`. In this case all replies to the command are ignored.

.. _command-timeout:

Time-outs
^^^^^^^^^

When the command is set to `~.maskbits.CommandStatus.RUNNING` (i.e., when it is processed from the :ref:`bus queue <can-queue>`), a timer starts that times out the command after a certain delay (usually one second). The timeout can be set when the command is instantiated. When the command times out it is marked done (if is has not already been so) according to the :ref:`above logic <command-done>`.

The ``timeout`` can be set to `None`, in which case the command will never time out. When combined with a broadcast this means the command will never be marked finished and the user will need to manually call `~.commands.base.Command.finish_command` to finish it. For example ::

    import asyncio

    from jaeger import FPS
    from jaeger.maskbits import CommandStatus, PositionerStatusV4_1


    async def check_status(status_cmd, positioners):

        print('Starting monitoring')

        if all(asyncio.gather(*[positioner.wait_for_status(PositionerStatusV4_1.DATUM_ALPHA_INITIALIZED) for positioner in positioners])):
            status_cmd.finish_command(status=CommandStatus.DONE)
        else:
            status_cmd.finish_command(status=CommandStatus.FAILED)


    async def get_status():

        fps = FPS()
        await fps.initialise()

        status_cmd = fps.send_command('GET_STATUS', positioner_id=0, timeout=None)

        asyncio.create_task(check_status(status_cmd, fps.positioners))

        await status_cmd

        print('Command done')


    asyncio.run(get_status())


Internals
---------

.. _config-files:

Configuration files
^^^^^^^^^^^^^^^^^^^

jaeger uses the default configuration file system from the `SDSS Python template <https://sdss-python-template.readthedocs.io/en/latest/#configuration-file-and-logging>`__. The main configuration file, in YAML_ format, is included with the package in `etc/jaeger.yml <https://github.com/sdss/jaeger/blob/master/python/jaeger/etc/jaeger.yml>`__. Any section in this file can be overridden in a personal configuration file that must be located at ``~/.jaeger.jaeger.yml`` in the HOME directory of the user executing the code. For example, if the default ``interfaces`` section is

.. code-block:: YAML

    profiles:
        default: slcan
        slcan:
            interface: slcan
            channel: /dev/tty.usbserial-LW1FJ8ZR
            ttyBaudrate: 1000000
            bitrate: 1000000
        test:
            interface: test
            channel: none
            ttyBaudrate: 1000000
            bitrate: 1000000

But we want to change the channel of the default configuration we can create a file that contains

.. code-block:: YAML

    interfaces:
        default:
            channel: /dev/tty.USB0

Logging
^^^^^^^

There are two loggers in jaeger. Both of them are output to the terminal (with different logging levels) and stored in files. The first one logs all jaeger specific messages and it is stored at ``~/.jaeger/jaeger.log``. The second logs interaction with the CAN bus and saves messages to ``~/.jaeger/can.log``. In both cases, all messages with logging level ``INFO`` or above are output to the terminal. The logger instances can be access from the top jaeger module by importing ``from jaeger import log, can_log``.

To change the terminal logging level you can use the `~logging.Handler.setLevel` method. For instance ::

    import logging
    from jaeger import log

    # log.sh contains the terminal logging handler
    log.sh.setLevel(logging.DEBUG)


.. _bootloader-mode:

The bootloader mode
-------------------

During the first 10 seconds after a positioner has been powered up it remains in bootloader mode. In this state is is possible to issue several :ref:`specific commands <bootloader-commands>` to update the firmware. In this mode the `~.commands.GetStatus` command returns bits that must be interpreted using the `~.maskbits.BootloaderStatus` maskbit.

Is is possible to know whether a positioner is in bootloader mode by `getting the firmware version <.commands.GetFirmwareVersion>` command and getting the version string. If the version is ``'XX.80.YY'`` the positioner is in bootloader mode.

.. note:: This implementation is temporary and will be changed once the bootloaded mode can be set via de sync cable.

Upgrading firmware
^^^^^^^^^^^^^^^^^^

If is possible to upgrade the firmware of a positioner (or set of them) by using the convenience function `~.commands.load_firmware`. A :ref:`CLI interface <cli>` to this function is available via the ``jaeger`` command.


.. _kaiju: https://github.com/csayres/kaiju
.. _python-can: https://github.com/hardbyte/python-can
