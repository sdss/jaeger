#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-03
# @Filename: goto.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import warnings

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import jaeger
from jaeger import config
from jaeger.commands import Command, CommandID
from jaeger.exceptions import (
    FPSLockedError,
    JaegerError,
    JaegerUserWarning,
    TrajectoryError,
)
from jaeger.kaiju import get_path_pair
from jaeger.utils import (
    bytes_to_int,
    get_goto_move_time,
    int_to_bytes,
    motor_steps_to_angle,
)
from jaeger.utils.helpers import run_in_executor

from .trajectory import send_trajectory


if TYPE_CHECKING:
    from clu.command import Command as CluCommand

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = [
    "GoToDatums",
    "GoToDatumAlpha",
    "GoToDatumBeta",
    "GotoAbsolutePosition",
    "GotoRelativePosition",
    "SetActualPosition",
    "SetSpeed",
    "SetCurrent",
    "goto",
]


TIME_STEP = jaeger.config["positioner"]["time_step"]


class GoToDatums(Command):
    """Initialises and zeroes the positioner."""

    command_id = CommandID.GO_TO_DATUMS
    broadcastable = False
    move_command = True


class GoToDatumAlpha(Command):
    """Initialises and zeroes the alpha arm of the positioner."""

    command_id = CommandID.GO_TO_DATUM_ALPHA
    broadcastable = False
    move_command = True


class GoToDatumBeta(Command):
    """Initialises and zeroes the beta arm of the positioner."""

    command_id = CommandID.GO_TO_DATUM_BETA
    broadcastable = False
    move_command = True


class GotoAbsolutePosition(Command):
    """Moves alpha and beta to absolute positions in degrees."""

    command_id = CommandID.GO_TO_ABSOLUTE_POSITION
    broadcastable = False
    move_command = True

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):
        if alpha is not None and beta is not None:
            alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

            alpha_bytes = int_to_bytes(alpha_steps, dtype="i4")
            beta_bytes = int_to_bytes(beta_steps, dtype="i4")

            data = alpha_bytes + beta_bytes
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def decode(data):
        """Decodes message data into alpha and beta moves."""

        alpha_steps = bytes_to_int(data[0:4], dtype="i4")
        beta_steps = bytes_to_int(data[4:8], dtype="i4")

        return motor_steps_to_angle(alpha_steps, beta_steps)

    def get_replies(self) -> Dict[int, Tuple[float, float]]:
        return self.get_move_time()

    def get_move_time(self):
        """Returns the time needed to move to the commanded position.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        move_times = {}
        for reply in self.replies:
            data = reply.data

            alpha = bytes_to_int(data[0:4], dtype="i4")
            beta = bytes_to_int(data[4:], dtype="i4")

            move_times[reply.positioner_id] = [alpha * TIME_STEP, beta * TIME_STEP]

        return move_times


class GotoRelativePosition(GotoAbsolutePosition):
    """Moves alpha and beta a relative number of degrees."""

    command_id = CommandID.GO_TO_RELATIVE_POSITION
    broadcastable = False
    move_command = True


class SetActualPosition(Command):
    """Sets the current position of the alpha and beta arms."""

    command_id = CommandID.SET_ACTUAL_POSITION
    broadcastable = False
    safe = True
    move_command = True  # Technically not a move command but we don't
    # want to issue it during a move.

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):
        if alpha is not None and beta is not None:
            alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

            alpha_bytes = int_to_bytes(int(alpha_steps), dtype="i4")
            beta_bytes = int_to_bytes(int(beta_steps), dtype="i4")

            data = alpha_bytes + beta_bytes

            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


class SetSpeed(Command):
    """Sets the speeds of the alpha and beta motors."""

    command_id = CommandID.SET_SPEED
    broadcastable = False
    safe = True
    move_command = False

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):
        if alpha is not None and beta is not None:
            assert alpha >= 0 and beta >= 0, "invalid speed."

            data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def encode(alpha, beta):
        """Encodes the alpha and beta speed as bytes."""

        data_speed = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))

        return data_speed


class SetCurrent(Command):
    """Sets the current of the alpha and beta motors."""

    command_id = CommandID.SET_CURRENT
    broadcastable = False
    safe = True
    move_command = True

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):
        if alpha is not None and beta is not None:
            assert alpha >= 0 and beta >= 0, "invalid current."

            data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


async def goto(
    fps: FPS,
    new_positions: dict[int, tuple[float, float]],
    speed: Optional[float] = None,
    relative: bool = False,
    use_sync_line: bool | None = None,
    go_cowboy: bool = False,
    force: bool = False,
    command: CluCommand[JaegerActor] | None = None,
):
    """Send positioners to a given position using a trajectory with ``kaiju`` check.

    Parameters
    ----------
    fps
        The `.FPS` instance.
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
    force
        If ``go_cowboy=False`` and the trajectory is deadlocked, a `.TrajectoryError`
        will be raised. Use ``force=True`` to apply the trajectory anyway.
    command
        A command to pass to `.send_trajectory` to output additional information.

    """

    if fps.locked:
        FPSLockedError("The FPS is locked.")

    if fps.moving:
        raise JaegerError("FPS is moving. Cannot send goto.")

    for pid in new_positions:
        if pid not in fps.positioners:
            raise JaegerError(f"Positioner ID {pid} is not connected.")

    speed = float(speed or config["positioner"]["motor_speed"])
    if speed < 500 or speed > 5000:
        raise JaegerError("Invalid speed.")

    positioner_ids = list(new_positions.keys())
    await fps.update_position(positioner_ids=positioner_ids)

    trajectories = {}

    if go_cowboy is True:
        for pid in positioner_ids:
            pos = fps[pid]

            if pos.alpha is None or pos.beta is None:
                raise JaegerError(
                    f"Positioner {pid}: cannot goto with unknown position."
                )

            current_alpha = pos.alpha
            current_beta = pos.beta

            if relative is True:
                alpha_end = current_alpha + new_positions[pid][0]
                beta_end = current_beta + new_positions[pid][1]
            else:
                alpha_end = new_positions[pid][0]
                beta_end = new_positions[pid][1]

            alpha_delta = abs(alpha_end - current_alpha)
            beta_delta = abs(beta_end - current_beta)

            time_end = [
                get_goto_move_time(alpha_delta, speed=speed),
                get_goto_move_time(beta_delta, speed=speed),
            ]

            trajectories[pid] = {
                "alpha": [(current_alpha, 0.1), (alpha_end, time_end[0] + 0.1)],
                "beta": [(current_beta, 0.1), (beta_end, time_end[1] + 0.1)],
            }

    else:
        if relative is True:
            raise JaegerError("relative is not implemented for kaiju moves.")

        data = {"collision_buffer": None, "grid": {}}

        for pid, (current_alpha, current_beta) in fps.get_positions_dict().items():
            if current_alpha is None or current_beta is None:
                raise JaegerError(f"Positioner {pid} does not know its position.")

            if pid in new_positions:
                data["grid"][int(pid)] = (
                    current_alpha,
                    current_beta,
                    new_positions[pid][0],
                    new_positions[pid][1],
                    fps[pid].disabled,
                )
            else:
                data["grid"][int(pid)] = (
                    current_alpha,
                    current_beta,
                    current_alpha,
                    current_beta,
                    fps[pid].disabled,
                )

        (to_destination, _, did_fail, deadlocks) = await run_in_executor(
            get_path_pair,
            data=data,
            path_generation_mode="greedy",
            stop_if_deadlock=force,
            executor="process",
            ignore_did_fail=force,
        )

        if did_fail is True:
            if force is False:
                raise TrajectoryError(
                    "Cannot execute trajectory. Found "
                    f"{len(deadlocks)} deadlocks ({deadlocks})."
                )
            else:
                warnings.warn(
                    f"Found {len(deadlocks)} deadlocks but applying trajectory.",
                    JaegerUserWarning,
                )

        trajectories = to_destination

    return await send_trajectory(
        fps,
        trajectories,
        use_sync_line=use_sync_line,
        command=command,
        extra_dump_data={"kaiju_trajectory": not go_cowboy},
    )
