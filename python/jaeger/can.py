#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-08-30 09:09:51

from jaeger import config, log
from jaeger.commands import Message, StatusMixIn
from jaeger.core import exceptions


#: Accepted CAN interfaces
VALID_INTERFACES = ['slcanBus']


def CAN(interface, autoinit=True, *args, **kwargs):
    """Initialises a CAN interface.

    Returns a CAN bus instance using the appropriate class for the input
    ``interface``. The returned instance is also a subclass of `.BaseCAN`.

    Parameters
    ----------
    interface : str
        One of `~jaeger.can.VALID_INTERFACES`.
        Defines the type of interface to use and the class from
        `python-can <https://python-can.readthedocs.io/en/stable/>`_
        to import.
    autoinit : bool
        Whether to call `.BaseCAN.initialise` after instantiating the bus.
    args,kwargs
        Arguments and keyword arguments to pass to the interface when
        initialising it (e.g., the channel, baudrate, etc).

    Returns
    -------
    bus
        A bus class instance, subclassing from the appropriate `python-can`_
        interface (ultimately a subclass of `~can.BusABC` itself) and
        `.BaseCAN`.

    """

    if interface == 'slcanBus':
        log.debug(f'using interface {interface}')
        from can.interface.slcan import slcanBus
        interface = slcanBus

    bus_class = type('CAN', (interface, BaseCAN), {})
    bus_instance = bus_class(*args, **kwargs)
    log.debug('created bus instance {id(bus_instance)}')

    if autoinit:
        bus_instance.initialise()

    return bus_instance


class BaseCAN(object):
    """Expands `can.bus.BusABC`."""

    def initialise(self, baudrate=None):
        """Prepares the device to receive commands.

        .. warning::
            This method will need to be modified to support interfaces other
            than :ref:`slcanBus <can:slcan>`.

        """

        my_id = id(self)

        # Clear buffer
        self.serialPortOrig.read_all()

        # Close the device in preparation for sending commands.
        self.write('C')
        log.debug(f'Bus {my_id}: closing device')

        reply = self.serialPortOrig.read_all()
        if reply != '\r':
            log.debug(f'Bus {my_id}: failed to close device. Device was probably closed.')

        # Sends the baudrate
        assert isinstance(baudrate, str) or baudrate is None, 'invalid baudrate'

        if baudrate is None:
            baudrate = config['CAN']['default']['baudrate_code']

        self.write(baudrate)
        log.debug(f'Bus {my_id}: setting baudrate {baudrate!r}')

        reply = self.serialPortOrig.read_all()
        if reply != '\r':
            raise exceptions.JaegerCANError(f'Bus {my_id}: failed to set baudrate {baudrate!r}.',
                                            serial_reply=reply)

        # Open the device
        self.write('O')
        log.debug(f'Bus {my_id}: opening device')

        reply = self.serialPortOrig.read_all()
        if reply != '\r':
            raise exceptions.JaegerCANError(f'Bus {my_id}: failed to open device.',
                                            serial_reply=reply)

    def send_command(self, command):
        """Sends multiple messages from a command and tracks status.

        Parameters
        ----------
        command : `~jaeger.commands.base.Command`
            The command to send.

        """

        cid = command.command_id

        assert command.status == StatusMixIn.READY, f'command {cid}: not ready'

        messages = command.get_messages()

        for message in messages:
            assert isinstance(message, Message), 'message is not an instance of Message'
            self.send(message)
