#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-08
# @Filename: trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import pathlib
import time
import warnings

from typing import TYPE_CHECKING, Dict, List, Tuple, cast

import numpy

import drift
from sdsstools import read_yaml_file

from jaeger import config, log
from jaeger.commands import Command, CommandID
from jaeger.exceptions import FPSLockedError, JaegerUserWarning, TrajectoryError
from jaeger.ieb import IEB
from jaeger.maskbits import FPSStatus, PositionerStatus, ResponseCode
from jaeger.utils import int_to_bytes


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = [
    "send_trajectory",
    "SendNewTrajectory",
    "SendTrajectoryData",
    "TrajectoryDataEnd",
    "SendTrajectoryAbort",
    "StartTrajectory",
    "StopTrajectory",
    "Trajectory",
]


MOTOR_STEPS = config["positioner"]["motor_steps"]
TIME_STEP = config["positioner"]["time_step"]


TrajectoryDataType = Dict[int, Dict[str, List[Tuple[float, float]]]]


async def send_trajectory(
    fps: FPS,
    trajectories: str | pathlib.Path | TrajectoryDataType,
    use_sync_line: bool | None = None,
    send_trajectory=True,
    start_trajectory=True,
) -> Trajectory:
    """Sends a set of trajectories to the positioners.

    This is a low-level function that raises errors when a problem is
    encountered. Most users should use `.FPS.send_trajectory` instead.
    `.send_trajectory` automates `.Trajectory` by calling the different
    methods in order, but provides less control.

    Parameters
    ----------
    fps
        The instance of `.FPS` that will receive the trajectory.
    trajectories
        Either a path to a YAML file to read or a dictionary with the
        trajectories. In either case the format must be a dictionary in
        which the keys are the ``positioner_ids`` and each value is a
        dictionary containing two keys: ``alpha`` and ``beta``, each
        pointing to a list of tuples ``(position, time)``, where
        ``position`` is in degrees and ``time`` is in seconds.
    use_sync_line
        If `True`, the SYNC line will be used to synchronise the beginning of
        all trajectories. Otherwise a `.START_TRAJECTORY` command will be sent
        over the CAN network. If `None`, defaults to the configuration parameter.
    send_trajectory
        If `True`, sends the trajectory to the positioners and returns the
        `.Trajectory` instance.
    start_trajectory
        If `True`, runs the trajectory after sending it. Otherwise, returns
        the `.Trajectory` instance after sending the data. Ignored if
        `send_trajectory=False`.

    Raises
    ------
    TrajectoryError
        If encounters a problem sending the trajectory.
    FPSLockedError
        If the FPS is locked.

    Examples
    --------
    ::

        >>> fps = FPS()
        >>> await fps.initialise()

        # Send a trajectory with two points for positioner 4
        >>> await fps.send_trajectory({4: {'alpha': [(90, 0), (91, 3)],
                                           'beta': [(20, 0), (23, 4)]}})

    """

    traj = Trajectory(fps, trajectories)

    if use_sync_line is None:
        use_sync_line = config["fps"]["use_sync_line"]

    if use_sync_line:
        if not isinstance(fps.ieb, IEB) or fps.ieb.disabled:
            raise TrajectoryError("IEB is not connected. Cannot use SYNC line.", traj)
        if (await fps.ieb.get_device("sync").read())[0] == "closed":
            raise TrajectoryError("The SYNC line is on high.", traj)

    if send_trajectory is False:
        return traj

    log.debug("sending trajectory data.")

    try:
        await traj.send()
    except TrajectoryError as err:
        raise TrajectoryError(
            f"Something went wrong sending the trajectory: {err}",
            err.trajectory,
        )

    log.debug(f"Trajectory sent in {traj.data_send_time:1f} seconds.")
    log.info(f"Expected time to complete trajectory: {traj.move_time:.2f} seconds.")

    if start_trajectory is False:
        return traj

    log.info("starting trajectory ...")
    try:
        await traj.start(use_sync_line=use_sync_line)
    except TrajectoryError as err:

        if traj.start_time is not None and traj.end_time is not None:
            elapsed = traj.end_time - traj.start_time
            elapsed_msg = f" Trajectory failed {elapsed:.2f} seconds after start."
        else:
            elapsed_msg = ""

        raise TrajectoryError(
            f"Something went wrong during the trajectory: {err}.{elapsed_msg}",
            err.trajectory,
        )

    log.info("All positioners have reached their destinations.")

    return traj


class Trajectory(object):
    """Prepares and sends a trajectory to the FPS.

    Most user will prefer using `.FPS.send_trajectory`, which automates the
    process. This class provides fine-grain control over the process.

    Parameters
    ----------
    fps : .FPS
        The instance of `.FPS` that will receive the trajectory.
    trajectories : `str` or `dict`
        Either a path to a YAML file to read or a dictionary with the
        trajectories. In either case the format must be a dictionary in
        which the keys are the ``positioner_ids`` and each value is a
        dictionary containing two keys: ``alpha`` and ``beta``, each
        pointing to a list of tuples ``(position, time)``, where
        ``position`` is in degrees and ``time`` is in seconds.

    Raises
    ------
    TrajectoryError
        If encounters a problem sending the trajectory.
    FPSLockedError
        If the FPS is locked.

    Examples
    --------
    Given the following two-point trajectory for positioner 4 ::

        points = {4: {'alpha': [(90, 0), (91, 3)],
                      'beta': [(20, 0), (23, 4)]}}

    the normal process to execute the trajectory is ::

        trajectory = Trajectory(fps, points)
        await trajectory.send()    # Sends the trajectory but does not yet execute it.
        await trajectory.start()   # This starts the trajectory (positioners move).

    """

    def __init__(
        self,
        fps: FPS,
        trajectories: str | pathlib.Path | TrajectoryDataType,
    ):

        self.fps = fps
        self.trajectories: TrajectoryDataType

        if self.fps.locked:
            raise FPSLockedError(
                f"FPS is locked by {fps.locked_by}. Cannot send trajectories."
            )

        if self.fps.moving:
            raise TrajectoryError(
                "The FPS is moving. Cannot send new trajectory.",
                self,
            )

        if isinstance(trajectories, (str, pathlib.Path)):
            self.trajectories = cast(TrajectoryDataType, read_yaml_file(trajectories))
        elif isinstance(trajectories, dict):
            self.trajectories = trajectories
        else:
            raise TrajectoryError("invalid trajectory data.", self)

        # List of positioners that failed receiving the trajectory and reason.
        self.failed_positioners: dict[int, str] = {}

        self.validate()

        #: Number of points sent to each positioner as a tuple ``(alpha, beta)``.
        self.n_points = {}

        #: The time required to complete the trajectory.
        self.move_time: float | None = None

        #: How long it took to send the trajectory.
        self.data_send_time: float | None = None

        self.failed = False
        self.send_new_trajectory_failed = False

        # Commands that will be sent. Mostly for inspection if the trajectory fails.
        self.data_send_cmd: Command | None = None
        self.end_traj_cmds: Command | None = None

        self.start_time: float | None = None
        self.end_time: float | None = None

        self._ready_to_start = False

    def validate(self):
        """Validates the trajectory."""

        if len(self.trajectories) == 0:
            raise TrajectoryError("trajectory is empty.", self)

        if len(self.trajectories) != len(numpy.unique(list(self.trajectories))):
            raise TrajectoryError("Duplicate positioner trajectories.", self)

        for pid in self.trajectories:
            trajectory = self.trajectories[pid]

            if "alpha" not in trajectory or "beta" not in trajectory:
                self.failed_positioners[pid] = "NO_DATA"
                raise TrajectoryError(
                    f"Positioner {pid} missing alpha or beta data.",
                    self,
                )

            for arm in ["alpha", "beta"]:
                data = numpy.array(list(zip(*trajectory[arm]))[0])

                # if numpy.any(data > 360) or numpy.any(data < 0):
                #     raise TrajectoryError(
                #         f"Positioner {pid}: trajectory has points out of range.",
                #         self,
                #     )

                if arm == "beta":
                    if config.get("safe_mode", False):
                        if config["safe_mode"] is True:
                            min_beta = 160
                        else:
                            min_beta = config["safe_mode"]["min_beta"]
                        if numpy.any(data < min_beta):
                            self.failed_positioners[pid] = "SAFE_MODE"
                            raise TrajectoryError(
                                f"Positioner {pid}: safe mode is on "
                                f"and beta < {min_beta}.",
                                self,
                            )

    async def send(self):
        """Sends the trajectory but does not start it."""

        self.move_time = 0.0

        await self.fps.stop_trajectory()

        if not await self.fps.update_status(
            positioner_ids=list(self.trajectories),
            timeout=1.0,
        ):
            self.failed = True
            raise TrajectoryError("Some positioners did not respond.", self)

        log.debug("Sending data points:")

        # Check that all positioners are ready to receive a new trajectory.
        for pos_id in self.trajectories:

            positioner = self.fps.positioners[pos_id]
            status = positioner.status

            if positioner.disabled:
                self.failed = True
                raise TrajectoryError(
                    f"positioner_id={pos_id} is disabled but was "
                    "included in the trajectory.",
                    self,
                )

            if (
                positioner.flags.DATUM_ALPHA_INITIALIZED not in status
                or positioner.flags.DATUM_BETA_INITIALIZED not in status
                or positioner.flags.DISPLACEMENT_COMPLETED not in status
            ):
                self.failed = True
                self.failed_positioners[pos_id] = "NOT_READY"
                raise TrajectoryError(
                    f"positioner_id={pos_id} is not ready to receive a trajectory.",
                    self,
                )

            traj_pos = self.trajectories[pos_id]

            log.debug(f"{pos_id}: {traj_pos}")

            self.n_points[pos_id] = (len(traj_pos["alpha"]), len(traj_pos["beta"]))

            # Gets maximum time for this positioner
            max_time_pos = max(
                [
                    max(list(zip(*traj_pos["alpha"]))[1]),
                    max(list(zip(*traj_pos["beta"]))[1]),
                ]
            )

            # Updates the global trajectory max time.
            if max_time_pos > self.move_time:
                self.move_time = max_time_pos

        new_traj_data = {}
        for pos_id in self.trajectories:
            data = SendNewTrajectory.get_data(
                self.n_points[pos_id][0],
                self.n_points[pos_id][1],
            )
            new_traj_data[pos_id] = data

        # Starts trajectory
        new_traj_cmd = await self.fps.send_command(
            "SEND_NEW_TRAJECTORY",
            positioner_ids=list(self.trajectories),
            data=new_traj_data,
        )

        if new_traj_cmd.status.failed or new_traj_cmd.status.timed_out:
            self.failed = True
            raise TrajectoryError("Failed sending SEND_NEW_TRAJECTORY.", self)

        start_trajectory_send_time = time.time()

        # How many points from the trajectory are we putting in each command.
        n_chunk = config["positioner"]["trajectory_data_n_points"]

        # Gets the maximum number of points for each arm for all positioners.
        max_points = numpy.max(list(self.n_points.values()), axis=0)
        max_points = {"alpha": max_points[0], "beta": max_points[1]}

        # Send chunks of size n_chunk to all the positioners in parallel.
        # Do alpha first, then beta.
        for arm in ["alpha", "beta"]:

            for jj in range(0, max_points[arm], n_chunk):

                data = {}
                send_trajectory_pids = []

                for pos_id in self.trajectories:

                    arm_chunk = self.trajectories[pos_id][arm][jj : jj + n_chunk]
                    if len(arm_chunk) == 0:
                        continue

                    send_trajectory_pids.append(pos_id)

                    positions = numpy.array(arm_chunk).astype(numpy.float64)
                    data_pos = SendTrajectoryData.calculate_positions(positions)

                    data[pos_id] = data_pos

                self.data_send_cmd = await self.fps.send_command(
                    "SEND_TRAJECTORY_DATA",
                    positioner_ids=send_trajectory_pids,
                    data=data,
                )

                status = self.data_send_cmd.status
                if status.failed or status.timed_out:
                    for reply in self.data_send_cmd.replies:
                        if reply.response_code != ResponseCode.COMMAND_ACCEPTED:
                            code = reply.response_code.name
                            self.failed_positioners[reply.positioner_id] = code
                    self.failed = True
                    raise TrajectoryError(
                        "At least one SEND_TRAJECTORY_COMMAND failed.",
                        self,
                    )

        # Finalise the trajectories
        self.end_traj_cmds = await self.fps.send_command(
            "TRAJECTORY_DATA_END",
            positioner_ids=list(self.trajectories.keys()),
        )

        for cmd in self.end_traj_cmds:
            if cmd.status.failed:
                self.failed = True
                raise TrajectoryError("TRAJECTORY_DATA_END failed.", self)

            if ResponseCode.INVALID_TRAJECTORY in cmd.replies[0].response_code:
                self.failed = True
                raise TrajectoryError(
                    f"positioner_id={cmd.positioner_id} got an "
                    f"INVALID_TRAJECTORY reply.",
                    self,
                )

        self.data_send_time = time.time() - start_trajectory_send_time

        self._ready_to_start = True
        self.failed = False

        return True

    async def start(self, use_sync_line=True):
        """Starts the trajectory."""

        if not self._ready_to_start or self.failed:
            raise TrajectoryError("The trajectory has not been sent.", self)

        if self.move_time is None:
            raise TrajectoryError("move_time not set.", self)

        if use_sync_line:

            if not isinstance(self.fps.ieb, IEB) or self.fps.ieb.disabled:
                raise TrajectoryError(
                    "IEB is not connected. Cannot use SYNC line.",
                    self,
                )
            if (await self.fps.ieb.get_device("sync").read())[0] == "closed":
                raise TrajectoryError("The SYNC line is on high.", self)

            sync = self.fps.ieb.get_device("sync")
            assert isinstance(sync, drift.Relay)

            # Set SYNC line to high.
            await sync.close()

            # Schedule reseting of SYNC line
            asyncio.get_event_loop().call_later(0.5, asyncio.create_task, sync.open())

        else:

            # Start trajectories
            command = await self.fps.send_command(
                "START_TRAJECTORY",
                positioner_ids=0,
                timeout=1,
                # All positioners reply, including those not in the trajectory.
                n_positioners=len(self.fps.positioners),
            )

            if command.status.failed:
                await self.fps.stop_trajectory()
                self.failed = True
                raise TrajectoryError("START_TRAJECTORY failed", self)

        restart_pollers = True if self.fps.pollers.running else False
        await self.fps.pollers.stop()

        self.start_time = time.time()

        min_trajectory_time = 2.0
        PS = PositionerStatus

        try:

            # The positioners take a bit to report that they are moving so if the
            # move time is too short, we don't try to check if the positioners started
            # moving (but we'll check later that they arrived to their positions.)
            if self.move_time >= min_trajectory_time:
                await asyncio.sleep(min_trajectory_time)
                await self.fps.update_status()

                if self.fps.status & FPSStatus.IDLE:
                    raise TrajectoryError("Move failed to start.")

                sbits = numpy.array([p.status for p in self.fps.values()])
                not_moving = numpy.where(sbits & PS.DISPLACEMENT_COMPLETED)
                if not_moving[0].any():
                    not_moving_pids = numpy.array(list(self.fps))[not_moving[0]]

                    # Before reporting that the positioner is not moving, check if it's
                    # already there.
                    await self.fps.update_position()
                    positions = self.fps.get_positions()

                    really_not_moving = []
                    for pid in not_moving_pids:
                        if pid not in self.trajectories:
                            continue

                        current = positions[positions[:, 0] == pid][0, 1:]
                        alpha = self.trajectories[pid]["alpha"][-1][0]
                        beta = self.trajectories[pid]["beta"][-1][0]

                        if not numpy.allclose(current - [alpha, beta], 0, atol=0.1):
                            really_not_moving.append(pid)

                    if len(really_not_moving) > 0:
                        not_moving_str = ", ".join(map(str, really_not_moving))
                        # Should this be an error?
                        warnings.warn(
                            "Some positioners do not appear to be "
                            f"moving: {not_moving_str}.",
                            JaegerUserWarning,
                        )

            while True:

                await asyncio.sleep(1)

                if self.fps.locked:
                    raise TrajectoryError(
                        "The FPS got locked during the trajectory.",
                        self,
                    )

                await self.fps.update_status()

                if self.fps.status & FPSStatus.IDLE:
                    self.failed = False
                    break

                elapsed = time.time() - self.start_time
                if elapsed > (self.move_time + 3):
                    raise TrajectoryError(
                        "Some positioners did not complete the move.",
                        self,
                    )

            # TODO: There seems to be bug in the firmware. Sometimes when a positioner
            # fails to start its trajectory, at the end of the trajectory time it
            # does believe it has reached the commanded position, although it's still
            # at the initial position. In those cases issuing a SEND_TRAJECTORY_ABORT
            # followed by a position update seems to return correct positions.
            await self.fps.stop_trajectory()

            # The FPS says they have all stopped moving but check that they are
            # actually at their positions.
            await self.fps.update_position()
            failed_reach = False
            for pid in self.trajectories:
                alpha = self.trajectories[pid]["alpha"][-1][0]
                beta = self.trajectories[pid]["beta"][-1][0]
                current_position = numpy.array(self.fps[pid].position)
                if not numpy.allclose(current_position, [alpha, beta], atol=0.1):
                    warnings.warn(
                        f"Positioner {pid} may not have reached its destination.",
                        JaegerUserWarning,
                    )
                    failed_reach = True

            if failed_reach:
                raise TrajectoryError(
                    "Some positioners did not reach their destinations.",
                    self,
                )

        except BaseException:
            self.failed = True
            await self.fps.stop_trajectory()
            raise

        finally:
            await self.fps.stop_trajectory()
            await self.fps.update_position()
            self.end_time = time.time()
            if restart_pollers:
                self.fps.pollers.start()

        return True

    async def abort_trajectory(self):
        """Aborts the trajectory transmission."""

        cmd = await self.fps.send_command(
            "SEND_TRAJECTORY_ABORT",
            positioner_ids=list(self.trajectories.keys()),
        )

        if cmd.status.failed:
            raise TrajectoryError("Cannot abort trajectory transmission.", self)


class SendNewTrajectory(Command):
    """Starts a new trajectory and sends the number of points."""

    command_id = CommandID.SEND_NEW_TRAJECTORY
    broadcastable = False
    move_command = True

    def __init__(self, positioner_ids, n_alpha=None, n_beta=None, **kwargs):

        if n_alpha is not None and n_beta is not None:
            kwargs["data"] = self.get_data(n_alpha, n_beta)
        else:
            assert "data" in kwargs, "n_alpha/n_beta or data need to be passed."

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def get_data(n_alpha, n_beta):
        """Returns the data bytearray from a pair for ``(n_alpha, n_beta)``."""

        alpha_positions = int(n_alpha)
        beta_positions = int(n_beta)

        assert alpha_positions > 0 and beta_positions > 0

        data = int_to_bytes(alpha_positions) + int_to_bytes(beta_positions)

        return data


class SendTrajectoryData(Command):
    """Sends a data point in the trajectory.

    This command sends multiple messages that represent a full trajectory.

    Parameters
    ----------
    positions : list
        A list of tuples in which the first element is the angle, in degrees,
        and the second is the associated time, in seconds.

    Examples
    --------
        >>> SendTrajectoryData([(90, 10), (88, 12), (80, 20)])

    """

    command_id = CommandID.SEND_TRAJECTORY_DATA
    broadcastable = False
    move_command = True

    def __init__(self, positioner_ids, positions=None, **kwargs):

        if positions is not None:
            kwargs["data"] = self.calculate_positions(positions)
        else:
            assert "data" in kwargs, "positions or data need to be passed."

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def calculate_positions(positions):
        """Converts angle-time posions to bytes data."""

        positions = numpy.array(positions).astype(numpy.float64)

        positions[:, 0] = positions[:, 0] / 360.0 * MOTOR_STEPS
        positions[:, 1] /= TIME_STEP

        positions = positions.astype(numpy.int32)

        data = []
        for angle, tt in positions:
            data.append(int_to_bytes(angle, dtype="i4") + int_to_bytes(tt, dtype="i4"))

        return data


class TrajectoryDataEnd(Command):
    """Indicates that the transmission for the trajectory has ended."""

    command_id = CommandID.TRAJECTORY_DATA_END
    broadcastable = False
    move_command = True


class SendTrajectoryAbort(Command):
    """Aborts sending a trajectory."""

    command_id = CommandID.SEND_TRAJECTORY_ABORT
    broadcastable = False
    move_command = False
    safe = True


class StartTrajectory(Command):
    """Starts the trajectories."""

    command_id = CommandID.START_TRAJECTORY
    broadcastable = True
    move_command = True
    safe = True


class StopTrajectory(Command):
    """Stop the trajectories."""

    command_id = CommandID.STOP_TRAJECTORY
    broadcastable = True
    safe = True
