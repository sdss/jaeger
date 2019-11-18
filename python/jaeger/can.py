#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import binascii
import collections
import pprint
import re

import can
import can.interfaces.slcan
import can.interfaces.socketcan
import can.interfaces.virtual

import jaeger
import jaeger.interfaces.cannet
from jaeger import can_log, config, log
from jaeger.commands import CommandID, StopTrajectory
from jaeger.maskbits import CommandStatus
from jaeger.utils import Poller


__ALL__ = ['JaegerCAN', 'CANnetInterface', 'JaegerReaderCallback', 'INTERFACES']


#: Accepted CAN interfaces and whether they are multibus.
INTERFACES = {
    'slcan': {
        'class': can.interfaces.slcan.slcanBus,
        'multibus': False
    },
    'socketcan': {
        'class': can.interfaces.socketcan.SocketcanBus,
        'multibus': False
    },
    'virtual': {
        'class': can.interfaces.virtual.VirtualBus,
        'multibus': False
    },
    'cannet': {
        'class': jaeger.interfaces.cannet.CANNetBus,
        'multibus': True
    }
}


class JaegerReaderCallback(can.Listener):
    """A message reader that triggers a callback on message received.

    Parameters
    ----------
    callback : function
        The function to run when a new message is received.
    loop : event loop or `None`
        If an asyncio event loop, the callback will be called with
        ``call_soon``, otherwise it will be called immediately.

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
    """A CAN interface with a command queue and reply handling.

    Provides support for multi-channel CAN networks, with each channel being
    able to host more than one bus. In general, a new instance of `.JaegerCAN`
    is create via the `~.JaegerCAN.from_profile` classmethod.

    Parameters
    ----------
    interface_name : str
        One of `~jaeger.can.INTERFACES`. Defines the
        `python-can <https://python-can.readthedocs.io/en/stable/>`_ interface
        to use.
    channels : list
        A list of channels to be used to instantiate the interfaces.
    loop
        The event loop to use.
    fps : .FPS
        The focal plane system.
    args,kwargs
        Arguments and keyword arguments to pass to the interfaces when
        initialising it (e.g., port, baudrate, etc).

    Attributes
    ----------
    command_queue : asyncio.Queue
        Queue of messages to be sent to the bus. The messages are sent as
        soon as the bus has finished processing any commands with the same
        ``command_id`` and ``positioner_id``.
    listener : JaegerReaderCallback
        A `.JaegerReaderCallback` instance that runs a callback when
        a new message is received from the bus.
    multibus : bool
        Whether the interfaces are multibus.
    notifier : can.Notifier
        A `can.Notifier` instance that processes messages from the list
        of buses, asynchronously.

    """

    def __init__(self, interface_name, channels, *args, loop=None, fps=None, **kwargs):

        self.loop = loop or asyncio.get_event_loop()

        assert interface_name in INTERFACES, f'invalid interface {interface_name}.'
        self.interface_name = interface_name

        InterfaceClass = INTERFACES[interface_name]['class']

        self.multibus = INTERFACES[interface_name]['multibus']

        if not isinstance(channels, (list, tuple)):
            channels = [channels]

        self.fps = fps

        #: list: A list of `python-can`_ interfaces, one for each of the ``channels``.
        self.interfaces = []
        for channel in channels:
            log.info(f'creating interface {interface_name}, '
                     f'channel={channel!r}, args={args}, kwargs={kwargs}.')
            try:
                self.interfaces.append(InterfaceClass(channel, *args, **kwargs))
            except Exception as ee:
                raise ConnectionRefusedError(
                    f'connection to {interface_name}:{channel} failed: {ee}.')

        self._start_notifier()

        self.command_queue = asyncio.Queue()
        self._command_queue_task = self.loop.create_task(self._process_queue())

        #: dict: Commands currently running ordered by ``positioner_id``
        #: (or zero forbroadcast).
        self.running_commands = collections.defaultdict(dict)

    def _start_notifier(self):
        """Starts the listener and notifiers."""

        self.listener = JaegerReaderCallback(self._process_reply, loop=self.loop)
        self.notifier = can.notifier.Notifier(self.interfaces, [self.listener], loop=self.loop)

        log.debug('started JaegerReaderCallback listener and notifiers')

    def _process_reply(self, msg):
        """Processes replies from the bus."""

        positioner_id, command_id, __, __ = jaeger.utils.parse_identifier(msg.arbitration_id)

        if command_id == CommandID.COLLISION_DETECTED:

            log.error('a collision was detected. Sending STOP_TRAJECTORIES '
                      'and locking the FPS.')

            # Manually send the stop trajectory to be sure it has
            # priority over other messages.
            stop_trajectory_command = StopTrajectory(positioner_id=0)
            self.send_to_interface(stop_trajectory_command.get_messages()[0])

            # Now lock the FPS.
            if self.fps:
                self.loop.create_task(self.fps.lock(abort_trajectories=False))

        if command_id == 0:
            can_log.warning('invalid command with command_id=0, '
                            f'arbitration_id={msg.arbitration_id} received. '
                            'Ignoring it.')
            return

        command_id_flag = CommandID(command_id)

        r_cmd = self.is_command_running(positioner_id, command_id)
        if not r_cmd:
            can_log.debug(f'({command_id_flag.name!r}, {positioner_id}): '
                          'ignoring reply for non-running command.')
            return

        can_log.debug(f'({command_id_flag.name!r}, {positioner_id}): queuing reply.')

        r_cmd.reply_queue.put_nowait(msg)

    def send_to_interface(self, message, interfaces=None, bus=None):
        """Sends the message to the appropriate interface and bus."""

        log_header = (f'({message.command.command_id.name!r}, '
                      f'{message.command.positioner_id}): ')

        if len(self.interfaces) == 1 and not self.multibus:
            self.interfaces[0].send(message)
            return

        # If not interface, send the message to all interfaces.
        if interfaces is None:
            interfaces = self.interfaces
        elif isinstance(interfaces, can.BusABC):
            interfaces = [interfaces]

        for iface in interfaces:

            iface_idx = self.interfaces.index(iface)
            data_hex = binascii.hexlify(message.data).decode()
            can_log.debug(log_header + 'sending message with '
                          f'arbitration_id={message.arbitration_id} '
                          f'and data={data_hex!r} to '
                          f'interface {iface_idx}, '
                          f'bus={0 if not bus else bus!r}.')

            if bus:
                iface.send(message, bus=bus)
            else:
                iface.send(message)

    async def _process_queue(self):
        """Processes messages in the command queue."""

        while True:

            cmd = await self.command_queue.get()

            log_header = f'({cmd.command_id.name!r}, {cmd.positioner_id}): '

            if not self._can_queue_command(cmd):

                # If we sent the command with override=True, finds the running
                # command and cancels it.
                if cmd._override:

                    if cmd._silent_on_conflict:
                        can_log.warning(log_header + 'another instance is already '
                                        'running but the new command overrides it. '
                                        'Cancelling previous command.')

                    found = False
                    for pos_id in [0, cmd.positioner_id]:
                        if not found:
                            for other_cmd in self.running_commands[pos_id]:
                                if other_cmd.command_id == cmd.command_id:
                                    found = True
                                    break

                    if not found:
                        can_log.error(log_header + 'cannot find the running command '
                                      'but _can_queue_command returned '
                                      'False. This must be a bug.')
                        continue

                    other_cmd.finish_command(CommandStatus.CANCELLED)
                    self.running_commands[other_cmd.positioner_id].pop(other_cmd)

                else:

                    if cmd._silent_on_conflict is False:
                        can_log.warning(log_header + 'another instance is already '
                                        'running. Requeuing and trying later.')

                    # Requeue command but wait a bit.
                    self.loop.call_later(0.1, self.command_queue.put_nowait, cmd)
                    continue

            if cmd.status != CommandStatus.READY:
                can_log.error(log_header + 'command is not ready')
                continue

            can_log.debug(log_header + 'sending messages to CAN bus.')

            cmd.status = CommandStatus.RUNNING
            self.running_commands[cmd.positioner_id][cmd.command_id] = cmd

            for message in cmd.get_messages():

                # Get the interface and bus to which to send the message
                interfaces = getattr(cmd, '_interface', None)
                bus = getattr(cmd, '_bus', None)

                if cmd.status.failed:
                    can_log.debug(log_header + 'not sending more messages ' +
                                  'since this command has failed.')
                    break

                self.send_to_interface(message, interfaces=interfaces, bus=bus)

    @classmethod
    def from_profile(cls, profile=None, **kwargs):
        """Creates a new bus interface from a configuration profile.

        Parameters
        ----------
        profile : `str` or `None`
            The name of the profile that defines the bus interface, or `None`
            to use the default configuration.

        """

        assert 'profiles' in config, \
            'configuration file does not have an interfaces section.'

        if profile is None:
            assert 'default' in config['profiles'], \
                'default interface not set in configuration.'
            profile = config['profiles']['default']

        if profile not in config['profiles']:
            raise ValueError(f'invalid interface profile {profile}')

        config_data = config['profiles'][profile].copy()

        interface = config_data.pop('interface')
        if interface not in INTERFACES:
            raise ValueError(f'invalid interface {interface}')

        if 'channel' in config_data:
            channels = [config_data.pop('channel')]
        elif 'channels' in config_data:
            channels = config_data.pop('channels')
            assert isinstance(channels, (list, tuple)), 'channels must be a list'
        else:
            raise KeyError('channel or channels key not found.')

        if interface == 'cannet':
            return CANnetInterface(interface, channels, **kwargs, **config_data)

        return cls(interface, channels, **kwargs, **config_data)

    @staticmethod
    def print_profiles():
        """Prints interface profiles and returns a list of profile names."""

        pprint.pprint(config['interfaces'])

        return config['interfaces'].keys()

    def is_command_running(self, positioner_id, command_id):
        """Checks running commands with ``command_id`` and ``positioner_id``.

        If the command is running, returns its instance.

        """

        r_coms = self.running_commands

        for pos_id in [0, positioner_id]:
            if pos_id in r_coms and command_id in r_coms[pos_id]:
                cmd = r_coms[pos_id][command_id]
                if cmd.status.is_done or cmd.command_id != command_id:
                    r_coms[pos_id].pop(command_id)
                else:
                    return cmd

        return False

    def _can_queue_command(self, command):
        """Checks whether we can queue the command."""

        return not self.is_command_running(command.positioner_id,
                                           command.command_id)


CANNET_ERRORS = {
    0: 'Unknown error <error_code>',
    1: 'CAN <port_num> baud rate not found',
    2: 'CAN <port_num> stop failed',
    3: 'CAN <port_num> start failed',
    4: 'CAN <port_num> extended filter is full',
    5: 'CAN <port_num> standard open filter set twice',
    6: 'CAN <port_num> standard filter is full',
    7: 'CAN <port_num> invalid identifier or mask for filter add',
    8: 'CAN <port_num> baud rate detection is busy',
    9: 'CAN <port_num> invalid parameter type',
    10: 'CAN <port_num> invalid CAN state',
    11: 'CAN <port_num> invalid parameter mode',
    12: 'CAN <port_num> invalid port number',
    13: 'CAN <port_num> init auto baud failed',
    14: 'CAN <port_num> filter parameter is missing',
    15: 'CAN <port_num> bus off parameter is missing',
    16: 'CAN <port_num> parameter is missing',
    17: 'DEV parameter is missing',
    18: 'CAN <port_num> invalid parameter brp',
    19: 'CAN <port_num> invalid parameter sjw',
    20: 'CAN <port_num> invalid parameter tSeg1',
    21: 'CAN <port_num> invalid parameter tSeg2',
    22: 'CAN <port_num> init custom failed',
    23: 'CAN <port_num> init failed',
    24: 'CAN <port_num> reset failed',
    25: 'CAN <port_num> filter parameter is missing',
    27: 'CYC parameter is missing',
    28: 'CYC message <msg_num> stop failed',
    29: 'CYC message <msg_num> init failed',
    30: 'CYC message <msg_num> invalid parameter port',
    31: 'CYC message <msg_num> invalid parameter msg_num',
    32: 'CYC message <msg_num> invalid parameter time',
    33: 'CYC message <msg_num> invalid parameter data'
}


class CANnetInterface(JaegerCAN):
    r"""An interface class specifically for the CAN\@net 200/420 device.

    This class bahaves as `.JaegerCAN` but allows communication with the
    device itself and tracks its status.

    """

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self._device_status = collections.defaultdict(dict)

        # Get ID and version of interfaces. No need to ask for this in each
        # status poll.
        for interface in self.interfaces:
            interface.write('DEV IDENTIFY')
            interface.write('DEV VERSION')

        # We use call_soon later to be sure the event loop is running when we start
        # the poller. This prevents problems when using the library in IPython.
        self.device_status_poller = Poller('cannet_device',
                                           self._get_device_status,
                                           delay=5,
                                           loop=self.loop)
        self.loop.call_soon(self.device_status_poller.start)

    def _process_reply(self, msg):
        """Processes a message checking first if it comes from the device."""

        if msg.arbitration_id == 0:
            return self.handle_device_message(msg)

        super()._process_reply(msg)

    @property
    def device_status(self):
        """Returns a dictionary with the status of the device."""

        if not self.device_status_poller.running:
            raise ValueError('the device status poller is not running.')

        return self._device_status

    def handle_device_message(self, msg):
        """Handles a reply from the device (i.e., not from the CAN network)."""

        device_status = self._device_status

        interface_id = self.interfaces.index(msg.interface)
        message = msg.data.decode()

        can_log.debug(f'received message {message!r} from interface ID {interface_id}.')

        if message.lower() == 'r ok':
            return

        dev_identify = re.match(r'^R (?P<device>CAN@net \w+ \d+)$', message)
        dev_version = re.match(r'^R V(?P<version>(\d+\.*)+)$', message)
        dev_error = re.match(r'^R ERR (?P<error_code>\d{1,2}) (?P<error_descr>\.+)$', message)
        dev_event = re.match(r'^E (?P<bus>\d+) (?P<event>.+)$', message)
        can_status = re.match(r'^R CAN (?P<bus>\d+) (?P<status>[-|\w]{5}) '
                              r'(?P<buffer>\d+)$', message)

        if dev_identify:
            device = dev_identify.group('device')
            device_status[interface_id]['name'] = device

        elif dev_version:
            version = dev_version.group('version')
            device_status[interface_id]['version'] = version

        elif dev_error:

            if 'errors' not in device_status[interface_id]:
                device_status[interface_id]['errors'] = []

            device_status[interface_id]['errors'].insert(
                0, {'error_code': dev_error.group('error_code'),
                    'error_description': dev_error.group('error_descr'),
                    'timestamp': str(msg.timestamp)})

        elif dev_event:
            bus, event = dev_event.groups()
            bus = int(bus)

            if 'events' not in device_status[interface_id]:
                device_status[interface_id]['events'] = collections.defaultdict(list)

            device_status[interface_id]['events'][bus].insert(
                0, {'event': event, 'timestamp': str(msg.timestamp)})

        elif can_status:
            bus, status, buffer = can_status.groups()
            bus = int(bus)
            buffer = int(buffer)

            # Unpack the status characters. If they are different than '-', they are True.
            status_bool = list(map(lambda x: x != '-', status))
            bus_off, error_warning, data_overrun, transmit_pending, init_state = status_bool

            device_status[interface_id][bus] = {'status': status,
                                                'buffer': buffer,
                                                'bus_off': bus_off,
                                                'error_warning_level': error_warning,
                                                'data_overrun_detected': data_overrun,
                                                'transmit_pending': transmit_pending,
                                                'init_state': init_state,
                                                'timestamp': str(msg.timestamp)}

        else:
            can_log.debug(f'message {message!r} cannot be parsed.')

    def _get_device_status(self):
        """Sends commands to the devices to get status updates."""

        for interface in self.interfaces:
            for bus in interface.buses:
                interface.write(f'CAN {bus} STATUS')
