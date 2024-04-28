#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-28
# @Filename: kaiju.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import time
import warnings

from typing import TYPE_CHECKING, Literal, Optional, Sequence, cast

import numpy
from matplotlib.figure import Figure

from jaeger import config, log
from jaeger.exceptions import JaegerError, JaegerUserWarning, TrajectoryError
from jaeger.utils.helpers import run_in_executor


if TYPE_CHECKING:
    from kaiju import RobotGridCalib

    from jaeger.fps import FPS


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
    "check_trajectory",
]


ArmTrajectoryType = Sequence[tuple[float, float]]
TrajectoryType = dict[int, dict[Literal["alpha", "beta"], ArmTrajectoryType]] | None
PathPairReturnType = tuple[TrajectoryType, TrajectoryType, bool, list[int]]


def warn(message):
    warnings.warn(message, JaegerUserWarning)


def get_robot_grid(fps: FPS | None, seed: int | None = None, collision_buffer=None):
    """Returns a new robot grid with the destination set to the lattice position.

    If an initialised instance of the FPS is available, disabled robots will be
    set offline in the grid at their current positions.

    """

    from kaiju.robotGrid import RobotGridCalib

    if seed is None:
        t = 1000 * time.time()
        seed = int(int(t) % 2**32 / 1000)

    kaiju_config = config["kaiju"]
    ang_step = kaiju_config["ang_step"]
    collision_buffer = collision_buffer or kaiju_config["collision_buffer"]
    alpha0, beta0 = kaiju_config["lattice_position"]
    epsilon = ang_step * kaiju_config["epsilon_factor"]

    if collision_buffer < 1.5:
        raise JaegerError("Invalid collision buffer < 1.5.")

    log.debug(f"Creating RobotGridCalib with stepSize={ang_step}, epsilon={epsilon}.")

    robot_grid = RobotGridCalib(stepSize=ang_step, epsilon=epsilon, seed=seed)
    robot_grid.setCollisionBuffer(collision_buffer)

    # TODO: This is a bit hacky. Kaiju doesn't have a collisionBuffer anymore
    # as collision buffers are per robot, but I want to keep this information
    # for when I dump and reload robot grids.
    robot_grid.collisionBuffer = collision_buffer

    for robot in robot_grid.robotDict.values():
        if fps is not None and robot.id in fps and fps[robot.id].disabled is True:
            positioner = fps[robot.id]
            robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)
            robot.setAlphaBeta(positioner.alpha, positioner.beta)
            robot.isOffline = True
        else:
            robot.setDestinationAlphaBeta(alpha0, beta0)
            robot.setAlphaBeta(alpha0, beta0)

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

        data["grid"][robot.id] = (
            alpha,
            beta,
            destinationAlpha,
            destinationBeta,
            robot.isOffline,
        )

    return data


def load_robot_grid(data: dict, set_destination: bool = True) -> RobotGridCalib:
    """Restores a robot grid from a dump."""

    collision_buffer = data["collision_buffer"]
    robot_grid = get_robot_grid(None, collision_buffer=collision_buffer)

    for robot in robot_grid.robotDict.values():
        data_robot = data["grid"][robot.id]
        robot.setAlphaBeta(data_robot[0], data_robot[1])
        if set_destination:
            robot.setDestinationAlphaBeta(data_robot[2], data_robot[3])
        if data_robot[4] is True:
            robot.isOffline = True

    return robot_grid


def decollide(
    robot_grid: Optional[RobotGridCalib] = None,
    data: Optional[dict] = None,
    simple: bool = False,
    decollide_grid_fallback: bool = False,
    priority_order: list[int] = [],
) -> tuple[RobotGridCalib | dict, list[int]]:
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
    decollide_grid_fallback
        If `True`, runs ``decollideGrid()`` if the positioner-by-positioner
        decollision fails.
    priority_list
        A sorted list of positioner IDs with the order of which positioners to
        try to keep at their current positions. Positioners earlier in the list
        will be decollided last. Ignore in case of ``simple=True``.

    Returns
    -------
    grid,decollided
        If ``robot_grid`` is passed, returns the same grid instance after decollision.
        If ``data`` is passed, returns a dictionary describing the decollided grid
        that can be used to recreate a grid using `.load_robot_grid`. Also returns
        the list of robots that have been decollided.

    """

    if robot_grid is not None and data is not None:
        raise JaegerError("robot_grid and data are mutually exclusive.")

    if data is not None:
        robot_grid = load_robot_grid(data)

    assert robot_grid is not None

    if simple:
        collided = robot_grid.getCollidedRobotList()
        robot_grid.decollideGrid()
        if len(robot_grid.getCollidedRobotList()) > 0:
            raise JaegerError("Failed decolliding grid.")

        if data is not None:
            return dump_robot_grid(robot_grid), collided
        else:
            return robot_grid, collided

    # First pass. If collided, decollide each robot one by one.
    collided = robot_grid.getCollidedRobotList()

    # Sort collided robots by priority order.
    collided = sorted(
        collided,
        key=lambda x: (
            len(priority_order) - priority_order.index(x) if x in priority_order else -1
        ),
    )

    decollided = []
    if len(collided) > 0:
        for robot_id in collided:
            if robot_grid.isCollided(robot_id):
                if robot_grid.robotDict[robot_id].isOffline:
                    continue
                robot_grid.decollideRobot(robot_id)
                decollided.append(robot_id)  # Even if we failed it may have moved.
                if robot_grid.isCollided(robot_id):
                    raise JaegerError(f"Failed decolliding positioner {robot_id}.")

    # Second pass. If still collided, try a grid decollision.
    if len(robot_grid.getCollidedRobotList()) > 0:
        if decollide_grid_fallback:
            warn("Grid is still colliding. Attempting full grid decollision.")
            robot_grid.decollideGrid()
            if robot_grid.getCollidedRobotList() is not False:
                raise JaegerError("Failed decolliding grid.")
            # We don't know which robots were decollided so assume all collided
            # robots have moved.
            decollided = collided
        else:
            raise JaegerError("Failed decolliding grid.")

    if data is not None:
        return dump_robot_grid(robot_grid), decollided
    else:
        return robot_grid, decollided


def get_path_pair(
    robot_grid: Optional[RobotGridCalib] = None,
    data: Optional[dict] = None,
    path_generation_mode: str | None = None,
    ignore_did_fail: bool = False,
    explode_deg: float = 5,
    explode_positioner_id: int | None = None,
    speed=None,
    smooth_points=None,
    path_delay=None,
    collision_shrink=None,
    greed: float | None = None,
    phobia: float | None = None,
    stop_if_deadlock: bool = False,
    ignore_initial_collisions: bool = False,
) -> PathPairReturnType:
    """Runs path generation and returns the to and from destination paths.

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
        Either ``greedy``, ``mdp``, ``explode`` or ``explode_one``. If
        `None`, defaults to ``kaiju.default_path_generator``.
    ignore_did_fail
        Generate paths even if path generation failed (i.e., deadlocks).
    explode_deg
        Degrees for ``pathGenExplode``.
    explode_positioner_id
        The positioner to explode.
    speed, smooth_points, path_delay, collision_shrink
        Kaiju parameters to pass to ``getPathPair``. Otherwise uses the default
        configuration values.
    phobia, greed
        Parameters for ``pathGenMDP``. If not set uses ``kaiju`` configuration
        values.
    stop_if_deadlock
        If `True`, detects deadlocks early in the path and returns shorter
        trajectories (at the risk of some false positive deadlocks).
    ignore_initial_collisions
        If `True`, does not fail if the initial state is collided. To be used
        only for offsets.

    Returns
    -------
    paths
        A tuple with the to destination path, from destination path, whether path
        generation failed, and the list of deadlocks

    """

    if path_generation_mode is None:
        path_generation_mode = cast(str, config["kaiju"]["default_path_generator"])

    if robot_grid is not None and data is not None:
        raise JaegerError("robot_grid and data are mutually exclusive.")

    if data is not None:
        set_destination = False if path_generation_mode == "explode" else True
        robot_grid = load_robot_grid(data, set_destination=set_destination)

    assert robot_grid is not None

    deadlocks = []
    if path_generation_mode == "explode":
        log.debug(f"Running pathGenExplode with explode_deg={explode_deg}.")
        robot_grid.pathGenExplode(explode_deg)

    elif path_generation_mode == "explode_one":
        log.debug(
            f"Running pathGenExplodeOne with explode_deg={explode_deg}, "
            f"explode_positioner_id={explode_positioner_id}."
        )
        robot_grid.pathGenExplodeOne(explode_deg, explode_positioner_id)

    elif path_generation_mode == "greedy":
        log.debug(f"Running pathGenGreedy with stopIfDeadlock={stop_if_deadlock}.")
        robot_grid.pathGenGreedy(
            stopIfDeadlock=stop_if_deadlock,
            ignoreInitialCollisions=ignore_initial_collisions,
        )

    elif path_generation_mode == "mdp":
        greed = greed or config["kaiju"]["greed"]
        phobia = phobia or config["kaiju"]["phobia"]
        log.debug(f"Running pathGenMDP with phobia={phobia}, greed={greed}.")
        robot_grid.pathGenMDP2(
            greed=greed,
            phobia=phobia,
            ignoreInitialCollisions=ignore_initial_collisions,
        )

    else:
        raise ValueError(f"Invalid path_generation_mode={path_generation_mode!r}.")

    if path_generation_mode in ["greedy", "mdp"]:
        # Check for deadlocks.
        deadlocks = robot_grid.deadlockedRobots()
        if robot_grid.didFail and ignore_did_fail is False:
            return (None, None, robot_grid.didFail, deadlocks)

    speed = speed or config["kaiju"]["speed"]
    smooth_points = smooth_points or config["kaiju"]["smooth_points"]
    collision_shrink = collision_shrink or config["kaiju"]["collision_shrink"]
    path_delay = path_delay or config["kaiju"]["path_delay"]

    log.debug(
        f"Running getPathPair with speed={speed}, smoothPoints={smooth_points}, "
        f"collisionShrink={collision_shrink}, pathDelay={path_delay}."
    )

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


async def get_path_pair_in_executor(
    robot_grid: RobotGridCalib,
    **kwargs,
) -> PathPairReturnType:
    """Calls `.get_path_pair` with a process executor."""

    data = dump_robot_grid(robot_grid)
    traj_data = await run_in_executor(
        get_path_pair,
        data=data,
        executor="process",
        **kwargs,
    )

    return traj_data


async def decollide_in_executor(
    robot_grid: RobotGridCalib, **kwargs
) -> tuple[RobotGridCalib, list[int]]:
    """Calls `.decollide` with a process executor."""

    data = dump_robot_grid(robot_grid)
    decollided_data, collided = await run_in_executor(
        decollide,
        data=data,
        executor="process",
        **kwargs,
    )

    return load_robot_grid(decollided_data), collided


async def unwind(
    current_positions: dict[int, tuple[float | None, float | None]],
    collision_buffer: float | None = None,
    disabled: list[int] = [],
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
        data["grid"][int(pid)] = (alpha, beta, alpha0, beta0, pid in disabled)

    (to_destination, _, did_fail, deadlocks) = await run_in_executor(
        get_path_pair,
        data=data,
        path_generation_mode="greedy" if force is True else None,
        ignore_did_fail=force,
        stop_if_deadlock=force,
        executor="process",
    )
    if did_fail:
        if force is False:
            raise TrajectoryError(
                "Failed generating a valid unwind trajectory. "
                f"{len(deadlocks)} deadlocks were found ({deadlocks})."
            )
        else:
            log.warning("Deadlocks found in unwind but proceeding anyway.")

    return to_destination


async def explode(
    current_positions: dict[int, tuple[float | None, float | None]],
    explode_deg=20.0,
    collision_buffer: float | None = None,
    disabled: list[int] = [],
    positioner_id: int | None = None,
):
    """Explodes the grid by a number of degrees.

    This coroutine uses a process pool executor to run Kaiju routines.

    """

    alpha0, beta0 = config["kaiju"]["lattice_position"]

    data = {"collision_buffer": collision_buffer, "grid": {}}
    for pid, (alpha, beta) in current_positions.items():
        data["grid"][int(pid)] = (alpha, beta, alpha0, beta0, pid in disabled)

    if positioner_id is not None:
        path_generation_mode = "explode_one"
    else:
        path_generation_mode = "explode"

    (to_destination, *_) = await run_in_executor(
        get_path_pair,
        data=data,
        path_generation_mode=path_generation_mode,
        explode_deg=explode_deg,
        explode_positioner_id=positioner_id,
        ignore_did_fail=False,
        executor="process",
    )

    return to_destination


def get_snapshot_async(
    path: str,
    robot_grid: Optional[RobotGridCalib] = None,
    data: Optional[dict] = None,
    highlight: int | None = None,
    title: str | None = None,
):
    """Creates an FPS snapshot and saves it to disk. To be used with an executor.

    Parameters
    ----------
    path
        The path where to save the file.
    robot_grid
        The Kaiju ``RobotGridCalib`` instance to plot.
    data
        A dictionary of data that can be used to reload a Kaiju robot grid
        using `.load_robot_grid`. This is useful if the function is being
        run in an executor.
    highlight
        Robot to highlight.
    title
        A title for the plot.

    """

    import matplotlib.pyplot as plt

    if robot_grid is not None and data is not None:
        raise JaegerError("robot_grid and data are mutually exclusive.")

    if data is not None:
        robot_grid = load_robot_grid(data)

    assert robot_grid is not None

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".+array interface is deprecated.+")
        ax = robot_grid.plot_state(highlightRobot=highlight)

    assert ax is not None

    if title is not None:
        ax.set_title(title)

    plt.tight_layout()

    figure = ax.figure
    assert isinstance(figure, Figure)

    figure.savefig(path)

    plt.close("all")


async def get_snapshot(
    path: str,
    fps: FPS | None = None,
    positions: dict | None = None,
    collision_buffer: float | None = None,
    highlight: int | list | None = None,
    title: str | None = None,
    show_disabled: bool = True,
):
    """Plots a snapshot of the FPS and saves it to disk."""

    from jaeger.fps import FPS

    fps = fps or FPS.get_instance()
    if fps.initialised is False:
        await fps.initialise()

    data = {"collision_buffer": collision_buffer, "grid": {}}

    if positions is None:
        await fps.update_position()

        if len(fps.positioners) == 0:
            raise ValueError("No positioners connected.")

        for pid in fps.positioners.keys():
            data["grid"][int(pid)] = (
                fps[pid].alpha,
                fps[pid].beta,
                0,
                0,
                fps[pid].disabled if show_disabled else False,
            )

    else:
        for pid in positions:
            data["grid"][int(pid)] = (
                positions[pid]["alpha"],
                positions[pid]["beta"],
                0,
                0,
                fps[pid].disabled if show_disabled else False,
            )

    await run_in_executor(
        get_snapshot_async,
        path,
        data=data,
        highlight=highlight,
        title=title,
        executor="process",
    )

    return True


async def check_trajectory(
    trajectory: dict,
    current_positions: dict | None = None,
    fps: FPS | None = None,
    atol=1.0,
) -> bool:
    """Checks that the current position matches the starting point of a trajectory.

    Parameters
    ----------
    trajectory
        The dictionary with the trajectory to check.
    current_positions
        A mapping of positioner ID to ``(alpha, beta)`` with the current arrangement
        of the FPS array.
    fps
        If ``current_positions`` is not passed, the `.FPS` instance is used to
        determine the current arrangement.

    """

    if current_positions is None:
        if fps:
            await fps.update_position()
            current_positions = fps.get_positions_dict(ignore_disabled=True)
        else:
            raise RuntimeError("Either current_positions or fps must be passed.")

    if len(current_positions) == 0:
        return False

    if len(current_positions) != len(trajectory):
        warn("Mismatch between number of positioners and trajectory.")
        return False

    array_current = numpy.zeros((len(current_positions), 2), dtype=numpy.float32)
    array_trajectory = numpy.zeros((len(current_positions), 2), dtype=numpy.float32)
    for ii, pid in enumerate(current_positions):
        array_current[ii, :] = current_positions[pid]

        if pid not in trajectory:
            warn(f"Positioner {pid} is not in the trajectory.")
            return False

        alpha0 = trajectory[pid]["alpha"][0][0]
        beta0 = trajectory[pid]["beta"][0][0]

        array_trajectory[ii, :] = (alpha0, beta0)

    if numpy.allclose(array_current, array_trajectory, atol=atol):
        return True
    else:
        warn("Trajectory start and current positions do not match.")
        return False
