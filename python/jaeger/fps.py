#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import os
import pathlib
import warnings
from contextlib import suppress
from dataclasses import dataclass

from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, TypeVar, Union

from jaeger import config, log, start_file_loggers
from jaeger.can import CANnetInterface, JaegerCAN
from jaeger.commands import Command, CommandID, GetFirmwareVersion, send_trajectory
from jaeger.exceptions import (
    FPSLockedError,
    JaegerError,
    JaegerUserWarning,
    PositionerError,
    TrajectoryError,
)
from jaeger.ieb import IEB
from jaeger.interfaces import BusABC
from jaeger.positioner import Positioner
from jaeger.utils import Poller, PollerList, bytes_to_int


__all__ = ["BaseFPS", "FPS"]


MIN_BETA = 160


class BaseFPS(dict):
    """A class describing the Focal Plane System.

    This class includes methods to read the layout and construct positioner
    objects and can be used by the real `FPS` class or the
    `~jaeger.testing.VirtualFPS`.

    Parameters
    ----------
    positioner_class
        The class to be used to create a new positioner. In principle this will
        be `.Positioner` but it may be different if the positioners are created
        for a `~jaeger.testing.VirtualFPS`.

    """

    positioner_class: ClassVar[Type[Any]] = Positioner

    def __init__(self):
        dict.__init__(self, {})

    @property
    def positioners(self):
        """Dictionary of positioner associated with this FPS.

        This is just a wrapper around the `.BaseFPS` instance which is a
        dictionary itself. May be deprecated in the future.

        """

        return self

    def add_positioner(self, positioner_id, centre=(None, None)) -> Positioner:
        """Adds a new positioner to the list, and checks for duplicates."""

        if positioner_id in self.positioners:
            raise JaegerError(
                f"{self.__class__.__name__}: there is already a "
                f"positioner in the list with positioner_id "
                f"{positioner_id}."
            )

        self.positioners[positioner_id] = self.positioner_class(
            positioner_id,
            None,
            centre=centre,
        )

        return self.positioners[positioner_id]


T = TypeVar("T", bound="FPS")


@dataclass
class FPS(BaseFPS):
    """A class describing the Focal Plane System.

    The recommended way to instantiate a new `.FPS` object is to use the `.create`
    classmethod ::

        fps = await FPS.create(...)

    which is equivalent to ::

        fps = FPS(...)
        await fps.initialise()

    Parameters
    ----------
    can
        A `.JaegerCAN` instance to use to communicate with the CAN network, or the CAN
        profile from the configuration to use, or `None` to use the default one.
    ieb
        If `True` or `None`, connects the Instrument Electronics Box PLC controller
        using the path to the IEB configuration file stored in jaeger's configuration.
        Can also be an `.IEB` instance, the path to a custom configuration file used
        to load one, or a dictionary with the configuration itself.

    Examples
    --------
    After instantiating a new `.FPS` object it is necessary to call
    `~.FPS.initialise` to retrieve the positioner layout and the status of
    the connected positioners. Note that `~.FPS.initialise` is a coroutine
    which needs to be awaited ::

        >>> fps = FPS(can='default')
        >>> await fps.initialise()
        >>> fps.positioners[4].status
        <Positioner (id=4, status='SYSTEM_INITIALIZED|
        DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|
        BETA_DISPLACEMENT_COMPLETED')>

    """

    can: JaegerCAN | str | None = None
    ieb: Union[bool, IEB, dict, str, pathlib.Path, None] = None

    def __post_init__(self):

        super().__init__()

        # Start file logger
        start_file_loggers(start_log=True, start_can=False)

        if config.CONFIG_FILE:
            log.debug(f"Using configuration from {config.CONFIG_FILE}")
        else:
            warnings.warn("Unknown configuration file.", JaegerUserWarning)

        self.loop = asyncio.get_event_loop()
        self.loop.set_exception_handler(log.asyncio_exception_handler)

        # The mapping between positioners and buses.
        self.positioner_to_bus: Dict[int, Tuple[BusABC, int | None]] = {}

        self._is_multibus = False

        self._locked = False

        if self.ieb is None or self.ieb is True:
            self.ieb = config["files"]["ieb_config"]

        if isinstance(self.ieb, pathlib.Path):
            self.ieb = str(self.ieb)

        if isinstance(self.ieb, (str, dict)):
            if isinstance(self.ieb, str):
                self.ieb = os.path.expanduser(os.path.expandvars(self.ieb))
                if not os.path.isabs(self.ieb):
                    self.ieb = os.path.join(os.path.dirname(__file__), self.ieb)
            try:
                self.ieb = IEB.from_config(self.ieb)
            except FileNotFoundError:
                warnings.warn(
                    f"IEB configuration file {self.ieb} cannot be loaded.",
                    JaegerUserWarning,
                )
                self.ieb = None
        elif self.ieb is False:
            self.ieb = None
        else:
            raise ValueError(f"Invalid input value for ieb {self.ieb!r}.")

        assert isinstance(self.ieb, IEB) or self.ieb is None

        # Position and status pollers
        self.pollers = PollerList(
            [
                Poller(
                    "status",
                    self.update_status,
                    delay=config["fps"]["status_poller_delay"],
                    loop=self.loop,
                ),
                Poller(
                    "position",
                    self.update_position,
                    delay=config["fps"]["position_poller_delay"],
                    loop=self.loop,
                ),
            ]
        )

    @classmethod
    async def create(
        cls,
        can=None,
        ieb=None,
        initialise=True,
        start_pollers=True,
    ) -> FPS:
        """Starts the CAN bus and .

        Parameters
        ----------
        initialise
            Whether to initialise the FPS.
        start_pollers
            Whether to initialise the pollers.
        kwargs
            Parameters to pass to `.FPS`.

        """

        instance = cls(can=can, ieb=ieb)
        await instance._start_can()

        if initialise:
            await instance.initialise(start_pollers=start_pollers)

        return instance

    async def _start_can(self):
        """Starts the JaegerCAN interface."""

        if isinstance(self.can, JaegerCAN):
            if self.can._started:
                return
            else:
                await self.can.start()
                return

        self.can = await JaegerCAN.create(self.can, fps=self)

    async def initialise(self: T, start_pollers: bool = True) -> T:
        """Initialises all positioners with status and firmware version.

        Parameters
        ----------
        start_pollers
            Whether to initialise the pollers.

        """

        await self._start_can()

        # Test IEB connection.
        if isinstance(self.ieb, IEB):
            try:
                async with self.ieb:
                    pass
            except BaseException as err:
                warnings.warn(str(err), JaegerUserWarning)

        assert isinstance(self.can, JaegerCAN), "CAN connection not established."

        if len(self.can.interfaces) == 0:
            warnings.warn("CAN interfaces not found.", JaegerUserWarning)
            return self

        # Get the positioner-to-bus map
        await self._get_positioner_bus_map()

        # Resets all positioners
        for positioner in self.positioners.values():
            positioner.reset()

        # Stop poller in case they are running
        await self.pollers.stop()

        get_firmware_command = self.send_command(
            CommandID.GET_FIRMWARE_VERSION,
            positioner_id=0,
            timeout=config["fps"]["initialise_timeouts"],
        )

        assert isinstance(get_firmware_command, GetFirmwareVersion)
        await get_firmware_command

        if get_firmware_command.status.failed:
            raise JaegerError("Failed retrieving firmware version.")

        # Loops over each reply and set the positioner status to OK. If the
        # positioner was not in the list, adds it.
        replied_pids = [reply.positioner_id for reply in get_firmware_command.replies]
        for pid in replied_pids:
            if pid not in self.positioners:
                self.add_positioner(pid)

            positioner = self.positioners[pid]
            positioner.fps = self
            positioner.firmware = get_firmware_command.get_firmware(pid)

        pids = sorted(list(self.keys()))
        if len(pids) > 0:
            log.info(f"Found {len(pids)} connected positioners: {pids!r}.")
        else:
            warnings.warn("No positioners found.", JaegerUserWarning)
            return self

        if len(set([pos.firmware for pos in self.values()])) > 1:
            warnings.warn(
                "Found positioners with different firmware versions.",
                JaegerUserWarning,
            )

        # Stop all positioners just in case.
        if not self.is_bootloader():
            await self.stop_trajectory()

        # Initialise positioners
        try:
            disable_precise_moves = config["positioner"]["disable_precise_moves"]
            if disable_precise_moves:
                warnings.warn("Disabling precise moves.", JaegerUserWarning)
            pos_initialise = [
                positioner.initialise(disable_precise_moves=disable_precise_moves)
                for positioner in self.values()
            ]
            await asyncio.gather(*pos_initialise)
        except (JaegerError, PositionerError) as err:
            raise JaegerError(f"Some positioners failed to initialise: {err}")

        n_non_initialised = len(
            [
                positioner
                for positioner in self.positioners.values()
                if (
                    positioner.status == positioner.flags.UNKNOWN
                    or not positioner.initialised
                )
            ]
        )

        if n_non_initialised > 0:
            raise JaegerError(f"{n_non_initialised} positioners failed to initialise.")

        if self.is_bootloader():
            bootlist = [p.positioner_id for p in self.values() if p.is_bootloader()]
            warnings.warn(
                f"Positioners in booloader mode: {bootlist!r}.",
                JaegerUserWarning,
            )
            return self

        if self.locked:
            warnings.warn("FPS is locked. Trying to unlock it.", JaegerUserWarning)
            if not await self.unlock():
                raise JaegerError("FPS cannot be unlocked. Initialisation failed.")
            else:
                log.info("FPS unlocked successfully.")

        if config.get("safe_mode", False) is not False:
            min_beta = MIN_BETA
            if isinstance(config["safe_mode"], dict):
                min_beta = config["safe_mode"].get("min_beta", MIN_BETA)
            warnings.warn(
                f"Safe mode enabled. Minimum beta is {min_beta} degrees.",
                JaegerUserWarning,
            )

        # Start the pollers
        if start_pollers and not self.is_bootloader():
            self.pollers.start()

        return self

    async def _get_positioner_bus_map(self):
        """Creates the positioner-to-bus map.

        Only relevant if the bus interface is multichannel/multibus.

        """

        assert isinstance(self.can, JaegerCAN), "CAN connection not established."

        if len(self.can.interfaces) == 1 and not self.can.multibus:
            self._is_multibus = False
            return

        self._is_multibus = True

        timeout = config["fps"]["initialise_timeouts"]
        id_cmd = self.send_command(CommandID.GET_ID, timeout=timeout)
        await id_cmd

        # Parse the replies
        for reply in id_cmd.replies:
            iface = reply.message.interface
            bus = reply.message.bus
            self.positioner_to_bus[reply.positioner_id] = (iface, bus)

    @property
    def locked(self):
        """Returns `True` if the `.FPS` is locked."""

        return self._locked

    @property
    def moving(self):
        """Returns `True` if any of the positioners is moving."""

        return any(
            [pos.moving for pos in self.values() if pos.status != pos.flags.UNKNOWN]
        )

    def is_bootloader(self):
        """Returns `True` if any positioner is in bootloader mode."""

        return any([pos.is_bootloader() is not False for pos in self.values()])

    def send_command(
        self,
        command: str | int | CommandID | Command,
        positioner_id: int = 0,
        data: List[bytearray] = [bytearray([])],
        interface: Optional[BusABC] = None,
        bus: Optional[int] = None,
        broadcast: bool = False,
        synchronous: bool = False,
        **kwargs,
    ) -> Command:
        """Sends a command to the bus.

        Parameters
        ----------
        command
            The ID of the command, either as the integer value, a string,
            or the `.CommandID` flag. Alternatively, the `.Command` to send.
        positioner_id
            The positioner ID to command, or zero for broadcast.
        data
            The bytes to send.
        interface
            The index in the interface list for the interface to use. Only
            relevant in case of a multibus interface. If `None`, the positioner
            to bus map will be used.
        bus
            The bus within the interface to be used. Only relevant in case of
            a multibus interface. If `None`, the positioner to bus map will
            be used.
        broadcast
            If `True`, sends the command to all the buses.
        synchronous
            If `True`, the command is sent to the CAN network immediately,
            skipping the command queue. No tracking is done for this command.
            It should only be used for shutdown commands.
        kwargs
            Extra arguments to be passed to the command.

        Returns
        -------
        command
            The command sent to the bus. The command needs to be awaited
            before it is considered done.

        """

        assert isinstance(self.can, JaegerCAN), "CAN connection not established."

        if positioner_id == 0:
            broadcast = True

        if not isinstance(command, Command):
            command_flag = CommandID(command)
            CommandClass = command_flag.get_command_class()
            assert CommandClass, "CommandClass not defined"

            command = CommandClass(
                positioner_id=positioner_id,
                loop=self.loop,
                data=data,
                **kwargs,
            )

        assert isinstance(command, Command)

        if broadcast:
            if any([self[pos].disabled for pos in self]) and not command.safe:
                raise JaegerError("Some positioners are disabled. Use send_to_all.")
        else:
            if self[positioner_id].disabled and not command.safe:
                raise JaegerError(f"Positioner {positioner_id} is disabled.")

        if positioner_id != 0 and positioner_id not in self.positioners:
            raise JaegerError(f"Positioner {positioner_id} is not connected.")

        # Check if we are in bootloader mode.
        if (broadcast and self.is_bootloader()) or (
            not broadcast and self[positioner_id].is_bootloader() is not False
        ):
            if not command.bootloader:
                raise JaegerError(
                    f"Cannot send command {command.command_id.name!r} "
                    "while in bootloader mode."
                )

        command_name = command.name
        command_uid = command.command_uid
        header = f"({command_name}, {positioner_id}, {command_uid}): "

        if self.locked:
            if command.safe:
                log.debug(f"FPS is locked but {command_name} is safe.")
            else:
                command.cancel(silent=True)
                raise FPSLockedError(
                    "Solve the problem and unlock the FPS before sending commands."
                )

        elif command.move_command and self.moving:
            command.cancel(silent=True)
            raise JaegerError(
                "Cannot send move command while the "
                "FPS is moving. Use FPS.stop_trajectory() "
                "to stop the FPS."
            )

        if command.status.is_done:
            raise JaegerError(header + "trying to send a done command.")

        # By default a command will be sent to all interfaces and buses.
        # Normally we want to set the interface and bus to which the command
        # will be sent.
        if not broadcast:
            self._set_interface(command, bus=bus, interface=interface)
            if command.status == command.status.FAILED:
                return command

        if not synchronous:
            assert self.can.command_queue
            self.can.command_queue.put_nowait(command)
            log.debug(header + "added command to CAN processing queue.")
        else:
            self.can._send_messages(command)
            log.debug(header + "sent command to CAN synchronously.")

        return command

    def _set_interface(
        self,
        command: Command,
        interface: Optional[BusABC] = None,
        bus: Optional[int] = None,
    ):
        """Sets the interface and bus to which to send a command."""

        # Don't do anything if the interface is not multibus
        if not self._is_multibus or command.positioner_id == 0:
            return

        if bus or interface:
            command._interfaces = [interface] if interface else None
            command._bus = bus
            return

        positioner_id = command.positioner_id

        if positioner_id not in self.positioner_to_bus:
            command.finish_command(command.status.FAILED)
            raise JaegerError(f"Positioner {positioner_id} has no assigned bus.")

        interface, bus = self.positioner_to_bus[positioner_id]

        command._interfaces = [interface]
        command._bus = bus

        return

    async def lock(self, stop_trajectories: bool = True):
        """Locks the `.FPS` and prevents commands to be sent.

        Parameters
        ----------
        stop_trajectories
            Whether to stop trajectories when locking.

        """

        warnings.warn("Locking FPS.", JaegerUserWarning)
        self._locked = True

        if stop_trajectories:
            await self.stop_trajectory()

    async def unlock(self, force=False):
        """Unlocks the `.FPS` if all collisions have been resolved."""

        await self.update_status(timeout=0.1)

        for positioner in self.positioners.values():
            if positioner.collision:
                self._locked = True
                raise JaegerError(
                    "Cannot unlock the FPS until all the collisions have been cleared."
                )

        self._locked = False

        return True

    async def update_status(
        self,
        positioner_ids: Optional[List[int]] = None,
        timeout: float = 1,
    ) -> bool:
        """Update statuses for all positioners.

        Parameters
        ----------
        positioner_ids
            The list of positioners to update. If `None`, update all
            positioners.
        timeout
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if positioner_ids:
            n_positioners = len(positioner_ids)
        else:
            # This is the max number that should reply.
            n_positioners = len(self) if len(self) > 0 else None

        await self.update_firmware_version(timeout=timeout)

        command = self.send_command(
            CommandID.GET_STATUS,
            positioner_id=0,
            n_positioners=n_positioners,
            timeout=timeout,
        )
        await command

        if command.status.failed:
            log.warning(
                f"Failed broadcasting {CommandID.GET_STATUS.name!r} "
                "during update status."
            )
            return False

        update_status_coros = []
        for reply in command.replies:

            pid = reply.positioner_id
            if pid not in self or (positioner_ids and pid not in positioner_ids):
                continue

            positioner = self[pid]

            status_int = int(bytes_to_int(reply.data))
            update_status_coros.append(positioner.update_status(status_int))

        await asyncio.gather(*update_status_coros)

        return True

    async def update_position(
        self,
        positioner_ids: Optional[List[int]] = None,
        timeout: float = 1,
    ) -> bool:
        """Updates positions.

        Parameters
        ----------
        positioner_ids
            The list of positioners to update. If `None`, update all
            positioners.
        timeout
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if not positioner_ids:
            positioner_ids = [
                pos.positioner_id
                for pos in self.values()
                if pos.initialised and not pos.is_bootloader()
            ]
            if not positioner_ids:
                return True

        commands_all = self.send_to_all(
            CommandID.GET_ACTUAL_POSITION,
            positioners=positioner_ids,
            timeout=timeout,
        )

        commands = await commands_all

        update_position_commands = []
        for command in commands:

            pid = command.positioner_id

            if not isinstance(command, Command) or (
                command.status.failed and self[pid].initialised
            ):
                log.warning(
                    f"({CommandID.GET_ACTUAL_POSITION.name}, "
                    f"{command.positioner_id}): "
                    "failed during update position."
                )
                continue

            try:
                position = command.get_positions()  # type: ignore
                update_position_commands.append(self[pid].update_position(position))
            except ValueError as ee:
                raise JaegerError(
                    f"Failed updating position for positioner {pid}: {ee}"
                )

        await asyncio.gather(*update_position_commands)

        return True

    async def update_firmware_version(
        self,
        positioner_ids: Optional[List[int]] = None,
        timeout: float = 2,
    ) -> bool:
        """Updates the firmware version of connected positioners.

        Parameters
        ----------
        positioner_ids
            The list of positioners to update. If `None`, update all
            positioners. ``positioner_ids=False`` ignores currently
            connected positioners and times out to receive all possible
            replies.
        timeout
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if positioner_ids:
            n_positioners = len(positioner_ids)
        else:
            n_positioners = len(self) if len(self) > 0 else None

        get_firmware_command = self.send_command(
            CommandID.GET_FIRMWARE_VERSION,
            positioner_id=0,
            timeout=timeout,
            n_positioners=n_positioners,
        )

        assert isinstance(get_firmware_command, GetFirmwareVersion)
        await get_firmware_command

        if get_firmware_command.status.failed:
            raise JaegerError("Failed retrieving firmware version.")

        for reply in get_firmware_command.replies:
            pid = reply.positioner_id
            if pid not in self.positioners or (
                positioner_ids and pid not in positioner_ids
            ):
                continue

            positioner = self.positioners[pid]
            positioner.firmware = get_firmware_command.get_firmware(pid)

        return True

    async def stop_trajectory(
        self,
        positioners: Optional[List[int]] = None,
        clear_flags: bool = True,
        timeout: float = 0,
    ):
        """Stops all the positioners.

        Parameters
        ----------
        positioners
            The list of positioners to abort. If `None`, abort all positioners.
        clear_flags
            If `True`, in addition to sending ``TRAJECTORY_TRANSMISSION_ABORT``
            sends ``STOP_TRAJECTORY`` which clears all the collision and
            warning flags.
        timeout
            How long to wait before timing out the command. By default, just
            sends the command and does not wait for replies.

        """

        if positioners is None:
            positioners = [
                positioner_id
                for positioner_id in self.keys()
                if not self[positioner_id].is_bootloader()
            ]
            if positioners == []:
                return

        await self.send_to_all(
            "TRAJECTORY_TRANSMISSION_ABORT",
            positioners=positioners,
        )

        if clear_flags:
            await self.send_command(
                "STOP_TRAJECTORY",
                positioner_id=0,
                timeout=timeout,
            )

    async def send_trajectory(self, *args, **kwargs):
        """Sends a set of trajectories to the positioners.

        See the documentation for `.send_trajectory`.

        """

        try:
            return await send_trajectory(self, *args, **kwargs)
        except TrajectoryError as ee:
            raise JaegerError(f"Sending trajectory failed with error: {ee}")

    def abort(self):
        """Aborts trajectories and stops positioners."""

        cmd = self.send_command(CommandID.STOP_TRAJECTORY, positioner_id=0)
        return asyncio.create_task(cmd)

    async def send_to_all(
        self,
        command: str | int | CommandID | Command,
        positioners: Optional[List[int]] = None,
        data: Optional[List[bytearray]] = None,
        **kwargs,
    ) -> List[Command]:
        """Sends a command to multiple positioners and awaits completion.

        Parameters
        ----------
        command
            The ID of the command, either as the integer value, a string,
            or the `.CommandID` flag. Alternatively, the `.Command` to send.
        positioners
            The list of ``positioner_id`` of the positioners to command. If
            `None`, sends the command to all the positioners in the FPS that
            are not disabled.
        data
            The payload to send. If `None`, no payload is sent. If the value
            is a list with a single value, the same payload is sent to all
            the positioners. Otherwise the list length must match the number
            of positioners.
        kwargs
            Keyword argument to pass to the command.

        Returns
        -------
        commands
            A list with the command instances executed.

        """

        if positioners is None or positioners == 0:
            positioners = [pos for pos in self.keys() if not self[pos].disabled]
        else:
            assert isinstance(positioners, (list, tuple))

        if data is None or len(data) == 1:
            commands = [
                self.send_command(command, positioner_id=positioner_id, **kwargs)
                for positioner_id in positioners
            ]
        else:
            commands = [
                self.send_command(
                    command,
                    positioner_id=positioner_id,
                    data=[data[ii]],
                    **kwargs,
                )
                for ii, positioner_id in enumerate(positioners)
            ]

        try:
            await asyncio.gather(*commands)
        except (JaegerError, FPSLockedError):
            for command in commands:
                command.cancel()
                with suppress(asyncio.CancelledError):
                    await command
            raise

        return commands

    async def report_status(self) -> Dict[str, Any]:
        """Returns a dict with the position and status of each positioner."""

        assert isinstance(self.can, CANnetInterface)

        status: Dict[str, Any] = {"positioners": {}}

        for positioner in self.positioners.values():

            pos_status = positioner.status
            pos_firmware = positioner.firmware
            pos_alpha = positioner.alpha
            pos_beta = positioner.beta

            status["positioners"][positioner.positioner_id] = {
                "position": [pos_alpha, pos_beta],
                "status": pos_status,
                "firmware": pos_firmware,
            }

        try:
            status["devices"] = self.can.device_status
        except AttributeError:
            pass

        if not isinstance(self.ieb, IEB):
            status["ieb"] = False
        else:
            if self.ieb.disabled:
                status["ieb"] = False
            else:
                status["ieb"] = await self.ieb.get_status()

        return status

    async def shutdown(self):
        """Stops pollers and shuts down all remaining tasks."""

        bootloader = all(
            [positioner.is_bootloader() is True for positioner in self.values()]
        )

        if not bootloader:
            log.info("Stopping positioners")
            await self.stop_trajectory()

        log.info("Stopping all pollers.")
        if self.pollers:
            await self.pollers.stop()

        await asyncio.sleep(1)

        log.info("Cancelling all pending tasks and shutting down.")

        tasks = [
            task
            for task in asyncio.all_tasks(loop=self.loop)
            if task is not asyncio.current_task(loop=self.loop)
        ]
        list(map(lambda task: task.cancel(), tasks))

        await asyncio.gather(*tasks, return_exceptions=True)

        self.loop.stop()

    async def __aenter__(self):
        await self.initialise()
        return self

    async def __aexit__(self, *excinfo):
        await self.shutdown()
