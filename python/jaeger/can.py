#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import binascii
import collections
import pprint
import re
import socket
import time
import warnings

from typing import Any, Callable, Generic, List, Optional, Type, TypeVar

import can
from can.interfaces.slcan import slcanBus
from can.interfaces.socketcan import SocketcanBus
from can.interfaces.virtual import VirtualBus

import jaeger
import jaeger.interfaces.cannet
import jaeger.utils
from jaeger import can_log, config, log, start_file_loggers
from jaeger.commands import Command, CommandID, Message, StopTrajectory
from jaeger.exceptions import JaegerUserWarning
from jaeger.interfaces.cannet import CANNetBus
from jaeger.maskbits import CommandStatus
from jaeger.utils import Poller


__all__ = ["JaegerCAN", "CANnetInterface", "JaegerReaderCallback", "INTERFACES"]


LOG_HEADER = "({cmd.command_id.name}, {cmd.positioner_id}, {cmd.command_uid}):"

#: Accepted CAN interfaces and whether they are multibus.
INTERFACES = {
    "slcan": {"class": slcanBus, "multibus": False},
    "socketcan": {"class": SocketcanBus, "multibus": False},
    "virtual": {"class": VirtualBus, "multibus": False},
    "cannet": {"class": CANNetBus, "multibus": True},
}


class JaegerReaderCallback(can.Listener):
    """A message reader that triggers a callback on message received.

    Parameters
    ----------
    callback
        The function to run when a new message is received.
    loop
        If an asyncio event loop, the callback will be called with
        ``call_soon``, otherwise it will be called immediately.

    """

    def __init__(
        self,
        callback: Callable[[can.Message], Any],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):

        self.callback = callback
        self.loop = loop

    def on_message_received(self, msg):
        """Calls the callback with the received message."""

        if self.loop:
            self.loop.call_soon(self.callback, msg)
        else:
            self.callback(msg)


Bus_co = TypeVar("Bus_co", bound="can.BusABC", covariant=True)


class JaegerCAN(Generic[Bus_co]):
    """A CAN interface with a command queue and reply handling.

    Provides support for multi-channel CAN networks, with each channel being
    able to host more than one bus. In general, a new instance of `.JaegerCAN`
    is create via the `~.JaegerCAN.from_profile` classmethod.

    Parameters
    ----------
    interface_type
        One of `~jaeger.can.INTERFACES`. Defines the
        `python-can <https://python-can.readthedocs.io/en/stable/>`_ interface
        to use.
    channels
        A list of channels to be used to instantiate the interfaces.
    fps
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

    def __init__(
        self,
        interface_type: str,
        channels: list | tuple,
        *args,
        fps: Optional[jaeger.FPS] = None,
        **kwargs,
    ):

        # Start can file logger
        start_file_loggers(start_log=False, start_can=True)

        self.loop = asyncio.get_event_loop()

        assert interface_type in INTERFACES, f"invalid interface {interface_type}."
        self.interface_type = interface_type

        InterfaceClass: Type[Bus_co] = INTERFACES[interface_type]["class"]

        self.multibus = INTERFACES[interface_type]["multibus"]

        if not isinstance(channels, (list, tuple)):
            channels = [channels]

        self.fps = fps

        #: list: A list of `python-can`_ interfaces, one for each of the ``channels``.
        self.interfaces: List[Bus_co] = []
        for channel in channels:
            log.info(
                f"creating interface {interface_type}, "
                f"channel={channel!r}, args={args}, kwargs={kwargs}."
            )
            try:
                self.interfaces.append(InterfaceClass(channel, *args, **kwargs))
            except ConnectionResetError:
                log.error(
                    f"connection to {interface_type}:{channel} failed. "
                    "Possibly another instance is connected to the device."
                )
            except (socket.timeout, ConnectionRefusedError, OSError):
                log.error(
                    f"connection to {interface_type}:{channel} failed. "
                    "The device is not responding."
                )
            except Exception as ee:
                raise ee.__class__(
                    f"connection to {interface_type}:{channel} failed: {ee}."
                )

        if len(self.interfaces) == 0:
            warnings.warn("cannot connect to any interface.", JaegerUserWarning)

        self._start_notifier()

        #: list: Currently running commands.
        self.running_commands = []

        self.command_queue: asyncio.Queue[Command] = asyncio.Queue()
        self._command_queue_task = self.loop.create_task(self._process_queue())

    def _start_notifier(self):
        """Starts the listener and notifiers."""

        self.listener = JaegerReaderCallback(self._process_reply, loop=self.loop)
        self.notifier = can.notifier.Notifier(
            self.interfaces,
            [self.listener],
            loop=self.loop,
        )

        log.debug("started JaegerReaderCallback listener and notifiers")

    def _process_reply(self, msg: can.Message):
        """Processes replies from the bus."""

        positioner_id, command_id, reply_uid, __ = jaeger.utils.parse_identifier(
            msg.arbitration_id
        )

        if command_id == CommandID.COLLISION_DETECTED:

            log.error(
                f"a collision was detected in positioner {positioner_id}. "
                "Sending STOP_TRAJECTORIES and locking the FPS."
            )

            # Manually send the stop trajectory to be sure it has
            # priority over other messages. No need to do it if the FPS
            # has been locked, which means that we have already stopped
            # trajectories.

            if self.fps and self.fps.locked:
                return

            stop_trajectory_command = StopTrajectory(positioner_id=0)
            self.send_to_interfaces(stop_trajectory_command.get_messages()[0])

            # Now lock the FPS. No need to abort trajectories because we just did.
            if self.fps:
                self.loop.create_task(self.fps.lock(stop_trajectories=False))
                return

        if command_id == 0:
            can_log.warning(
                "invalid command with command_id=0, "
                f"arbitration_id={msg.arbitration_id} received. "
                "Ignoring it."
            )
            return

        command_id_flag = CommandID(command_id)

        # Remove done running command. Leave the failed and cancelled ones in
        # the list for 60 seconds to be able to catch delayed replies. We
        # also sort them so that the most recent commands are found first.
        # This is important for timed out broadcast still in the list while
        # another instance of the same command is running. We want replies to
        # be sent to the running command first.
        self.running_commands = sorted(
            [
                rcmd
                for rcmd in self.running_commands
                if not rcmd.status == rcmd.status.DONE
                and (time.time() - rcmd.start_time) < 60
            ],
            key=lambda cmd: cmd.start_time,
            reverse=True,
        )

        found_cmd = False
        for r_cmd in self.running_commands:
            if r_cmd.command_id == command_id:
                if (reply_uid == 0 and r_cmd.positioner_id == 0) or (
                    reply_uid in r_cmd.message_uids
                    and positioner_id == r_cmd.positioner_id
                ):
                    found_cmd = r_cmd
                    break

        if found_cmd:
            can_log.debug(
                f"({command_id_flag.name}, "
                f"{positioner_id}, {found_cmd.command_uid}): "
                f"queuing reply UID={reply_uid} "
                f"to command {found_cmd.command_uid}."
            )
            found_cmd.reply_queue.put_nowait(msg)
        else:
            can_log.debug(
                f"({command_id_flag.name}, {positioner_id}): "
                f"cannot find running command for reply UID={reply_uid}."
            )

    def send_to_interfaces(
        self,
        message: Message,
        interfaces: Optional[List[Bus_co]] = None,
        bus: Optional[Any] = None,
    ):
        """Sends the message to the appropriate interface and bus."""

        log_header = (
            f"({message.command.command_id.name}, "
            f"{message.command.positioner_id} "
            f"{message.command.command_uid!s}): "
        )

        if len(self.interfaces) == 1 and not self.multibus:
            data_hex = binascii.hexlify(message.data).decode()
            can_log.debug(
                log_header + "sending message with "
                f"arbitration_id={message.arbitration_id}, "
                f"UID={message.uid}, "
                f"and data={data_hex!r} to interface."
            )
            self.interfaces[0].send(message)
            return

        # If not interface, send the message to all interfaces.
        if interfaces is None:
            interfaces = self.interfaces

        for iface in interfaces:

            iface_idx = self.interfaces.index(iface)
            data_hex = binascii.hexlify(message.data).decode()
            can_log.debug(
                log_header + "sending message with "
                f"arbitration_id={message.arbitration_id}, "
                f"UID={message.uid}, "
                f"and data={data_hex!r} to "
                f"interface {iface_idx}, "
                f"bus={0 if not bus else bus!r}."
            )

            if bus:
                iface.send(message, bus=bus)  # type: ignore
            else:
                iface.send(message)

    async def _process_queue(self):
        """Processes messages in the command queue."""

        while True:

            cmd = await self.command_queue.get()

            log_header = LOG_HEADER.format(cmd=cmd)

            if cmd.status != CommandStatus.READY:
                if cmd.status != CommandStatus.CANCELLED:
                    can_log.error(
                        f"{log_header} command is not ready "
                        f"(status={cmd.status.name!r})"
                    )
                    cmd.cancel()
                continue

            can_log.debug(log_header + " sending messages to CAN bus.")

            cmd.status = CommandStatus.RUNNING

            try:
                self._send_messages(cmd)
                self.running_commands.append(cmd)
            except jaeger.JaegerError as ee:
                can_log.error(f"found error while getting messages: {ee}")
                continue

    def _send_messages(self, cmd: Command):
        """Sends messages to the interface.

        This method exists separate from _process_queue so that it can be used
        to send command messages to the interface synchronously.

        """

        log_header = LOG_HEADER.format(cmd=cmd)
        messages = cmd.get_messages()

        for message in messages:

            # Get the interface and bus to which to send the message
            interfaces = getattr(cmd, "_interfaces", None)
            bus = getattr(cmd, "_bus", None)

            if cmd.status.failed:
                can_log.debug(
                    log_header
                    + " not sending more messages "
                    + "since this command has failed."
                )
                self.running_commands.remove(cmd)
                break

            self.send_to_interfaces(message, interfaces=interfaces, bus=bus)

    @classmethod
    def from_profile(cls, profile: Optional[str] = None, **kwargs) -> JaegerCAN:
        """Creates a new bus interface from a configuration profile.

        Parameters
        ----------
        profile
            The name of the profile that defines the bus interface, or `None`
            to use the default configuration.

        """

        assert (
            "profiles" in config
        ), "configuration file does not have an interfaces section."

        if profile is None:
            assert (
                "default" in config["profiles"]
            ), "default interface not set in configuration."
            profile = config["profiles"]["default"]

        if profile not in config["profiles"]:
            raise ValueError(f"invalid interface profile {profile}")

        config_data = config["profiles"][profile].copy()

        interface = config_data.pop("interface")
        if interface not in INTERFACES:
            raise ValueError(f"invalid interface {interface}")

        if "channel" in config_data:
            channels = [config_data.pop("channel")]
        elif "channels" in config_data:
            channels = config_data.pop("channels")
            assert isinstance(channels, (list, tuple)), "channels must be a list"
        else:
            raise KeyError("channel or channels key not found.")

        if interface == "cannet":
            return CANnetInterface(interface, channels, **kwargs, **config_data)

        return cls(interface, channels, **kwargs, **config_data)

    @staticmethod
    def print_profiles() -> List[str]:
        """Prints interface profiles and returns a list of profile names."""

        pprint.pprint(config["interfaces"])

        return list(config["interfaces"].keys())


CANNET_ERRORS = {
    0: "Unknown error <error_code>",
    1: "CAN <port_num> baud rate not found",
    2: "CAN <port_num> stop failed",
    3: "CAN <port_num> start failed",
    4: "CAN <port_num> extended filter is full",
    5: "CAN <port_num> standard open filter set twice",
    6: "CAN <port_num> standard filter is full",
    7: "CAN <port_num> invalid identifier or mask for filter add",
    8: "CAN <port_num> baud rate detection is busy",
    9: "CAN <port_num> invalid parameter type",
    10: "CAN <port_num> invalid CAN state",
    11: "CAN <port_num> invalid parameter mode",
    12: "CAN <port_num> invalid port number",
    13: "CAN <port_num> init auto baud failed",
    14: "CAN <port_num> filter parameter is missing",
    15: "CAN <port_num> bus off parameter is missing",
    16: "CAN <port_num> parameter is missing",
    17: "DEV parameter is missing",
    18: "CAN <port_num> invalid parameter brp",
    19: "CAN <port_num> invalid parameter sjw",
    20: "CAN <port_num> invalid parameter tSeg1",
    21: "CAN <port_num> invalid parameter tSeg2",
    22: "CAN <port_num> init custom failed",
    23: "CAN <port_num> init failed",
    24: "CAN <port_num> reset failed",
    25: "CAN <port_num> filter parameter is missing",
    27: "CYC parameter is missing",
    28: "CYC message <msg_num> stop failed",
    29: "CYC message <msg_num> init failed",
    30: "CYC message <msg_num> invalid parameter port",
    31: "CYC message <msg_num> invalid parameter msg_num",
    32: "CYC message <msg_num> invalid parameter time",
    33: "CYC message <msg_num> invalid parameter data",
}


class CANnetInterface(JaegerCAN[CANNetBus]):
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
            interface.write("DEV IDENTIFY")
            interface.write("DEV VERSION")

        # We use call_soon later to be sure the event loop is running when we start
        # the poller. This prevents problems when using the library in IPython.
        self.device_status_poller = Poller(
            "cannet_device",
            self._get_device_status,
            delay=5,
        )
        self.device_status_poller.start()

    def _process_reply(self, msg: can.Message):
        """Processes a message checking first if it comes from the device."""

        if msg.arbitration_id == 0:
            return self.handle_device_message(msg)

        super()._process_reply(msg)

    @property
    def device_status(self):
        """Returns a dictionary with the status of the device."""

        if not self.device_status_poller.running:
            raise ValueError("the device status poller is not running.")

        return self._device_status

    def handle_device_message(self, msg: can.Message):
        """Handles a reply from the device (i.e., not from the CAN network)."""

        device_status = self._device_status

        interface_id = self.interfaces.index(msg.interface)
        message = msg.data.decode()

        can_log.debug(f"received message {message!r} from interface ID {interface_id}.")

        if message.lower() == "r ok":
            return

        dev_identify = re.match(
            r"^R (?P<device>CAN@net \w+ \d+)$",
            message,
        )
        dev_version = re.match(
            r"^R V(?P<version>(\d+\.*)+)$",
            message,
        )
        dev_error = re.match(
            r"^R ERR (?P<error_code>\d{1,2}) (?P<error_descr>\.+)$",
            message,
        )
        dev_event = re.match(
            r"^E (?P<bus>\d+) (?P<event>.+)$",
            message,
        )
        can_status = re.match(
            r"^R CAN (?P<bus>\d+) (?P<status>[-|\w]{5}) (?P<buffer>\d+)$",
            message,
        )

        if dev_identify:
            device = dev_identify.group("device")
            device_status[interface_id]["name"] = device

        elif dev_version:
            version = dev_version.group("version")
            device_status[interface_id]["version"] = version

        elif dev_error:

            if "errors" not in device_status[interface_id]:
                device_status[interface_id]["errors"] = []

            device_status[interface_id]["errors"].insert(
                0,
                {
                    "error_code": dev_error.group("error_code"),
                    "error_description": dev_error.group("error_descr"),
                    "timestamp": str(msg.timestamp),
                },
            )

        elif dev_event:
            bus, event = dev_event.groups()
            bus = int(bus)

            if "events" not in device_status[interface_id]:
                device_status[interface_id]["events"] = collections.defaultdict(list)

            device_status[interface_id]["events"][bus].insert(
                0, {"event": event, "timestamp": str(msg.timestamp)}
            )

        elif can_status:
            bus, status, buffer = can_status.groups()
            bus = int(bus)
            buffer = int(buffer)

            # Unpack the status characters. If they are different
            # than '-', they are True.
            status_bool = list(map(lambda x: x != "-", status))
            (
                bus_off,
                error_warning,
                data_overrun,
                transmit_pending,
                init_state,
            ) = status_bool

            device_status[interface_id][bus] = {
                "status": status,
                "buffer": buffer,
                "bus_off": bus_off,
                "error_warning_level": error_warning,
                "data_overrun_detected": data_overrun,
                "transmit_pending": transmit_pending,
                "init_state": init_state,
                "timestamp": str(msg.timestamp),
            }

        else:
            can_log.debug(f"message {message!r} cannot be parsed.")

    def _get_device_status(self):
        """Sends commands to the devices to get status updates."""

        for interface in self.interfaces:
            for bus in interface.buses:
                interface.write(f"CAN {bus} STATUS")
