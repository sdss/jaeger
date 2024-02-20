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
from dataclasses import dataclass, field

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Generic,
    List,
    Optional,
    Type,
    TypeVar,
    cast,
)

import jaeger
from jaeger import can_log, config, log, start_file_loggers
from jaeger.commands import Command, CommandID, EmptyPool
from jaeger.exceptions import JaegerCANError
from jaeger.interfaces import BusABC, CANNetBus, Message, Notifier, VirtualBus
from jaeger.maskbits import CommandStatus
from jaeger.utils import Poller, parse_identifier


try:
    from can.interfaces.slcan import slcanBus  # type: ignore
    from can.interfaces.socketcan import SocketcanBus  # type: ignore
except ImportError:
    SocketcanBus = None
    slcanBus = None

if TYPE_CHECKING:
    from .fps import FPS


__all__ = ["JaegerCAN", "CANnetInterface", "INTERFACES"]


LOG_HEADER = "({cmd.command_id.name}, {cmd.command_uid}):"

#: Accepted CAN interfaces and whether they are multibus.
INTERFACES = {
    "slcan": {"class": slcanBus, "multibus": False},
    "socketcan": {"class": SocketcanBus, "multibus": False},
    "virtual": {"class": VirtualBus, "multibus": False},
    "cannet": {"class": CANNetBus, "multibus": True},
}


Bus_co = TypeVar("Bus_co", bound="BusABC")
T = TypeVar("T", bound="JaegerCAN")


@dataclass
class JaegerCAN(Generic[Bus_co]):
    """A CAN interface with a command queue and reply handling.

    Provides support for multi-channel CAN networks, with each channel being
    able to host more than one bus. The recommended way to instantiate a new
    `.JaegerCAN` object is using the `.create` classmethod ::

        can = await JaegerCAN.create(...)

    which is equivalent to ::

        can = JaegerCAN(...)
        await can.start()

    Parameters
    ----------
    interface_type
        One of `~jaeger.can.INTERFACES`.
    channels
        A list of channels to be used to instantiate the interfaces.
    fps
        The focal plane system.
    interface_args
        Keyword arguments to pass to the interfaces when
        initialising it (e.g., port, baudrate, etc).

    """

    interface_type: str
    channels: list | tuple
    fps: Optional[jaeger.fps.FPS] = None
    interface_args: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.interface_type not in INTERFACES:
            raise ValueError(f"Invalid interface {self.interface_type}.")

        # Start can file logger
        if config["debug"] is True:
            start_file_loggers(start_log=False, start_can=True)

        # List of interfaces for each channel.
        self.interfaces: List[Bus_co] = []

        self.multibus: bool = False

        self._started: bool = False

        # Currently running commands.
        self.running_commands: Dict[int, Command] = {}

        self.command_queue: asyncio.Queue[Command] | None = None
        self._command_queue_task: asyncio.Task | None = None

        self.notifier: Notifier | None = None

    async def start(self: T) -> T:
        self.stop()

        itype = self.interface_type

        InterfaceClass: Type[Bus_co] = INTERFACES[itype]["class"]
        self.multibus = INTERFACES[itype]["multibus"]

        if not isinstance(self.channels, (list, tuple)):
            self.channels = [self.channels]

        for channel in self.channels:
            iargs = "".join([f" {k}={repr(v)}" for k, v in self.interface_args.items()])
            log.debug(f"creating interface {itype}, channel={channel!r}{iargs}.")
            try:
                interface = InterfaceClass(channel, **self.interface_args)
                result = await interface.open()
                if result is False:
                    raise ConnectionError()
                self.interfaces.append(interface)
            except ConnectionResetError:
                log.error(
                    f"connection to {itype}:{channel} failed. "
                    "Possibly another instance is connected to the device."
                )
            except (socket.timeout, ConnectionError, ConnectionRefusedError, OSError):
                log.error(f"connection to {itype}:{channel} failed.")
            except Exception as ee:
                raise ee.__class__(f"connection to {itype}:{channel} failed: {ee}.")

        self.command_queue = asyncio.Queue()
        self._command_queue_task = asyncio.create_task(self._process_command_queue())

        self.notifier = Notifier(
            listeners=[self._process_reply_queue],
            buses=self.interfaces,
        )

        self._started = True

        return self

    def stop(self):
        """Stops the interfaces."""

        if self.notifier:
            self.notifier.stop()
            self.notifier = None

        for interface in self.interfaces:
            interface: Any
            try:
                interface.close()
            except AttributeError:
                pass

        self.interfaces = []

        if self._command_queue_task:
            self._command_queue_task.cancel()

        self._started = False

    @classmethod
    async def create(
        cls,
        profile: Optional[str] = None,
        fps: FPS | None = None,
        interface_type: Optional[str] = None,
        channels: list | tuple = [],
        interface_args: Dict[str, Any] = {},
    ) -> "JaegerCAN":
        """Create and initialise a new bus interface from a configuration profile.

        This is the preferred method to initialise a new `.JaegerCAN` instance and is
        equivalent to calling ``JaegerCAN`` and then `~.JaegerCAN.start`.

        Parameters
        ----------
        profile
            The name of the profile that defines the bus interface, or `None`
            to use the default configuration.
        fps
            The focal plane system.
        interface_type
            One of `~jaeger.can.INTERFACES`. Cannot be used with ``profile``.
        channels
            A list of channels to be used to instantiate the interfaces.
        interface_args
            Keyword arguments to pass to the interfaces when
            initialising it (e.g., port, baudrate, etc).

        """

        if profile is not None or (profile is None and interface_type is None):
            if "profiles" not in config:
                raise ValueError("No 'interfaces' section in the configuration file.")

            if profile is None:
                if "default" not in config["profiles"]:
                    raise ValueError("Default interface not defined in configuration.")
                profile = config["profiles"]["default"]

            if profile not in config["profiles"]:
                raise ValueError(f"Invalid interface profile {profile}")

            config_data = config["profiles"][profile].copy()

            interface_type = config_data.pop("interface")
            if interface_type not in INTERFACES:
                raise ValueError(f"invalid interface {interface_type}")

            if "channel" in config_data:
                channels = [config_data.pop("channel")]
            elif "channels" in config_data:
                channels = config_data.pop("channels")
                assert isinstance(channels, (list, tuple)), "channels must be a list"
            else:
                raise KeyError("channel or channels key not found.")

            interface_args = config_data

        elif profile is not None and interface_type is not None:
            raise JaegerCANError("profile and interface_type are mutually exclusive.")

        if interface_type == "cannet":
            cls = CANnetInterface

        assert interface_type, "interface_type not set. This should not have happened."

        instance = cls(
            interface_type,
            channels=channels,
            fps=fps,
            interface_args=interface_args,
        )

        await instance.start()

        return instance

    def refresh_running_commands(self):
        """Clears completed commands."""

        rc = self.running_commands
        self.running_commands = {key: cmd for key, cmd in rc.items() if not cmd.done()}

    async def _process_command_queue(self):
        """Processes messages in the command queue."""

        assert self.command_queue

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

            try:
                self.send_messages(cmd)
            except EmptyPool:
                if cmd.positioner_ids == [0]:
                    # We'll ignore this case silently since generally this happens only
                    # with GET_FIRMWARE or GET_STATUS that can usually be delayed.
                    loop = asyncio.get_event_loop()
                    loop.call_later(1, self.command_queue.put_nowait, cmd)
                    continue
            except jaeger.JaegerError as ee:
                can_log.error(f"found error while getting messages: {ee}")
                continue

    async def _process_reply_queue(self, msg: Message):
        """Processes one reply message."""

        positioner_id, command_id, reply_uid, __ = parse_identifier(msg.arbitration_id)

        if command_id == CommandID.COLLISION_DETECTED:
            # Sending stop trajectories causes many more robots to report a collision
            # so if the FPS has already been locked we ignore those.
            if not self.fps or self.fps.locked:
                return

            log.error(
                f"A collision was detected in positioner {positioner_id}. "
                "Sending SEND_TRAJECTORY_ABORT and locking the FPS."
            )

            if self.fps:
                await self.fps.lock(by=[positioner_id])
                return

        if command_id == 0:
            can_log.warning(
                "invalid command with command_id=0, "
                f"arbitration_id={msg.arbitration_id} received. "
                "Ignoring it."
            )
            return

        command_id_flag = CommandID(command_id)

        self.refresh_running_commands()

        cmd_key = (positioner_id << 25) + (command_id << 15) + reply_uid

        if cmd_key in self.running_commands:
            running_cmd = self.running_commands[cmd_key]
        elif (command_id << 15) + reply_uid in self.running_commands:
            # Checks if the reply corresponds to a broadcast.
            cmd_key = (command_id << 15) + reply_uid
            running_cmd = self.running_commands[cmd_key]
        else:
            can_log.debug(
                f"[{command_id_flag.name}, {positioner_id}]: "
                f"cannot find a matching running command."
            )
            return

        if not running_cmd.is_broadcast:
            if reply_uid not in running_cmd.message_uids:
                can_log.debug(
                    f"[{command_id_flag.name}, {positioner_id}]: "
                    f"matching command does not contain reply UID={reply_uid}."
                )
                return

        can_log.debug(
            f"[{command_id_flag.name}, "
            f"{positioner_id}, {running_cmd.command_uid}]: "
            f"queuing reply UID={reply_uid} "
            f"to command {running_cmd.command_uid}."
        )

        asyncio.create_task(running_cmd.process_reply(msg))

    def send_messages(self, cmd: Command):
        """Sends messages to the interface.

        This method exists separate from _process_queue so that it can be used
        to send command messages to the interface synchronously.

        """

        log_header = LOG_HEADER.format(cmd=cmd)

        if cmd.status != CommandStatus.READY:
            if cmd.status != CommandStatus.CANCELLED:
                can_log.error(
                    f"{log_header} command is not ready "
                    f"(status={cmd.status.name!r})"
                )
                cmd.cancel()
            return

        can_log.debug(
            f"{log_header} sending command {cmd.command_uid} "
            f"to positioners {cmd.positioner_ids!r}."
        )
        can_log.debug(log_header + " sending messages to CAN bus.")

        messages = cmd.get_messages()

        for message in messages:
            if cmd.status.failed:
                can_log.debug(
                    f"{log_header} not sending more messages "
                    "since this command has failed."
                )
                break

            cmd_key = message.positioner_id << 25
            cmd_key += message.command.command_id << 15
            cmd_key += message.uid

            self.running_commands[cmd_key] = message.command

            # Get the interface and buses to which to send this command.
            interfaces = self.interfaces
            bus = None
            is_multibus = self.multibus or len(self.interfaces) > 1
            if is_multibus and message.positioner_id != 0:
                if self.fps and message.positioner_id in self.fps.positioner_to_bus:
                    interface, bus = self.fps.positioner_to_bus[message.positioner_id]
                    interfaces = [cast(Bus_co, interface)]

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

        cmd.status = CommandStatus.RUNNING

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


@dataclass
class CANnetInterface(JaegerCAN[CANNetBus]):
    r"""An interface class specifically for the CAN\@net 200/420 device.

    This class bahaves as `.JaegerCAN` but allows communication with the
    device itself and tracks its status.

    """

    status_interval: float = 5

    def __post_init__(self):
        super().__post_init__()
        self._device_status = collections.defaultdict(dict)

        self.device_status_poller: Poller | None = None

    async def start(self):
        r"""Starts CAN\@net connection."""

        await super().start()

        if len(self.interfaces) == 0:
            return

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
            delay=self.status_interval,
        )
        self.device_status_poller.start()

    def stop(self):
        """Stops the interfaces."""

        super().stop()

        if self.device_status_poller is not None:
            asyncio.create_task(self.device_status_poller.stop())

    async def _process_reply_queue(self, msg: Message):
        """Processes a message checking first if it comes from the device."""

        if msg.arbitration_id == 0:
            return self.handle_device_message(msg)

        await super()._process_reply_queue(msg)

    @property
    def device_status(self):
        """Returns a dictionary with the status of the device."""

        if self.device_status_poller and not self.device_status_poller.running:
            raise ValueError("the device status poller is not running.")

        return self._device_status

    def handle_device_message(self, msg: Message):
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
