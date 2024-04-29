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
from dataclasses import dataclass
from glob import glob

from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)

import numpy
from astropy.time import Time
from typing_extensions import Self
from zc.lockfile import LockFile

import jaeger
from jaeger import can_log, config, log, start_file_loggers
from jaeger.can import JaegerCAN
from jaeger.commands import (
    Command,
    CommandID,
    GetFirmwareVersion,
    goto,
    send_trajectory,
)
from jaeger.exceptions import (
    FPSLockedError,
    JaegerError,
    JaegerUserWarning,
    PositionerError,
)
from jaeger.ieb import IEB
from jaeger.interfaces import BusABC
from jaeger.maskbits import FPSStatus, PositionerStatus
from jaeger.positioner import Positioner
from jaeger.utils import Poller, PollerList


if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from jaeger.target.configuration import BaseConfiguration


try:
    from coordio import calibration
except ImportError:
    calibration = None

try:
    IPYTHON = get_ipython()  # type: ignore
except NameError:
    IPYTHON = None


__all__ = ["BaseFPS", "FPS"]


MIN_BETA = 160
LOCK_FILE = "/var/tmp/sdss/jaeger.lock"

_FPS_INSTANCES: dict[Type[BaseFPS], BaseFPS] = {}


class BaseFPS(Dict[int, Positioner]):
    """A class describing the Focal Plane System.

    This class includes methods to read the layout and construct positioner
    objects and can be used by the real `FPS` class or the
    `~jaeger.testing.VirtualFPS`.

    `.BaseFPS` instances are singletons in the sense that one cannot instantiate
    more than one. An error is raise if ``__new__`` is called with an existing
    instance. To retrieve the running instance, use `.get_instance`.

    Attributes
    ----------
    positioner_class
        The class to be used to create a new positioner. In principle this will
        be `.Positioner` but it may be different if the positioners are created
        for a `~jaeger.testing.VirtualFPS`.

    """

    positioner_class: ClassVar[Type[Positioner]] = Positioner

    initialised: bool

    def __new__(cls, *args, **kwargs):
        if cls in _FPS_INSTANCES:
            raise JaegerError(
                "An instance of FPS is already running. "
                "Use get_instance() to retrieve it."
            )

        new_obj = super().__new__(cls, *args, **kwargs)
        _FPS_INSTANCES[cls] = new_obj

        dict.__init__(new_obj, {})
        new_obj.initialised = False

        return new_obj

    @classmethod
    def get_instance(cls, *args, **kwargs) -> Self:
        """Returns the running instance."""

        if cls not in _FPS_INSTANCES:
            return cls(*args, **kwargs)

        if args or kwargs:
            log.warning(
                "Ignoring arguments passed to get_instance() "
                "and returning cached instance."
            )

        return _FPS_INSTANCES[cls]

    @property
    def positioners(self):
        """Dictionary of positioner associated with this FPS.

        This is just a wrapper around the `.BaseFPS` instance which is a
        dictionary itself. May be deprecated in the future.

        """

        return self

    def add_positioner(
        self,
        positioner: int | Positioner,
        centre=(None, None),
    ) -> Positioner:
        """Adds a new positioner to the list, and checks for duplicates."""

        if isinstance(positioner, self.positioner_class):
            positioner_id = positioner.positioner_id
        elif isinstance(positioner, int):
            positioner_id = positioner
            positioner = self.positioner_class(positioner_id, None, centre=centre)
        else:
            raise TypeError(f"Invalid parameter positioner of type {type(positioner)}")

        if positioner_id in self.positioners:
            raise JaegerError(
                f"{self.__class__.__name__}: there is already a "
                f"positioner in the list with positioner_id "
                f"{positioner_id}."
            )

        self.positioners[positioner_id] = positioner

        return positioner


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
    status: FPSStatus = FPSStatus.IDLE | FPSStatus.TEMPERATURE_NORMAL

    def __post_init__(self):

        # Start file logger
        start_file_loggers(start_log=True, start_can=False)

        if config._CONFIG_FILE:
            log.debug(f"Using configuration from {config._CONFIG_FILE}")
        else:
            warnings.warn("Unknown configuration file.", JaegerUserWarning)

        if not IPYTHON:
            loop = asyncio.get_event_loop()
            loop.set_exception_handler(log.asyncio_exception_handler)

        # The mapping between positioners and buses.
        self.positioner_to_bus: Dict[int, Tuple[BusABC, int | None]] = {}

        self.pid_lock: LockFile | None = None

        self._locked = False
        self.locked_by: List[int] = []

        self.observatory = config["observatory"]

        self.disabled: set[int] = set([])

        if IPYTHON:
            log.warning("IEB cannot run inside IPython.")
            self.ieb = False

        if self.ieb is None or self.ieb is True:
            self.ieb = config["ieb"]["config"]

        if isinstance(self.ieb, pathlib.Path):
            self.ieb = str(self.ieb)

        if isinstance(self.ieb, (str, dict)):
            if isinstance(self.ieb, str):
                self.ieb = os.path.expanduser(os.path.expandvars(str(self.ieb)))
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

        self.__status_event = asyncio.Event()
        self.__temperature_task: asyncio.Task | None = None

        self._configuration: BaseConfiguration | None = None
        self._previous_configurations: list[BaseConfiguration] = []
        self._preloaded_configuration: BaseConfiguration | None = None

        # Position and status pollers
        self.pollers = PollerList(
            [
                Poller(
                    "status",
                    self.update_status,
                    delay=config["fps"]["status_poller_delay"],
                ),
                Poller(
                    "position",
                    self.update_position,
                    delay=config["fps"]["position_poller_delay"],
                ),
            ]
        )

    @classmethod
    async def create(
        cls,
        can=None,
        ieb=None,
        initialise=True,
        start_pollers: bool | None = None,
        enable_low_temperature: bool = True,
    ) -> "FPS":
        """Starts the CAN bus and initialises it.

        Note that ``FPS.create()`` always returns a new instance. If you want to
        retrieve the currently running instance use `~.BaseFPS.get_instance`.

        Parameters
        ----------
        initialise
            Whether to initialise the FPS.
        start_pollers
            Whether to initialise the pollers.
        kwargs
            Parameters to pass to `.FPS`.

        """

        # FPS.create() always returns a new instance.
        if cls in _FPS_INSTANCES:
            del _FPS_INSTANCES[cls]

        instance = cls.get_instance(can=can, ieb=ieb)
        await instance.start_can()

        if initialise:
            await instance.initialise(
                start_pollers=start_pollers,
                enable_low_temperature=enable_low_temperature,
            )

        return instance

    async def start_can(self):
        """Starts the JaegerCAN interface."""

        use_lock = config["fps"]["use_lock"]

        if use_lock and self.pid_lock is None:
            try:
                if not os.path.exists(os.path.dirname(LOCK_FILE)):
                    os.makedirs(os.path.dirname(LOCK_FILE))
                self.pid_lock = LockFile(LOCK_FILE)
            except Exception:
                raise JaegerError(
                    f"Failed creating lock file {LOCK_FILE}. "
                    "Probably another instance is running. "
                    "If that is not the case, remove the lock file and retry."
                )

        if isinstance(self.can, JaegerCAN):
            await self.can.start()
            return

        self.can = await JaegerCAN.create(self.can, fps=self)
        return True

    def add_positioner(
        self,
        positioner: int | Positioner,
        centre=(None, None),
        interface: Optional[BusABC | int] = None,
        bus: Optional[int] = None,
    ) -> Positioner:
        positioner = super().add_positioner(positioner, centre=centre)
        positioner.fps = self

        if interface is not None:
            if isinstance(interface, int):
                assert isinstance(self.can, JaegerCAN), "JaegerCAN not initialised."
                interface = self.can.interfaces[interface]
                assert isinstance(interface, BusABC), f"Invalid interface {interface!r}"

            self.positioner_to_bus[positioner.positioner_id] = (interface, bus)

        return positioner

    @property
    def configuration(self):
        """Returns the configuration."""

        return self._configuration

    @configuration.setter
    def configuration(self, new: BaseConfiguration | None):
        """Sets the new configuration."""

        # Store current configuration.
        if self._configuration is not None:
            self._previous_configurations.append(self._configuration)

        # Keep only 10 previous configurations.
        self._previous_configurations = self._previous_configurations[-10:]

        self._configuration = new

    async def initialise(
        self: Self,
        start_pollers: bool | None = None,
        enable_low_temperature: bool = True,
        keep_disabled: bool = True,
        skip_fibre_assignments_check: bool = False,
    ) -> Self:
        """Initialises all positioners with status and firmware version.

        Parameters
        ----------
        start_pollers
            Whether to initialise the pollers.
        enable_low_temperature
            Enables the low temperature warnings.
        keep_disabled
            Maintain the list of disabled/offline robots.
        skip_fibre_assignments_check
            Do not check fibre assignments.

        """

        if start_pollers is None:
            start_pollers = config["fps"]["start_pollers"]
        assert isinstance(start_pollers, bool)

        if keep_disabled:
            for positioner in self.positioners.values():
                if positioner.offline or positioner.disabled:
                    self.disabled.add(positioner.positioner_id)
                elif positioner.positioner_id in self.disabled:
                    self.disabled.remove(positioner.positioner_id)
        else:
            self.disabled = set([])

        # Clear all robots
        self.clear()
        self.positioner_to_bus = {}

        # Stop pollers while initialising
        if self.pollers.running:
            await self.pollers.stop()

        # Make sure CAN buses are connected.
        await self.start_can()

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

        # Stop poller in case they are running
        await self.pollers.stop()

        get_fw_command = self.send_command(
            CommandID.GET_FIRMWARE_VERSION,
            positioner_ids=0,
            timeout=config["fps"]["initialise_timeouts"],
        )

        assert isinstance(get_fw_command, GetFirmwareVersion)
        await get_fw_command

        if get_fw_command.status.failed:
            raise JaegerError("Failed retrieving firmware version.")

        # Loops over each reply and set the positioner status to OK. If the
        # positioner was not in the list, adds it.
        for reply in get_fw_command.replies:
            if reply.positioner_id not in self.positioners:
                if hasattr(reply.message, "interface"):
                    interface = reply.message.interface
                    bus = reply.message.bus
                else:
                    interface = bus = None

                self.add_positioner(reply.positioner_id, interface=interface, bus=bus)

            positioner = self.positioners[reply.positioner_id]
            positioner.fps = self
            positioner.firmware = get_fw_command.get_firmware()[reply.positioner_id]

            if (
                positioner.positioner_id in config["fps"]["disabled_positioners"]
                or positioner.positioner_id in self.disabled
            ):
                positioner.disabled = True
                self.disabled.add(positioner.positioner_id)

        # Add offline robots. Offline positioners are physically in the array but
        # they don't reply to commands and we need to specify their position. Once
        # That's done they behave as normal disabled robots.
        if config["fps"]["offline_positioners"] is not None:
            for pid in config["fps"]["offline_positioners"]:
                off_alpha, off_beta = config["fps"]["offline_positioners"][pid]
                if pid not in self.positioners:
                    positioner = self.add_positioner(pid)
                else:
                    positioner = self.positioners[pid]
                positioner.disabled = True
                positioner.offline = True
                positioner.alpha = off_alpha
                positioner.beta = off_beta

                self.disabled.add(positioner.positioner_id)

        # Mark as initialised here although we have some more work to do.
        self.initialised = True

        positioners = self.positioners.values()
        c_pids = sorted([pp.positioner_id for pp in positioners if not pp.offline])
        if len(c_pids) > 0:
            log.info(f"Found {len(c_pids)} connected positioners: {c_pids!r}.")
        else:
            warnings.warn("No positioners found.", JaegerUserWarning)
            return self

        if len(set([pos.firmware for pos in self.values() if not pos.offline])) > 1:
            warnings.warn(
                "Found positioners with different firmware versions.",
                JaegerUserWarning,
            )

        # Stop all positioners just in case. This won't clear collided flags.
        if not self.is_bootloader():
            await self.stop_trajectory()

        # Initialise positioners
        try:
            disable_precise_moves = config["positioner"]["disable_precise_moves"]
            # if disable_precise_moves:
            #     warnings.warn("Disabling precise moves.", JaegerUserWarning)
            pos_initialise = [
                positioner.initialise(disable_precise_moves=disable_precise_moves)
                for positioner in self.values()
                if positioner.offline is False
            ]
            await asyncio.gather(*pos_initialise)
        except (JaegerError, PositionerError) as err:
            raise JaegerError(f"Some positioners failed to initialise: {err}")

        if disable_precise_moves is True and any(
            [self[i].precise_moves for i in self if self[i].offline is False]
        ):
            log.error("Unable to disable precise moves for some positioners.")

        n_non_initialised = len(
            [
                positioner
                for positioner in self.positioners.values()
                if (
                    positioner.offline is False
                    and (
                        positioner.status == positioner.flags.UNKNOWN
                        or not positioner.initialised
                    )
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

        # Check if any of the positioners are collided and if so lock the FPS.
        locked_by = []
        for positioner in self.values():
            if positioner.collision:
                locked_by.append(positioner.positioner_id)

        if len(locked_by) > 0:
            await self.lock(by=locked_by, do_warn=False, snapshot=False)
            warnings.warn(
                "The FPS was collided and has been locked.",
                JaegerUserWarning,
            )

        if config.get("safe_mode", False) is not False:
            min_beta = MIN_BETA
            if isinstance(config["safe_mode"], dict):
                min_beta = config["safe_mode"].get("min_beta", MIN_BETA)
            warnings.warn(
                f"Safe mode enabled. Minimum beta is {min_beta} degrees.",
                JaegerUserWarning,
            )

        # Disable collision detection for listed robots.
        disable_collision = config["fps"]["disable_collision_detection_positioners"]
        if len(disable_collision) > 0:
            if self.locked:
                warnings.warn(
                    "The FPS is locked. Cannot disable collision detection",
                    JaegerUserWarning,
                )

            else:
                warnings.warn(
                    "Disabling collision detection for positioners: "
                    f"{disable_collision}.",
                    JaegerUserWarning,
                )
                await self.send_command(
                    CommandID.ALPHA_CLOSED_LOOP_WITHOUT_COLLISION_DETECTION,
                    positioner_ids=disable_collision,
                )
                await self.send_command(
                    CommandID.BETA_CLOSED_LOOP_WITHOUT_COLLISION_DETECTION,
                    positioner_ids=disable_collision,
                )

        # Set robots to open loop mode
        open_loop_positioners = config["fps"].get("open_loop_positioners", [])
        if len(open_loop_positioners) > 0:
            if self.locked:
                warnings.warn(
                    "The FPS is locked. Cannot set open loop mode.",
                    JaegerUserWarning,
                )

            else:
                warnings.warn(
                    "Setting open loop mode for positioners: "
                    f"{open_loop_positioners}.",
                    JaegerUserWarning,
                )
                await self.send_command(
                    CommandID.ALPHA_OPEN_LOOP_WITHOUT_COLLISION_DETECTION,
                    positioner_ids=open_loop_positioners,
                )
                await self.send_command(
                    CommandID.BETA_OPEN_LOOP_WITHOUT_COLLISION_DETECTION,
                    positioner_ids=open_loop_positioners,
                )

        # Ensure closed loop mode for remaining robots. This does not work if
        # any of the robots is collided.
        if not self.locked:
            closed_loop_positioners = list(
                set([pid for pid in self.positioners if not self[pid].disabled])
                - set(disable_collision)
                - set(open_loop_positioners)
            )
            await self.send_command(
                CommandID.ALPHA_CLOSED_LOOP_COLLISION_DETECTION,
                positioner_ids=closed_loop_positioners,
            )
            await self.send_command(
                CommandID.BETA_CLOSED_LOOP_COLLISION_DETECTION,
                positioner_ids=closed_loop_positioners,
            )

        # Check that all the robots match the fibre assignments.
        self._check_fibre_assignments(raise_error=not skip_fibre_assignments_check)

        # Start temperature watcher.
        if self.__temperature_task is not None:
            self.__temperature_task.cancel()
        if (
            isinstance(self.ieb, IEB)
            and not self.ieb.disabled
            and enable_low_temperature
        ):
            self.__temperature_task = asyncio.create_task(self._handle_temperature())
        else:
            self.set_status(
                (self.status & ~FPSStatus.TEMPERATURE_NORMAL)
                | FPSStatus.TEMPERATURE_UNKNOWN
            )

        # Issue an update status to get the status set.
        await self.update_status()

        # Start the pollers
        if start_pollers and not self.is_bootloader():
            self.pollers.start()

        return self

    def _check_fibre_assignments(self, raise_error: bool = True):
        """Checks that all the expected robots are present."""

        if calibration is None:
            msg = "coordio.calibrations failed to import. Cannot check assignments."
            if raise_error:
                raise JaegerError(msg)
            else:
                warnings.warn(msg, JaegerUserWarning)
                return

        cal_obs = calibration.fiberAssignments.loc[self.observatory]
        cal_obs = cal_obs.loc[cal_obs.Device == "Positioner"]

        failed: bool = False

        for pid in list(cal_obs.positionerID):
            if pid not in self:
                warnings.warn(
                    f"Positioner {pid} is in fiberAssigments but not connected.",
                    JaegerUserWarning,
                )
                failed = True

        for pid in self:
            if pid not in list(cal_obs.positionerID):
                warnings.warn(
                    f"Positioner {pid} is connected but not in fiberAssigments.",
                    JaegerUserWarning,
                )
                failed = True

        if raise_error and failed:
            raise JaegerError("Some positioners do not match fiberAssignments.csv.")

    def set_status(self, status: FPSStatus):
        """Sets the status of the FPS."""

        if status != self.status:
            self.status = status
            if not self.__status_event.is_set():
                self.__status_event.set()

    async def async_status(self):
        """Generator that yields FPS status changes."""

        yield self.status
        while True:
            await self.__status_event.wait()
            yield self.status
            self.__status_event.clear()

    async def _get_positioner_bus_map(self):
        """Creates the positioner-to-bus map.

        Only relevant if the bus interface is multichannel/multibus.

        """

        assert isinstance(self.can, JaegerCAN), "CAN connection not established."

        if len(self.can.interfaces) == 1 and not self.can.multibus:
            return

        timeout = config["fps"]["initialise_timeouts"]
        id_cmd = self.send_command(
            CommandID.GET_ID,
            positioner_ids=[0],
            timeout=timeout,
        )
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

        return self.status & FPSStatus.MOVING

    def is_bootloader(self):
        """Returns `True` if any positioner is in bootloader mode."""

        return any([pos.is_bootloader() is not False for pos in self.values()])

    def send_command(
        self,
        command: str | int | CommandID | Command,
        positioner_ids: int | List[int] | None = None,
        data: Any = None,
        now: bool = False,
        **kwargs,
    ) -> Command:
        """Sends a command to the bus.

        Parameters
        ----------
        command
            The ID of the command, either as the integer value, a string,
            or the `.CommandID` flag. Alternatively, the `.Command` to send.
        positioner_ids
            The positioner IDs to command, or zero for broadcast. If `None`,
            sends the command to all FPS non-disabled positioners.
        data
            The bytes to send. See `.Command` for details on the format.
        interface
            The index in the interface list for the interface to use. Only
            relevant in case of a multibus interface. If `None`, the positioner
            to bus map will be used.
        bus
            The bus within the interface to be used. Only relevant in case of
            a multibus interface. If `None`, the positioner to bus map will
            be used.
        now
            If `True`, the command is sent to the CAN network immediately,
            skipping the command queue. No tracking is done for this command.
            It should only be used for emergency and shutdown commands.
        kwargs
            Extra arguments to be passed to the command.

        Returns
        -------
        command
            The command sent to the bus. The command needs to be awaited
            before it is considered done.

        """

        if not isinstance(self.can, JaegerCAN) or self.can._started is False:
            raise JaegerError("CAN connection not established.")

        if positioner_ids is None:
            positioner_ids = [p for p in self if not self[p].disabled]

        if not isinstance(command, Command):
            if isinstance(command, str):
                command = CommandID[command]

            command_flag = CommandID(command)
            assert isinstance(command_flag, CommandID)
            CommandClass = command_flag.get_command_class()
            assert CommandClass, "CommandClass not defined"

            command = CommandClass(positioner_ids, data=data, **kwargs)

        assert isinstance(command, Command)

        broadcast = command.is_broadcast
        pids = command.positioner_ids

        if broadcast:
            if any([self[pos].disabled for pos in self]) and not command.safe:
                raise JaegerError("Some positioners are disabled.")
        else:
            if any([self[pid].disabled for pid in pids]) and not command.safe:
                raise JaegerError("Some commanded positioners are disabled.")

        if not broadcast and not all([p in self for p in pids]):
            raise JaegerError("Some positioners are not connected.")

        # Check if we are in bootloader mode.
        in_boot = [p for p in pids if p != 0 and self[p].is_bootloader()]
        if (broadcast and self.is_bootloader()) or (not broadcast and any(in_boot)):
            if not command.bootloader:
                raise JaegerError(
                    f"Cannot send command {command.command_id.name!r} "
                    "while in bootloader mode."
                )

        command_name = command.name
        command_uid = command.command_uid
        header = f"({command_name}, {command_uid}): "

        if self.locked:
            if command.safe:
                log.debug(f"FPS is locked but {command_name} is safe.")
            else:
                command.cancel(silent=True)
                raise FPSLockedError("FPS is locked.")

        elif command.move_command and self.moving:
            command.cancel(silent=True)
            raise JaegerError(
                "Cannot send move command while the "
                "FPS is moving. Use FPS.stop_trajectory() "
                "to stop the FPS."
            )

        if command.status.is_done:
            raise JaegerError(header + "trying to send a done command.")

        if not now:
            assert self.can.command_queue
            self.can.command_queue.put_nowait(command)
            can_log.debug(header + "added command to CAN processing queue.")
        else:
            self.can.send_messages(command)
            can_log.debug(header + "sent command to CAN immediately.")

        return command

    async def lock(
        self,
        stop_trajectories: bool = True,
        by: Optional[List[int]] = None,
        do_warn: bool = True,
        snapshot: bool = True,
    ):
        """Locks the `.FPS` and prevents commands to be sent.

        Parameters
        ----------
        stop_trajectories
            Whether to stop trajectories when locking. This will not
            clear any collided flags.

        """

        self._locked = True
        if do_warn:
            warnings.warn("Locking the FPS.", JaegerUserWarning)

        if stop_trajectories:
            await self.stop_trajectory()

        await self.update_status()

        axes = "?"

        alpha = -999.0
        beta = -999.0

        if by and len(by) > 0:
            self.locked_by += by

            status_bits = self.positioners[by[0]].status
            if status_bits & PositionerStatus.COLLISION_ALPHA:
                axes = "alpha"
            if status_bits & PositionerStatus.COLLISION_BETA:
                if axes == "alpha":
                    axes = "both"
                else:
                    axes = "beta"

            alpha = self.positioners[by[0]].alpha
            beta = self.positioners[by[0]].beta

        if jaeger.actor_instance:
            jaeger.actor_instance.write(
                "e",
                {
                    "locked": True,
                    "locked_by": self.locked_by,
                    "locked_axes": axes,
                    "locked_alpha": alpha,
                    "locked_beta": beta,
                },
            )

        if snapshot:
            if self.locked_by is not None:
                highlight = self.locked_by[0]
            else:
                highlight = None

            log.debug(f"Saving snapshot with highlight {highlight} ({self.locked_by})")
            filename = await self.save_snapshot(highlight=highlight)
            warnings.warn(f"Snapshot for locked FPS: {filename}", JaegerUserWarning)

    async def unlock(self, force=False):
        """Unlocks the `.FPS` if all collisions have been resolved."""

        # Send STOP_TRAJECTORY. This clears the collided flags.
        await self.stop_trajectory(clear_flags=True)

        await self.update_status(timeout=2)

        for positioner in self.positioners.values():
            if positioner.collision:
                self._locked = True
                raise JaegerError(
                    "Cannot unlock the FPS until all the "
                    "collisions have been cleared."
                )

        self._locked = False
        self.locked_by = []

        return True

    def get_positions(self, ignore_disabled=False) -> numpy.ndarray:
        """Returns the alpha and beta positions as an array."""

        data = [
            (p.positioner_id, p.alpha, p.beta)
            for p in self.positioners.values()
            if ignore_disabled is False or p.disabled is False
        ]

        return numpy.array(data)

    def get_positions_dict(
        self,
        ignore_disabled=False,
    ) -> dict[int, tuple[float | None, float | None]]:
        """Returns the alpha and beta positions as a dictionary."""

        return {
            p.positioner_id: (p.alpha, p.beta)
            for p in self.positioners.values()
            if ignore_disabled is False or p.disabled is False
        }

    async def update_status(
        self,
        positioner_ids: Optional[int | List[int]] = None,
        timeout: float = 2,
        is_retry: bool = False,
    ) -> bool:
        """Update statuses for all positioners.

        Parameters
        ----------
        positioner_ids
            The list of positioners to update. If `None`, update all
            positioners.
        timeout
            How long to wait before timing out the command.
        is_retry
            A flag to determine whether the function is being called
            as a retry if the previous command timed out.

        """

        if len(self.positioners) == 0:
            return True

        if positioner_ids is None:
            positioner_ids = [0]
        elif not isinstance(positioner_ids, (list, tuple)):
            positioner_ids = [positioner_ids]

        if positioner_ids == [0]:
            valid = [pid for pid in self if self[pid].offline is False]
            n_positioners = len(valid) if len(valid) > 0 else None
        else:
            n_positioners = None

        await self.update_firmware_version(timeout=timeout)

        command = self.send_command(
            CommandID.GET_STATUS,
            positioner_ids=positioner_ids,
            n_positioners=n_positioners,
            timeout=timeout,
        )
        await command

        if command.status.failed:
            log.warning(f"{CommandID.GET_STATUS.name!r} failed during update status.")
            return False

        if command.status.timed_out and not is_retry and n_positioners is not None:
            log.warning("GET_STATUS timed out. Retrying.")
            return await self.update_status(positioner_ids, is_retry=True)

        if len(command.replies) == 0:
            return True

        update_status_coros = []
        for pid, status_int in command.get_positioner_status().items():  # type: ignore
            if pid not in self:
                continue

            update_status_coros.append(self[pid].update_status(status_int))

        await asyncio.gather(*update_status_coros)

        # Set the status of the FPS based on positioner information.
        # First get the current bitmask without the status bit.
        current = self.status & ~FPSStatus.STATUS_BITS

        pbits = numpy.array([int(p.status) for p in self.values() if not p.disabled])

        coll_bits = PositionerStatus.COLLISION_ALPHA | PositionerStatus.COLLISION_BETA

        if ((pbits & coll_bits) > 0).any():
            self.set_status(current | FPSStatus.COLLIDED)

        elif ((pbits & PositionerStatus.DISPLACEMENT_COMPLETED) > 0).all():
            self.set_status(current | FPSStatus.IDLE)

        else:
            self.set_status(current | FPSStatus.MOVING)

        return True

    async def update_position(
        self,
        positioner_ids: Optional[int | List[int]] = None,
        timeout: float = 2,
        is_retry: bool = False,
    ) -> numpy.ndarray | bool:
        """Updates positions.

        Parameters
        ----------
        positioner_ids
            The list of positioners to update. If `None`, update all
            initialised positioners.
        timeout
            How long to wait before timing out the command.
        is_retry
            A flag to determine whether the function is being called
            as a retry if the previous command timed out.

        """

        valid = [pid for pid in self if self[pid].offline is False]
        if len(self.positioners) == 0 or len(valid) == 0:
            return True

        if positioner_ids is None:
            positioner_ids = [
                pos.positioner_id
                for pos in self.values()
                if pos.initialised and not pos.is_bootloader()
            ]
            if positioner_ids == []:
                return numpy.array([])
        elif isinstance(positioner_ids, int):
            positioner_ids = [positioner_ids]

        positioner_ids = [
            pid
            for pid in positioner_ids
            if pid in self and (not self[pid].disabled and not self[pid].offline)
        ]

        command = await self.send_command(
            CommandID.GET_ACTUAL_POSITION,
            positioner_ids=positioner_ids,
            timeout=timeout,
        )

        if command.status.failed:
            log.error(f"{command.name} failed during update position.")
            return False

        if command.status.timed_out and not is_retry:
            log.warning("GET_ACTUAL_POSITION timed out. Retrying.")
            return await self.update_position(positioner_ids, is_retry=True)

        update_position_commands = []
        for pid, position in command.get_positions().items():  # type: ignore
            if pid not in self:
                continue

            update_position_commands.append(self[pid].update_position(position))

        await asyncio.gather(*update_position_commands)

        return self.get_positions()

    async def update_firmware_version(
        self,
        timeout: float = 2,
        is_retry: bool = False,
    ) -> bool:
        """Updates the firmware version of all connected positioners.

        Parameters
        ----------
        timeout
            How long to wait before timing out the command.
        is_retry
            A flag to determine whether the function is being called
            as a retry if the previous command timed out.

        """

        if len(self.positioners) == 0:
            return True

        valid = [pid for pid in self if self[pid].offline is False]
        n_positioners = len(valid) if len(valid) > 0 else None

        get_fw_command = self.send_command(
            CommandID.GET_FIRMWARE_VERSION,
            positioner_ids=0,
            timeout=timeout,
            n_positioners=n_positioners,
        )

        assert isinstance(get_fw_command, GetFirmwareVersion)
        await get_fw_command

        if get_fw_command.status.failed:
            log.error("Failed retrieving firmware version.")
            return False

        if (
            get_fw_command.status.timed_out
            and not is_retry
            and n_positioners is not None
        ):
            log.warning("GET_FIRMWARE_VERSION timed out. Retrying.")
            return await self.update_firmware_version(timeout=timeout, is_retry=True)

        for reply in get_fw_command.replies:
            pid = reply.positioner_id
            if pid not in self.positioners:
                continue

            positioner = self.positioners[pid]
            positioner.firmware = get_fw_command.get_firmware()[pid]

        return True

    async def is_folded(self):
        """Returns `True` if the array if folded."""

        alphaL, betaL = config["kaiju"]["lattice_position"]

        await self.update_position()
        positions_array = self.get_positions(ignore_disabled=True)

        if len(positions_array) == 0:
            return False

        lattice: Any = numpy.array([alphaL, betaL])
        return numpy.allclose(positions_array[:, 1:] - lattice, 0, atol=1)

    async def stop_trajectory(self, clear_flags=False):
        """Stops all the positioners without clearing collided flags.

        Parameters
        ----------
        clear_flags
            If `True`, sends ``STOP_TRAJECTORY`` which clears collided
            flags. Otherwise sends ``SEND_TRAJECTORY_ABORT``.

        """

        if clear_flags is False:
            await self.send_command(
                "SEND_TRAJECTORY_ABORT",
                positioner_ids=None,
                timeout=0,
                now=True,
            )
        else:
            await self.send_command(
                "STOP_TRAJECTORY",
                positioner_ids=0,
                timeout=0,
                now=True,
            )

        # Check running command that are "move" and cancel them.
        assert isinstance(self.can, JaegerCAN)
        for command in self.can.running_commands.values():
            if command.move_command and not command.done():
                command.cancel(silent=True)

        self.can.refresh_running_commands()

        # Wait 0.5 seconds since we are using timeout=0 and we aren't actually giving
        # time for the robots to reply. Not waiting can cause issues if you emit
        # another command immediately after the stop.
        await asyncio.sleep(0.5)

    async def goto(
        self,
        new_positions: dict[int, tuple[float, float]],
        speed: Optional[float] = None,
        relative=False,
        use_sync_line: bool | None = None,
        go_cowboy: bool = False,
    ):
        """Sends a list of positioners to a given position.

        Parameters
        ----------
        new_positions
            The new positions as a dictionary of positioner ID to a tuple of new
            alpha and beta angles. Positioners not specified will be kept on the
            same positions.
        speed
            The speed to use.
        relative
            If `True`, ``alpha`` and ``beta`` are considered relative angles.
        use_sync_line
            Whether to use the SYNC line to start the trajectories.
        go_cowboy
            If set, does not create a ``kaiju``-safe trajectory. Use at your own risk.

        """

        try:
            traj = await goto(
                self,
                new_positions,
                relative=relative,
                speed=speed,
                use_sync_line=use_sync_line,
                go_cowboy=go_cowboy,
            )
        except Exception:
            raise
        finally:
            await self.update_status()
            await self.update_position()

        return traj

    async def send_trajectory(self, *args, **kwargs):
        """Sends a set of trajectories to the positioners.

        See the documentation for `.send_trajectory`.

        Returns
        -------
        trajectory
            The `.Trajectory` object.

        Raises
        ------
        TajectoryError
            You can inspect the `.Trajectory` object by capturing the error and
            accessing ``error.trajectory``.

        """

        return await send_trajectory(self, *args, **kwargs)

    def abort(self):
        """Aborts trajectories and stops positioners. Alias for `.stop_trajectory`."""

        return asyncio.create_task(self.stop_trajectory())

    async def send_to_all(self, *args, **kwargs):
        """Sends a command to all connected positioners.

        This method has been deprecated. Use `.send_command` with a list
        for ``positioner_ids`` instead.

        """

        raise JaegerError("send_to_all has been deprecated. Use send_command instead.")

    async def report_status(self) -> Dict[str, Any]:
        """Returns a dict with the position and status of each positioner."""

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
            status["devices"] = self.can.device_status  # type: ignore
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

    async def save_snapshot(
        self,
        path: Optional[str | pathlib.Path] = None,
        collision_buffer: float | None = None,
        positions: dict | None = None,
        highlight: int | list | None = None,
        show_disabled: bool = True,
        write_to_actor: bool = True,
    ) -> str | Axes:
        """Creates a plot with the current arrangement of the FPS array.

        Parameters
        ----------
        path
            The path where to save the plot. Defaults to
            ``/data/logs/jaeger/snapshots/MJD/fps_snapshot_<SEQ>.pdf``.
        collision_buffer
            The collision buffer.
        positions
            A dictionary of positioner_id to a mapping of ``"alpha"`` and
            ``"beta"`` positions (``{124: {"alpha": 223.4, "beta": 98.1}, ...}``).
            If not provided, the internal FPS positions will be used.
        highlight
            A robot ID to highlight.
        show_disabled
            If `True`, greys out disabled positioners.
        write_to_actor
            If `True`, writes the name of the snapshot to the actor users.

        """

        from jaeger.kaiju import get_snapshot

        if path is not None:
            path = str(path)

        else:
            mjd = int(Time.now().mjd)
            dirpath = os.path.join(config["fps"]["snapshot_path"], str(mjd))
            if not os.path.exists(dirpath):
                os.makedirs(dirpath)

            path_pattern = dirpath + "/fps_snapshot_*.pdf"
            files = sorted(glob(path_pattern))

            if len(files) == 0:
                seq = 1
            else:
                seq = int(files[-1].split("_")[-1][0:4]) + 1

            path = path_pattern.replace("*", f"{mjd}_{seq:04d}")

        result = await get_snapshot(
            path,
            positions=positions,
            collision_buffer=collision_buffer,
            highlight=highlight,
            show_disabled=show_disabled,
        )

        if result is True and write_to_actor is True and jaeger.actor_instance:
            jaeger.actor_instance.write("i", {"snapshot": path})

        return path

    async def _handle_temperature(self):
        """Handle positioners in low temperature."""

        if not isinstance(self.ieb, IEB):
            log.error("Cannot handle low-temperature mode. IEB not present.")

        async def set_rpm(activate):
            if activate:
                rpm = config["low_temperature"]["rpm_cold"]
                log.warning(f"Low temperature mode. Setting RPM={rpm}.")
            else:
                rpm = config["low_temperature"]["rpm_normal"]
                log.warning(f"Disabling low temperature mode. Setting RPM={rpm}.")

            config["positioner"]["motor_speed"] = rpm

        async def set_idle_power(activate):
            if activate:
                ht = config["low_temperature"]["holding_torque_very_cold"]
                log.warning("Very low temperature mode. Setting holding torque.")
            else:
                ht = config["low_temperature"]["holding_torque_normal"]
                log.warning(
                    "Disabling very low temperature mode. Setting holding torque."
                )
            await self.send_command(
                CommandID.SET_HOLDING_CURRENT,
                alpha=ht[0],
                beta=ht[1],
            )

        sensor = config["low_temperature"]["sensor"]
        cold = config["low_temperature"]["cold_threshold"]
        very_cold = config["low_temperature"]["very_cold_threshold"]
        interval = config["low_temperature"]["interval"]

        while True:
            try:
                assert isinstance(self.ieb, IEB) and self.ieb.disabled is False
                device = self.ieb.get_device(sensor)
                temp = (await device.read())[0]

                # Get the status without the temperature bits.
                base_status = self.status & ~FPSStatus.TEMPERATURE_BITS

                if temp <= very_cold:
                    if self.status & FPSStatus.TEMPERATURE_NORMAL:
                        await set_rpm(True)
                        await set_idle_power(True)
                    elif self.status & FPSStatus.TEMPERATURE_COLD:
                        await set_idle_power(True)
                    else:
                        pass
                    self.set_status(base_status | FPSStatus.TEMPERATURE_VERY_COLD)

                elif temp <= cold:
                    if self.status & FPSStatus.TEMPERATURE_NORMAL:
                        await set_rpm(True)
                    elif self.status & FPSStatus.TEMPERATURE_COLD:
                        pass
                    else:
                        await set_idle_power(False)
                    self.set_status(base_status | FPSStatus.TEMPERATURE_COLD)

                else:
                    if self.status & FPSStatus.TEMPERATURE_NORMAL:
                        pass
                    elif self.status & FPSStatus.TEMPERATURE_COLD:
                        await set_rpm(False)
                    else:
                        await set_rpm(False)
                        await set_idle_power(False)
                    self.set_status(base_status | FPSStatus.TEMPERATURE_NORMAL)

            except BaseException as err:
                log.warning(
                    f"Cannot read device {sensor!r}. "
                    f"Low-temperature tracking temporarily disabled: {err}",
                )
                base_status = self.status & ~FPSStatus.TEMPERATURE_BITS
                self.set_status(base_status | FPSStatus.TEMPERATURE_UNKNOWN)

            finally:
                await asyncio.sleep(interval)

    async def shutdown(self):
        """Stops pollers and shuts down all remaining tasks."""

        if not self.is_bootloader:
            log.info("Stopping positioners and shutting down.")
            await self.stop_trajectory()

        log.debug("Stopping all pollers.")
        if self.pollers:
            await self.pollers.stop()

        log.debug("Cancelling all pending tasks and shutting down.")

        loop = asyncio.get_running_loop()

        tasks = [
            task
            for task in asyncio.all_tasks(loop=loop)
            if task is not asyncio.current_task(loop=loop)
        ]
        list(map(lambda task: task.cancel(), tasks))

        await asyncio.gather(*tasks, return_exceptions=True)

        loop.stop()

        self.discard()

    def discard(self):
        """Discards this singleton instance of the FPS."""

        _FPS_INSTANCES.pop(self.__class__, None)

    async def __aenter__(self):
        await self.initialise()
        return self

    async def __aexit__(self, *excinfo):
        await self.shutdown()

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self
