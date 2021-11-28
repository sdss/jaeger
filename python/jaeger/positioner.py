#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import logging
from distutils.version import StrictVersion
from time import time

from typing import List, Optional, Tuple, cast

import jaeger
from jaeger import config, log, maskbits
from jaeger.can import JaegerCAN
from jaeger.commands import CommandID
from jaeger.commands.bootloader import GetFirmwareVersion
from jaeger.commands.goto import GotoAbsolutePosition, GotoRelativePosition, goto
from jaeger.commands.status import GetActualPosition
from jaeger.exceptions import JaegerError, PositionerError
from jaeger.utils import StatusMixIn, bytes_to_int


__all__ = ["Positioner"]


class Positioner(StatusMixIn):
    r"""Represents the status and parameters of a positioner.

    Parameters
    ----------
    positioner_id
        The ID of the positioner
    fps
        The `~jaeger.fps.FPS` instance to which this positioner is linked to.
    centre
        The :math:`(x_{\rm focal}, y_{\rm focal})` coordinates of the
        central axis of the positioner.
    sextant
        The id of the sextant to which this positioner is connected.

    """

    def __init__(
        self,
        positioner_id: int,
        fps: jaeger.FPS | None = None,
        centre: Tuple[Optional[float], Optional[float]] = (None, None),
    ):

        self.fps = fps

        self.positioner_id = positioner_id

        self.centre = centre

        self.alpha = None
        self.beta = None
        self.speed = (None, None)
        self.firmware: str | None = None

        self.disabled = False
        self.precise_moves = True

        super().__init__(
            maskbit_flags=maskbits.PositionerStatus,
            initial_status=maskbits.PositionerStatus.UNKNOWN,
        )

    @property
    def position(self):
        """Returns a tuple with the ``(alpha, beta)`` position."""

        return (self.alpha, self.beta)

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        if not self.status:
            return False

        return self.status.collision

    @property
    def moving(self):
        """Returns `True` if the positioner is moving."""

        if self.status.DISPLACEMENT_COMPLETED not in self.status:
            return True

        return False

    @property
    def initialised(self):
        """Returns ``True`` if the system and datums have been initialised."""

        if self.status is None:
            return False

        if self.is_bootloader():
            if self.status != maskbits.BootloaderStatus.UNKNOWN:
                return True
            return False

        if (
            not self.status.initialised
            or self.flags.DATUM_ALPHA_INITIALIZED not in self.status
            or self.flags.DATUM_BETA_INITIALIZED not in self.status
        ):
            return False

        return True

    def reset(self):
        """Resets positioner values and statuses."""

        self.alpha = None
        self.beta = None
        self.status = self.flags.UNKNOWN
        self.firmware = None

    def _log(self, message, level=logging.DEBUG):
        """Logs a message."""

        log.log(level, f"Positioner {self.positioner_id}: {message}")

    def get_bus(self) -> Tuple[int, int | None]:
        """Returns the interface index and bus."""

        if self.fps is None:
            raise PositionerError("FPS is not defined.")

        if not isinstance(self.fps.can, JaegerCAN):
            raise PositionerError("CAN bus not found.")

        if not self.fps.can.multibus:
            return (0, None)

        iface, bus = self.fps.positioner_to_bus[self.positioner_id]
        iface_idx = self.fps.can.interfaces.index(iface)

        return (iface_idx, bus)

    async def send_command(self, command, error: Optional[str] = None, **kwargs):
        """Sends and awaits a command to the FPS for this positioner."""

        if not self.fps:
            raise PositionerError("FPS is not set.")

        command = await self.fps.send_command(
            command,
            positioner_ids=self.positioner_id,
            **kwargs,
        )

        if error and (command.status.failed or command.status.timed_out):
            raise PositionerError(error)

        return command

    async def update_position(
        self,
        position: Tuple[float, float] = None,
        timeout=1,
    ):
        """Updates the position of the alpha and beta arms."""

        if position is None:

            command = await self.send_command(
                CommandID.GET_ACTUAL_POSITION,
                timeout=timeout,
            )

            assert isinstance(command, GetActualPosition)

            if command.status.failed:
                self.alpha = self.beta = None
                raise PositionerError("failed updating position")

            try:
                position = command.get_positions()[self.positioner_id]
            except ValueError:
                raise PositionerError("cannot parse current position.")

        self.alpha, self.beta = position

    async def update_status(
        self,
        status: maskbits.PositionerStatus | int = None,
        timeout=1.0,
    ):
        """Updates the status of the positioner."""

        assert self.fps, "FPS is not set."

        # Need to update the firmware to make sure we get the right flags.
        if not self.firmware:
            await self.update_firmware_version()

        if not status:

            command = await self.send_command(
                CommandID.GET_STATUS,
                timeout=timeout,
                error="cannot get status.",
            )

            n_replies = len(command.replies)

            if n_replies == 1:
                status = int(bytes_to_int(command.replies[0].data))
            else:
                self.status = self.flags.UNKNOWN
                raise PositionerError(f"GET_STATUS received {n_replies} replies.")

        if not self.is_bootloader():
            self.flags = self.get_positioner_flags()
        else:
            self.flags = maskbits.BootloaderStatus

        self.status = self.flags(int(status))

        # Checks if the positioner is collided. If so, locks the FPS.
        # if not self.is_bootloader() and self.collision and not self.fps.locked:
        #     await self.fps.lock()
        #     raise PositionerError("collision detected. Locking the FPS.")

        return True

    async def wait_for_status(
        self,
        status: List[maskbits.PositionerStatus],
        delay=1,
        timeout: Optional[float] = None,
    ) -> bool:
        """Polls the status until it reaches a certain value.

        Parameters
        ----------
        status
            The status to wait for. Can be a list in which case it will wait
            until all the statuses in the list have been reached.
        delay
            Time, in seconds, to wait between position updates.
        timeout
            How many seconds to wait for the status to reach the desired value
            before aborting.

        Returns
        -------
        result
            Returns `True` if the status has been reached or `False` if the
            timeout limit was reached.

        """

        if self.is_bootloader():
            raise JaegerError("wait_for_status cannot be scheduled in bootloader mode.")

        if not isinstance(status, (list, tuple)):
            status = [status]

        async def status_waiter(wait_for_status):

            while True:
                await self.update_status()
                # Check all statuses in the list
                all_reached = True
                for ss in wait_for_status:
                    if ss not in self.status:
                        all_reached = False
                        break

                if all_reached:
                    return

                await asyncio.sleep(delay)

        wait_for_status = [self.flags(int(ss)) for ss in status]

        try:
            await asyncio.wait_for(status_waiter(wait_for_status), timeout)
        except asyncio.TimeoutError:
            return False

        return True

    async def initialise(self, disable_precise_moves=False):
        """Initialises the position watcher."""

        # Resets all.
        self.reset()

        await self.update_firmware_version()
        await self.update_status()

        # Exits if we are in bootloader mode.
        if self.is_bootloader():
            return True

        if not self.initialised:
            raise PositionerError("failed initialising.")

        # Update position only if it's not bootloader.
        await self.update_position()

        # Sets the default speed
        await self.set_speed(
            alpha=config["positioner"]["motor_speed"],
            beta=config["positioner"]["motor_speed"],
        )

        if StrictVersion(self.firmware) < StrictVersion("04.01.17"):
            self._log("Disabling precise moves requires >=04.01.17", logging.DEBUG)
        else:
            await self.set_precise_move(mode=not disable_precise_moves)

        self._log("initialisation complete.")

        return True

    async def update_firmware_version(self):
        """Updates the firmware version."""

        command = await self.send_command(
            CommandID.GET_FIRMWARE_VERSION,
            error="failed retrieving firmware version.",
        )

        assert isinstance(command, GetFirmwareVersion)

        firmware = command.get_firmware()[self.positioner_id]
        assert firmware is not None and isinstance(firmware, str)

        self.firmware = firmware
        self.flags = self.get_positioner_flags()

        self._log(f"firmware {self.firmware}")

        return True

    def get_positioner_flags(self):
        """Returns the correct position maskbits from the firmware version."""

        assert self.firmware, "Firmware is not set."

        if self.is_bootloader():
            return maskbits.BootloaderStatus

        if StrictVersion(self.firmware) < StrictVersion("04.01.00"):
            return maskbits.PositionerStatusV4_0
        else:
            return maskbits.PositionerStatus

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split(".")[1] == "80"

    async def set_position(self, alpha: float, beta: float):
        """Sets the internal position of the motors."""

        set_position_command = await self.send_command(
            CommandID.SET_ACTUAL_POSITION,
            alpha=float(alpha),
            beta=float(beta),
            error="failed setting position.",
        )

        self._log(f"position set to ({alpha:.2f}, {beta:.2f})")

        return set_position_command

    async def set_speed(self, alpha: float, beta: float, force=False):
        """Sets motor speeds.

        Parameters
        ----------
        alpha
            The speed of the alpha arm, in RPM on the input.
        beta
            The speed of the beta arm, in RPM on the input.
        force
            Allows to set speed limits outside the normal range.

        """

        MIN_SPEED = 0
        MAX_SPEED = 5000

        if (
            alpha < MIN_SPEED
            or alpha > MAX_SPEED
            or beta < MIN_SPEED
            or beta > MAX_SPEED
        ) and not force:
            raise PositionerError("speed out of limits.")

        speed_command = await self.send_command(
            CommandID.SET_SPEED,
            alpha=float(alpha),
            beta=float(beta),
            error="failed setting speed.",
        )

        self.speed = (alpha, beta)

        self._log(f"speed set to ({alpha:.2f}, {beta:.2f})")

        return speed_command

    async def set_precise_move(self, mode, alpha=True, beta=True):
        """Switches the precise moves on alpha and beta."""

        cmds = []

        if not alpha and not beta:
            raise PositionerError("positioneralpha or beta need to be True.")

        if alpha:
            if mode is True:
                cmds.append(self.send_command(CommandID.SWITCH_ON_PRECISE_MOVE_ALPHA))
            else:
                cmds.append(self.send_command(CommandID.SWITCH_OFF_PRECISE_MOVE_ALPHA))

        if beta:
            if mode is True:
                cmds.append(self.send_command(CommandID.SWITCH_ON_PRECISE_MOVE_BETA))
            else:
                cmds.append(self.send_command(CommandID.SWITCH_OFF_PRECISE_MOVE_BETA))

        cmds = await asyncio.gather(*cmds)

        if any([cmd.status != maskbits.CommandStatus.DONE for cmd in cmds]):
            raise PositionerError("failed switching precise moves.")

        self.precise_moves = mode

        return True

    def _can_move(self):
        """Returns `True` if the positioner can be moved."""

        if self.moving or self.collision or not self.initialised or not self.status:
            return False

        if self.flags == maskbits.PositionerStatusV4_0:
            return True
        else:
            PS = maskbits.PositionerStatus
            invalid_bits = [
                PS.COLLISION_DETECT_ALPHA_DISABLE,
                PS.COLLISION_DETECT_BETA_DISABLE,
            ]
            for b in invalid_bits:
                if b in self.status:
                    self._log(f"canot move; found status bit {b.name}.", logging.ERROR)
                    return False
            if (
                PS.CLOSED_LOOP_ALPHA not in self.status
                or PS.CLOSED_LOOP_BETA not in self.status
            ):
                self._log(
                    "canot move; positioner not in closed loop mode.",
                    logging.ERROR,
                )
                return False
            return True

    async def _goto_position(self, alpha: float, beta: float, relative=False):
        """Go to a position."""

        if relative:
            command_id = CommandID.GO_TO_RELATIVE_POSITION
        else:
            command_id = CommandID.GO_TO_ABSOLUTE_POSITION

        return await self.send_command(
            command_id,
            alpha=float(alpha),
            beta=float(beta),
            error="failed going to position.",
        )

    async def goto(
        self,
        alpha: float,
        beta: float,
        speed: Tuple[float, float] = None,
        relative=False,
        force=False,
        use_trajectory=True,
        use_sync_line=None,
    ) -> bool:
        """Moves positioner to a given position.

        Parameters
        ----------
        alpha
            The position where to move the alpha arm, in degrees.
        beta
            The position where to move the beta arm, in degrees.
        speed
            The speed of the ``(alpha, beta)`` arms, in RPM on the input.
        relative
            Whether the movement is absolute or relative to the current
            position.
        force
            Allows to set position and speed limits outside the normal range.
        use_trajectory
            If `True`, uses a trajectory to reach the position.
        use_sync_line
            If ``use_trajectory=True``, whether to use the SYNC line.

        Returns
        -------
        result
            `True` if both arms have reached the desired position, `False` if
            a problem was found.

        Examples
        --------
        ::

            # Move alpha and beta at the currently set speed
            >>> await goto(alpha=100, beta=10)

            # Set the speed of the alpha arm
            >>> await goto(speed=(1000, 500))

        """

        if self.moving:
            raise PositionerError("Positioner is already moving.")

        if force is False and not self._can_move():
            raise PositionerError("Positioner is not in a movable state.")

        if use_trajectory:
            assert self.fps
            try:
                await goto(
                    self.fps,
                    [self.positioner_id],
                    alpha,
                    beta,
                    speed=speed,
                    relative=relative,
                    use_sync_line=use_sync_line,
                )
                return True
            except JaegerError:
                raise

        ALPHA_MAX = 360
        BETA_MAX = 360
        ALPHA_MIN = -ALPHA_MAX if relative else 0
        BETA_MIN = -BETA_MAX if relative else 0

        if (
            alpha < ALPHA_MIN or alpha > ALPHA_MAX or beta < BETA_MIN or beta > BETA_MAX
        ) and not force:
            raise PositionerError("Position out of limits.")

        if not self.initialised:
            raise PositionerError("Not initialised.")

        if None in self.speed:
            raise PositionerError("Speed has not been set.")

        # Update position
        await self.update_position()
        assert (
            self.alpha is not None and self.beta is not None
        ), "alpha or beta positions unknown."

        # Check if safe mode is enabled
        if "safe_mode" in config and config["safe_mode"] is not False:
            if isinstance(config["safe_mode"], bool):
                min_beta = 160
            else:
                min_beta = config["safe_mode"]["min_beta"]

            if (relative is False and beta < min_beta) or (
                relative is True and (self.beta + beta) < min_beta
            ):
                raise PositionerError(
                    "Safe mode enabled. Cannot move beta arm that far."
                )

        original_speed = cast(Tuple[float, float], self.speed)

        try:  # Wrap in try-except to restore speed if something fails.

            # Set the speed
            if speed and all(speed):
                await self.set_speed(speed[0], speed[1], force=force)

            self._log(
                f'goto {"relative" if relative else "absolute"} '
                f"position ({alpha:.3f}, {beta:.3f}) degrees."
            )

            goto_command = await self._goto_position(alpha, beta, relative=relative)
            assert isinstance(
                goto_command,
                (GotoAbsolutePosition, GotoRelativePosition),
            )

            alpha_time, beta_time = goto_command.get_move_time()[self.positioner_id]
            move_time = max([alpha_time, beta_time])
            self._log(f"The move will take {move_time:.2f} seconds", logging.INFO)

            assert self.fps is not None
            assert goto_command.start_time is not None

            while True:
                await asyncio.sleep(0.2)

                if self.fps.locked:
                    # No need to be very verbose about this.
                    self._log(
                        "Cancelling goto because the FPS got locked",
                        logging.DEBUG,
                    )
                    result = False
                    break

                elapsed = time() - goto_command.start_time
                if elapsed > move_time + 3:
                    raise PositionerError("Failed to reach commanded position.")

                await self.update_status()
                if maskbits.PositionerStatus.DISPLACEMENT_COMPLETED in self.status:
                    result = True
                    break

        except BaseException:
            raise

        finally:
            if self.speed != original_speed:
                await self.set_speed(*original_speed, force=force)

        if result is True:
            self._log("Destination reached.", logging.INFO)

        return result

    async def home(self):
        """Homes the positioner.

        Zeroes the positioner by counter-clockwise rotating alpha and beta
        until they hit the hardstops. Blocks until the move is complete.

        """

        if self.moving:
            raise PositionerError("positioner is already moving.")

        if not self.fps:
            raise PositionerError("the positioner is not linked to a FPS instance.")

        await self.send_command(
            "GO_TO_DATUMS", error="failed while sending GO_TO_DATUMS command."
        )

        self._log("waiting to home.")
        await self.wait_for_status(self.flags.DISPLACEMENT_COMPLETED)

        self._log("homed.", logging.INFO)

    async def set_loop(self, motor="both", loop="closed", collisions=True):
        """Sets the control loop for a motor.

        These parameters are cleared after a restart. The motors revert to
        closed loop with collision detection.

        Parameters
        ----------
        motor
            The motor to which these changes apply, either ``'alpha`'``,
            ``'beta'``, or ``'both'``.
        loop
            The type of control loop, either ``'open'`` or ``'closed'``.
        collisions
            Whether the firmware should automatically detect collisions and
            stop the positioner.

        """

        if motor == "both":
            motors = ["alpha", "beta"]
        else:
            motors = [motor]

        for motor in motors:

            command_name = motor.upper() + "_" + loop.upper() + "_LOOP"
            if collisions:
                command_name += "_COLLISION_DETECTION"
            else:
                command_name += "_WITHOUT_COLLISION_DETECTION"

            await self.send_command(
                command_name, error=f"failed setting loop for {motor}."
            )

            self._log(
                f"set motor={motor!r}, loop={loop!r}, "
                f"detect_collision={collisions}",
            )

        return True

    def __repr__(self):

        return (
            f"<Positioner (id={self.positioner_id}, "
            f"status={self.status!s}, initialised={self.initialised})>"
        )

    async def get_number_trajectories(self) -> int | None:
        """Returns the number of trajectories executed by the positioner.

        Will return `None` if the firmware does not support the
        ``GET_NUMBER_TRAJECTORIES``.

        """

        if not self.firmware:
            return None

        if StrictVersion(self.firmware) < StrictVersion("04.01.21"):
            return None

        assert self.fps

        cmd = await self.fps.send_command(
            CommandID.GET_NUMBER_TRAJECTORIES,
            positioner_ids=[self.positioner_id],
            timeout=1,
        )

        n_traj = cmd.get_replies()[self.positioner_id]

        return n_traj
