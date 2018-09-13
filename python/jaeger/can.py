#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-12 09:14:18

import asyncio
import pprint

import can
import can.interfaces.slcan
import jaeger.tests.bus
from jaeger import config, log
from jaeger.commands import Message, StatusMixIn


__ALL__ = ['JaegerCAN', 'JaegerReaderCallback', 'VALID_INTERFACES']


#: Accepted CAN interfaces
VALID_INTERFACES = {'slcan': can.interfaces.slcan.slcanBus,
                    'test': jaeger.tests.bus.SlcanBusTester}


class JaegerReaderCallback(can.Listener):
    """A message reader that triggers a callback on message received.

    Parameters
    ----------
    callback : function
        The function to run when a new message is received.
    loop : event loop or `None`
        If an asyncio event loop, the callback will be called with
        `asyncio.call_soon`, otherwise it will be called immediately.

    """

    def __init__(self, callback, loop=None):

        self.callback = callback
        self.loop = loop

    def on_message_received(self, msg):
        """Calls the callback with the received message."""

        if self.loop:
            self.loop.call_soon(self.callback, msg)
        else:
            self.callback(msg)


class JaegerCAN(object):
    """Returns an expanded CAN interface.

    Returns a CAN bus instance subclassing from the appropriate `python-can`_
    interface (ultimately a subclass of `~can.BusABC` itself).

    Parameters
    ----------
    interface : str
        One of `~jaeger.can.VALID_INTERFACES`.
        Defines the type of interface to use and the class from
        `python-can <https://python-can.readthedocs.io/en/stable/>`_
        to import.
    args,kwargs
        Arguments and keyword arguments to pass to the interface when
        initialising it (e.g., the channel, baudrate, etc).

    Attributes
    ----------
    queue : `asyncio.Queue`
        Queue of messages to be sent to the bus.

    Returns
    -------
    bus : `.JaegerCAN`
        A bus class instance,

    """

    def __new__(cls, interface, *args, **kwargs):
        """Dynamically subclasses from the correct CAN interface."""

        assert interface in VALID_INTERFACES, f'invalid interface {interface!r}'

        log.debug(f'starting bus with interface {interface}, args={args!r}, kwargs={kwargs!r}')

        interface_class = VALID_INTERFACES[interface]

        jaeger_class = type('JaegerCAN', (interface_class, JaegerCAN), {})

        jaeger_instance = interface_class.__new__(jaeger_class)

        interface_class.__init__(jaeger_instance, *args, **kwargs)
        JaegerCAN.__init__(jaeger_instance, *args, **kwargs)

        return jaeger_instance

    def __init__(self, *args, loop=None, **kwargs):

        self.queue = asyncio.Queue(maxsize=10)
        self.running = []

        self.loop = loop if loop is not None else asyncio.get_event_loop()

        # self.listener = JaegerReaderCallback(self._process_reply, loop=self.loop)
        # self.notifiers = can.notifier.Notifier(self, [self.listener], loop=self.loop)
        # log.debug('started AsyncBufferedReader listener')

        self._queue_process_task = self.loop.create_task(self._process_queue())

    async def _process_queue(self):
        """Processes the queue of waiting commands."""

        while True:
            message = await self.queue.get()
            assert isinstance(message, Message)
            log.debug(f'retrieved message with id {message.command_id} from queue')

    def _process_reply(self, msg):
        pass

    @classmethod
    def from_profile(cls, profile=None):
        """Creates a new bus interface from a configuration profile.

        Parameters
        ----------
        profile : `str` or `None`
            The name of the profile that defines the bus interface, or `None`
            to use the default configuration.

        """

        if profile is None:
            profile = 'default'

        if profile not in config['interfaces']:
            raise ValueError(f'invalid interface profile {profile}')

        config_data = config['interfaces'][profile].copy()
        interface = config_data.pop('interface')

        return cls(interface, **config_data)

    @staticmethod
    def print_profiles():
        """Prints interface profiles and returns a list of profile names."""

        pprint.pprint(config['interfaces'])

        return config['interfaces'].keys()

    def send_command(self, command):
        """Sends multiple messages from a command and tracks status.

        Parameters
        ----------
        command : `~jaeger.commands.commands.Command`
            The command to send.

        """

        assert command.status == StatusMixIn.READY, f'command {command!s}: not ready'

        messages = command.get_messages()

        for message in messages:
            assert isinstance(message, Message), 'message is not an instance of Message'
            self.send(message)
