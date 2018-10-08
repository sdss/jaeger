Commands
========

Base classes
------------

.. automodule:: jaeger.commands.base
    :exclude-members: Abort


List of commands
----------------

.. autoclass:: jaeger.commands.CommandID
    :exclude-members:
    :undoc-members:
    :noindex:

.. autoclass:: jaeger.commands.Abort
    :exclude-members: get_messages
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

.. autoclass:: jaeger.commands.StartTrajectory
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

.. autoclass:: jaeger.commands.SetSpeed
    :exclude-members:
    :undoc-members:


Bootloader commands
-------------------

.. autoclass:: jaeger.commands.StartFirmwareUpgrade
    :exclude-members:
    :undoc-members:

.. autoclass:: jaeger.commands.SendFirmwareData
    :exclude-members:
    :undoc-members:
