.. _maskbits:

Maskbits
========

All statuses in jaeger are expressed as classes that subclass from `enum.IntFlag`. This allows them to behave as a series of values that can be combined with bitwise operations while also containing a representation of the name associated with a bit. `~enum.IntFlag` objects are ultimately just integers and can be used as such ::

    >>> dat_init = PositionerStatus.DATUM_INITIALIZED
    >>> dat_init
    <PositionerStatus.DATUM_INITIALIZED: 536870912>
    >>> dat_init.name
    'DATUM_INITIALIZED'
    >>> int(dat_init)
    536870912
    >>> isinstance(dat_init, int)
    True

To create a new enumeration from an integer ::

    >>> PositionerStatus(117440512)
    <PositionerStatus.BETA_DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|DISPLACEMENT_COMPLETED: 117440512>

Enumerations can be combined using bitwise operations ::

    >>> status = PositionerStatus.DATUM_INITIALIZED | PositionerStatus.SYSTEM_INITIALIZATION
    <PositionerStatus.DATUM_INITIALIZED|SYSTEM_INITIALIZATION: 536870913>
    >>> status.active_bits
    [<PositionerStatus.SYSTEM_INITIALIZATION: 1>,
     <PositionerStatus.DATUM_INITIALIZED: 536870912>]
    >>> status & PositionerStatus.ALPHA_DISPLACEMENT_COMPLETED
    <PositionerStatus.0: 0>


Maskbits API
------------

.. automodule:: jaeger.maskbits
    :undoc-members:
    :member-order: bysource
