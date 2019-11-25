.. _maskbits:

Maskbits
========

All statuses in jaeger are expressed as classes that subclass from `enum.IntFlag`. This allows them to behave as a series of values that can be combined with bitwise operations while also containing a representation of the name associated with a bit. `~enum.IntFlag` objects are ultimately just integers and can be used as such ::

    >>> dat_init = PositionerStatusV4_1.DATUM_INITIALIZED
    >>> dat_init
    <PositionerStatusV4_1.DATUM_INITIALIZED: 536870912>
    >>> dat_init.name
    'DATUM_INITIALIZED'
    >>> int(dat_init)
    536870912
    >>> isinstance(dat_init, int)
    True

To create a new enumeration from an integer ::

    >>> PositionerStatusV4_1(117440512)
    <PositionerStatusV4_1.BETA_DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|DISPLACEMENT_COMPLETED: 117440512>

Enumerations can be combined using bitwise operations ::

    >>> status = PositionerStatusV4_1.DATUM_INITIALIZED | PositionerStatusV4_1.SYSTEM_INITIALIZED
    <PositionerStatusV4_1.DATUM_INITIALIZED|SYSTEM_INITIALIZED: 536870913>
    >>> status.active_bits
    [<PositionerStatusV4_1.SYSTEM_INITIALIZED: 1>,
     <PositionerStatusV4_1.DATUM_INITIALIZED: 536870912>]
    >>> status & PositionerStatusV4_1.ALPHA_DISPLACEMENT_COMPLETED
    <PositionerStatusV4_1.0: 0>


Maskbits API
------------

.. automodule:: jaeger.maskbits
    :undoc-members:
    :member-order: bysource
