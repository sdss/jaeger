#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-28
# @Filename: kaiju.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import warnings

from typing import TYPE_CHECKING, Optional

from jaeger import config, log
from jaeger.exceptions import JaegerError, JaegerUserWarning, TrajectoryError
from jaeger.fps import FPS
from jaeger.utils.helpers import run_in_executor


if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from kaiju import RobotGridCalib


__all__ = [
    "get_robot_grid",
    "dump_robot_grid",
    "decollide",
    "get_path_pair",
    "get_snapshot",
    "unwind",
    "explode",
    "get_path_pair_in_executor",
    "decollide_in_executor",
]


def warn(message):
    warnings.warn(message, JaegerUserWarning)


def get_robot_grid(seed: int = 0, collision_buffer=None):
    """Returns a new robot grid with the destination set to the lattice position.

    If an initialised instance of the FPS is available, disabled robots will be
    set offline in the grid at their current positions.

    """

    fps = FPS.get_instance()
    if fps is None:
        warn(
            "FPS information not provided when creating the robot grid. "
            "Will not be able to disable robots."
        )

    from kaiju.robotGrid import RobotGridCalib

    kaiju_config = config["kaiju"]
    ang_step = kaiju_config["ang_step"]
    collision_buffer = collision_buffer or kaiju_config["collision_buffer"]
    alpha0, beta0 = kaiju_config["lattice_position"]
    epsilon = ang_step * kaiju_config["epsilon_factor"]

    if collision_buffer < 1.5:
        raise JaegerError("Invalid collision buffer < 1.5.")

    robot_grid = RobotGridCalib(stepSize=ang_step, epsilon=epsilon, seed=seed)
    robot_grid.setCollisionBuffer(collision_buffer)

    # TODO: This is a bit hacky. Kaiju doesn't have a collisionBuffer anymore
    # as collision buffers are per robot, but I want to keep this information
    # for when I dump and reload robot grids.
    robot_grid.collisionBuffer = collision_buffer

    if fps is not None and set(robot_grid.robotDict.keys()) != set(fps.keys()):
        raise JaegerError("Mismatch between connected positioners and robot grid.")

    for robot in robot_grid.robotDict.values():
        if fps is not None:
            positioner = fps[robot.id]
            if positioner.disabled:
                log.debug(f"Setting positioner {robot.id} offline in Kaiju.")
                robot.setAlphaBeta(positioner.alpha, positioner.beta)
                robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)
                robot.isOffline = True
                continue

        robot.setDestinationAlphaBeta(alpha0, beta0)

    return robot_grid


def dump_robot_grid(robot_grid: RobotGridCalib) -> dict:
    """Dump the information needed to restore a robot grid into a dictionary."""

    data = {}

    data["collision_buffer"] = robot_grid.collisionBuffer
    data["grid"] = {}

    for robot in robot_grid.robotDict.values():
        alpha = robot.alpha
        beta = robot.beta
        destinationAlpha = robot.destinationAlpha
        destinationBeta = robot.destinationBeta

        data["grid"][robot.id] = (alpha, beta, destinationAlpha, destinationBeta)

    return data


def load_robot_grid(data: dict, set_destination: bool = True) -> RobotGridCalib:
    """Restores a robot grid from a dump."""

    collision_buffer = data["collision_buffer"]
    robot_grid = get_robot_grid(collision_buffer=collision_buffer)

    for robot in robot_grid.robotDict.values():
        data_robot = data["grid"][robot.id]
        robot.setAlphaBeta(data_robot[0], data_robot[1])
        if set_destination:
            robot.setDestinationAlphaBeta(data_robot[2], data_robot[3])

    return robot_grid


def decollide(
    robot_grid: Optional[RobotGridCalib] = None,
    data: Optional[dict] = None,
    simple: bool = False,
) -> RobotGridCalib | dict:
    """Decollides a potentially collided grid. Raises on fail.

    Parameters
    ----------
    robot_grid
        The Kaiju ``RobotGridCalib`` instance to decollide.
    data
        A dictionary of data that can be used to reload a Kaiju robot grid
        using `.load_robot_grid`. This is useful if the function is being
        run in an executor.
    simple
        Runs ``decollideGrid()`` and returns.

    Returns
    -------
    grid
        If ``robot_grid`` is passed, returns the same grid instance after decollision.
        If ``data`` is passed, returns a dictionary describing the decollided grid
        that can be used to recreate a grid using `.load_robot_grid`.

    """

    def get_collided(robot_grid):
        collided = [rid for rid in robot_grid.robotDict if robot_grid.isCollided(rid)]
        if len(collided) == 0:
            return False
        else:
            return collided

    if robot_grid is not None and data is not None:
        raise JaegerError("robot_grid and data are mutually exclusive.")

    if data is not None:
        robot_grid = load_robot_grid(data)

    assert robot_grid is not None

    if simple:
        robot_grid.decollideGrid()
        if get_collided(robot_grid) is not False:
            raise JaegerError("Failed decolliding grid.")

        if data is not None:
            return dump_robot_grid(robot_grid)
        else:
            return robot_grid

    # First pass. If collided, decollide each robot one by one.
    # TODO: Probably this should be done in order of less to more important targets
    # to throw out the less critical ones first.
    collided = get_collided(robot_grid)
    if collided is not False:
        warn("The grid is collided. Attempting one-by-one decollision.")
        for robot_id in collided:
            if robot_grid.isCollided(robot_id):
                robot_grid.decollideRobot(robot_id)
                if robot_grid.isCollided(robot_id):
                    warn(f"Failed decolliding positioner {robot_id}.")
                else:
                    warn(f"Positioner {robot_id} was decollided.")

    # Second pass. If still collided, try a grid decollision.
    if get_collided(robot_grid) is not False:
        warn("Grid is still colliding. Attempting full grid decollision.")
        robot_grid.decollideGrid()
        if get_collided(robot_grid) is not False:
            raise JaegerError("Failed decolliding grid.")
        else:
            warn("The grid was decollided.")

    if data is not None:
        return dump_robot_grid(robot_grid)
    else:
        return robot_grid


def get_path_pair(
    robot_grid: Optional[RobotGridCalib] = None,
    data: Optional[dict] = None,
    path_generation_mode: str = "greedy",
    ignore_did_fail: bool = False,
    escape_deg: float = 5,
    speed=None,
    smooth_points=None,
    path_delay=None,
    collision_shrink=None,
) -> tuple:
    """Runs ``pathGenGreedy`` and returns the to and from destination paths.

    Parameters
    ----------
    robot_grid
        The Kaiju ``RobotGridCalib`` instance to decollide.
    data
        A dictionary of data that can be used to reload a Kaiju robot grid
        using `.load_robot_grid`. This is useful if the function is being
        run in an executor.
    path_generation_mode
        Defines the path generation algorithm to use.
        Either ``greedy`` or ``escape``.
    ignore_did_fail
        Generate paths even if path generation failed (i.e., deadlocks).
    escape_deg
        Degrees for ``pathGenEscape``.
    speed, smooth_points, path_delay, collision_shrink
        Kaiju parameters to pass to ``getPathPair``. Otherwise uses the default
        configuration values.

    Returns
    -------
    paths
        A tuple with the to destination path, from destination path, whether path
        generation failed, and the list of deadlocks

    """

    if robot_grid is not None and data is not None:
        raise JaegerError("robot_grid and data are mutually exclusive.")

    if data is not None:
        set_destination = False if path_generation_mode == "escape" else True
        robot_grid = load_robot_grid(data, set_destination=set_destination)

    assert robot_grid is not None

    if path_generation_mode == "escape":
        robot_grid.pathGenEscape(escape_deg)
        deadlocks = []
    else:
        robot_grid.pathGenGreedy()

        # Check for deadlocks.
        deadlocks = robot_grid.deadlockedRobots()
        if robot_grid.didFail and ignore_did_fail is False:
            return (None, None, robot_grid.didFail, deadlocks)

    speed = speed or config["kaiju"]["speed"]
    smooth_points = smooth_points or config["kaiju"]["smooth_points"]
    collision_shrink = collision_shrink or config["kaiju"]["collision_shrink"]
    path_delay = path_delay or config["kaiju"]["path_delay"]

    to_destination, from_destination = robot_grid.getPathPair(
        speed=speed,
        smoothPoints=smooth_points,
        collisionShrink=collision_shrink,
        pathDelay=path_delay,
    )

    return (
        to_destination,
        from_destination,
        robot_grid.didFail,
        deadlocks,
    )


async def get_path_pair_in_executor(robot_grid: RobotGridCalib, **kwargs):
    """Calls `.get_path_pair` with a process executor."""

    data = dump_robot_grid(robot_grid)
    traj_data = await run_in_executor(
        get_path_pair,
        data=data,
        executor="process",
        **kwargs,
    )

    return traj_data


async def decollide_in_executor(robot_grid: RobotGridCalib, **kwargs) -> RobotGridCalib:
    """Calls `.decollide` with a process executor."""

    data = dump_robot_grid(robot_grid)
    decollided_data = await run_in_executor(
        decollide,
        data=data,
        executor="process",
        **kwargs,
    )

    return load_robot_grid(decollided_data)


async def unwind(
    current_positions: dict[int, tuple[float | None, float | None]],
    collision_buffer: float | None = None,
    force: bool = False,
):
    """Folds all the robots to the lattice position.

    This coroutine uses a process pool executor to run Kaiju routines.

    """

    alpha0, beta0 = config["kaiju"]["lattice_position"]

    # We create the data directly since it's simple. This should be a bit faster
    # than creating a grid and dumping it.
    data = {"collision_buffer": collision_buffer, "grid": {}}
    for pid, (alpha, beta) in current_positions.items():
        data["grid"][int(pid)] = (alpha, beta, alpha0, beta0)

    (to_destination, _, did_fail, deadlocks) = await run_in_executor(
        get_path_pair,
        data=data,
        ignore_did_fail=force,
        executor="process",
    )
    if did_fail:
        if force is False:
            raise TrajectoryError(
                "Failed generating a valid unwind trajectory. "
                f"{len(deadlocks)} deadlocks were found."
            )
        else:
            log.warning("Deadlocks found in unwind but proceeding anyway.")

    return to_destination


async def explode(
    current_positions: dict[int, tuple[float | None, float | None]],
    explode_deg=20.0,
    collision_buffer: float | None = None,
):
    """Explodes the grid by a number of degrees.

    This coroutine uses a process pool executor to run Kaiju routines.

    """

    alpha0, beta0 = config["kaiju"]["lattice_position"]

    data = {"collision_buffer": collision_buffer, "grid": {}}
    for pid, (alpha, beta) in current_positions.items():
        data["grid"][int(pid)] = (alpha, beta, alpha0, beta0)

    (to_destination, *_) = await run_in_executor(
        get_path_pair,
        data=data,
        path_generation_mode="escape",
        escape_deg=explode_deg,
        ignore_did_fail=False,
        executor="process",
    )

    return to_destination


async def get_snapshot(
    fps: FPS | None = None,
    collision_buffer: float | None = None,
    highlight: int | None = None,
) -> Axes:
    """Returns matplotlib axes with the current arrangement of the FPS array."""

    fps = fps or FPS.get_instance()
    if fps.initialised is False:
        await fps.initialise()

    await fps.update_position()

    # Create a robot grid and set the current positions.
    robot_grid = get_robot_grid(collision_buffer=collision_buffer)

    for robot in robot_grid.robotDict.values():
        if robot.id not in fps.positioners.keys():
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot.setAlphaBeta(fps[robot.id].alpha, fps[robot.id].beta)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".+array interface is deprecated.+")
        ax: Axes = await run_in_executor(
            robot_grid.plot_state,
            highlightRobot=highlight,
        )

    return ax
