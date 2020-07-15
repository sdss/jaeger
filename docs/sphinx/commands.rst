Command API
===========

Base classes
------------

.. automodule:: jaeger.commands.base

.. _command-list:

List of commands
----------------

.. autoclass:: jaeger.commands.CommandID
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.GetID
    :exclude-members: get_messages
    :undoc-members:

.. autoclass:: jaeger.commands.GetFirmwareVersion
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.GetStatus
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.InitialiseDatums
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.GotoAbsolutePosition
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.GotoRelativePosition
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.GetActualPosition
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SetActualPosition
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SetSpeed
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SetCurrent
    :exclude-members:
    :undoc-members:


.. _bootloader-commands:

Bootloader commands
-------------------

.. autoclass:: jaeger.commands.StartFirmwareUpgrade
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SendFirmwareData
    :exclude-members:
    :undoc-members:

.. autofunction:: jaeger.commands.load_firmware


.. _trajectory-commands:

Trajectory commands
-------------------

.. autoclass:: jaeger.commands.Trajectory

.. autofunction:: jaeger.commands.send_trajectory

.. autoclass:: jaeger.commands.SendNewTrajectory
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SendTrajectoryData
    :exclude-members: get_messages
    :undoc-members:

.. autoclass:: jaeger.commands.TrajectoryDataEnd
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.TrajectoryTransmissionAbort
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.StartTrajectory
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.StopTrajectory
    :exclude-members:
    :undoc-members:


.. _calibration-commands:

Calibration commands
--------------------

.. automodule:: jaeger.commands.calibration
    :member-order: bysource
