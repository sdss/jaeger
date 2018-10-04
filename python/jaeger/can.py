#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-03 16:40:24

import asyncio
import collections
import pprint

import can
import can.interfaces.slcan

import jaeger
import jaeger.tests.bus
from jaeger import __IPYTHON__, config, log
from jaeger.commands import CommandID, Message
from jaeger.utils.maskbits import CommandStatus


__ALL__ = ['JaegerCAN', 'JaegerReaderCallback', 'VALID_INTERFACES']


#: Accepted CAN interfaces
VALID_INTERFACES = {'slcan': can.interfaces.slcan.slcanBus,
                    'test': jaeger.tests.bus.VirtualBusTester}


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
    command_queue : `asyncio.Queue`
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

        self.command_queue = asyncio.Queue(maxsize=10)

        #: Commands currently running ordered positioner_id (or zero for broadcast).
        self.running_commands = collections.defaultdict(dict)

        self.loop = loop if loop is not None else asyncio.get_event_loop()

        self.listener = JaegerReaderCallback(self._process_reply, loop=self.loop)
        self.notifiers = can.notifier.Notifier(self, [self.listener], loop=self.loop)
        log.debug('started JaegerReaderCallback listener')

        self._queue_process_task = self.loop.create_task(self._process_queue())

    async def _process_queue(self):
        """Processes the queue of waiting commands."""

        while True:
            message = await self.command_queue.get()
            assert isinstance(message, Message)
            log.debug(f'retrieved message {message.uuid} from queue and sent to CAN bus')
            self.send(message)

    def _process_reply(self, msg):
        """Processes replies from the bus."""

        positioner_id, command_id, __ = jaeger.utils.parse_identifier(msg.arbitration_id)

        command_id_flag = CommandID(command_id)

        log.debug(f'processing reply for command {command_id_flag.name}')

        r_cmd = self.is_command_running(positioner_id, command_id)
        if not r_cmd:
            return
            # raise RuntimeError(f'command {command_id_flag.name} is not running')

        r_cmd.reply_queue.put_nowait(msg)

    async def send_command(self, command, block=None):
        """Sends multiple messages from a command and tracks status.

        Parameters
        ----------
        command : `~jaeger.commands.commands.Command`
            The command to send.
        block : `bool`
            Whether to `await` for the command to be done before returning. If
            ``block=None``, will block only if the code is being run inside
            iPython.

        """

        log.debug(f'received command {command.command_id.name}')

        if not self._can_queue_command(command):
            raise ValueError(f'command with command_id={command.command_id} is already running.')

        assert command.status == CommandStatus.READY, f'command {command!s}: not ready'

        for message in command.get_messages():

            log.debug(f'command {command.command_id.name}: '
                      f'putting message {message.uuid!s} with '
                      f'arbitration_id={message.arbitration_id} '
                      f'and payload {message.data!r} in the queue')

            self.command_queue.put_nowait(message)

        command.status = CommandStatus.RUNNING
        self.running_commands[command.positioner_id][command.command_id] = command

        block = (block or __IPYTHON__) if block is None else block

        if block:
            log.debug(f'blocking command {command.command_id.name} until done.')
            await command.wait_for_status(CommandStatus.DONE, loop=self.loop)

    @classmethod
    def from_profile(cls, profile=None, loop=None):
        """Creates a new bus interface from a configuration profile.

        Parameters
        ----------
        profile : `str` or `None`
            The name of the profile that defines the bus interface, or `None`
            to use the default configuration.
        loop : event loop or `None`
            The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
            get a valid loop.

        """

        if profile is None:
            profile = 'default'

        if profile not in config['interfaces']:
            raise ValueError(f'invalid interface profile {profile}')

        config_data = config['interfaces'][profile].copy()
        interface = config_data.pop('interface')

        return cls.__new__(cls, interface, loop=loop, **config_data)

    @staticmethod
    def print_profiles():
        """Prints interface profiles and returns a list of profile names."""

        pprint.pprint(config['interfaces'])

        return config['interfaces'].keys()

    def is_command_running(self, positioner_id, command_id):
        """Checks running commands with ``command_id`` and ``positioner_id``.

        If the command is running, returns its instance.

        """

        for pos_id in [0, positioner_id]:
            if pos_id in self.running_commands and command_id in self.running_commands[pos_id]:
                cmd = self.running_commands[pos_id][command_id]
                if cmd.status.is_done or cmd.command_id != command_id:
                    self.running_commands[pos_id].pop(command_id)
                else:
                    return cmd

        return False

    def _can_queue_command(self, command):
        """Checks whether we can queue the command."""

        return not self.is_command_running(command.positioner_id, command.command_id)
