
The shatterdome
===============

In the `shatterdome <http://pacificrim.wikia.com/wiki/Shatterdome>`__ we'll have a closer look at some of the internal mechanics of the jaeger.

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

The command queue
^^^^^^^^^^^^^^^^^

Because we need to be able to associate replies from the bus with the command that triggered them, and given that commands and replies don't have unique identifiers beyond the command and positioner ids, we do not allow more than one instance the pair (`command_id <jaeger.commands.CommandID>`, positioner_id) to run at the same time. When a command is executed (ultimately by calling `.Command.send`), the command is put in a queue. When a new command is available in the queue, the code checks that no other command with the same ``command_id`` and ``positioner_id`` are `already running <.JaegerCAN.running_commands>`. If no identical command is running, all the messages from the command are sent to the bus and the command remains in `~.JaegerCAN.running_commands` until it has been completed (see the command-done_ section for more details). If a command is running, the new command is re-queued until the previous command has finished.

Broadcast commands are a bit special: when a broadcast command (``positioner_id=0``) is running no other command with the same ``command_id`` will run until the broadcast has finished, regardless of ``positioner_id``.


The `.FPS` class
----------------

Initialisation
^^^^^^^^^^^^^^

Sending commands
^^^^^^^^^^^^^^^^

Shutting down the FPS
^^^^^^^^^^^^^^^^^^^^^

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


Positioner, status, and position
--------------------------------

Initialisation
^^^^^^^^^^^^^^

Position and status pollers
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sending a positioner to a position
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Waiting for a status
^^^^^^^^^^^^^^^^^^^^


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


.. _kaiju: https://github.com/csayres/kaiju
.. _python-can: https://github.com/hardbyte/python-can
