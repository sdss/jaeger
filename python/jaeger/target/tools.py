#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-10
# @Filename: tools.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import warnings

from typing import TYPE_CHECKING

import numpy

from coordio.conv import (
    positionerToTangent,
    tangentToPositioner,
    tangentToWok,
    wokToTangent,
)
from coordio.defaults import POSITIONER_HEIGHT, calibration, getHoleOrient

from jaeger import FPS, config, log
from jaeger.exceptions import JaegerError, JaegerUserWarning, TrajectoryError
from jaeger.utils.helpers import run_in_executor


if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from kaiju import RobotGridCalib


__all__ = [
    "warn",
    "get_robot_grid",
    "decollide_grid",
    "unwind",
    "explode",
    "wok_to_positioner",
    "positioner_to_wok",
    "get_snapshot",
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
    epsilon = ang_step * 2

    if collision_buffer < 1.5:
        raise JaegerError("Invalid collision buffer < 1.5.")

    robot_grid = RobotGridCalib(stepSize=ang_step, epsilon=epsilon, seed=seed)
    robot_grid.setCollisionBuffer(collision_buffer)

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


def decollide_grid(robot_grid: RobotGridCalib, simple=False):
    """Decollides a potentially collided grid. Raises on fail.

    If ``simple=True``, just runs a ``decollideGrid()`` and returns silently.

    """

    def get_collided():
        collided = [rid for rid in robot_grid.robotDict if robot_grid.isCollided(rid)]
        if len(collided) == 0:
            return False
        else:
            return collided

    if simple:
        robot_grid.decollideGrid()
        if get_collided() is not False:
            raise JaegerError("Failed decolliding grid.")
        return

    # First pass. If collided, decollide each robot one by one.
    # TODO: Probably this should be done in order of less to more important targets
    # to throw out the less critical ones first.
    collided = get_collided()
    if collided is not False:
        warn("The grid is collided. Attempting one-by-one decollision.")
        for robot_id in collided:
            if robot_grid.isCollided(robot_id):
                robot_grid.decollideRobot(robot_id)
                if robot_grid.isCollided(robot_id):
                    warn(f"Failed decolliding positioner {robot_id}.")
                else:
                    warn(f"Positioner {robot_id} was successfully decollided.")

    # Second pass. If still collided, try a grid decollision.
    if get_collided() is not False:
        warn("Grid is still colliding. Attempting full grid decollision.")
        robot_grid.decollideGrid()
        if get_collided() is not False:
            raise JaegerError("Failed decolliding grid.")
        else:
            warn("The grid was decollided.")


def unwind(
    current_positions: dict[int, tuple[float, float]],
    collision_buffer: float | None = None,
    force: bool = False,
):
    """Folds all the robots to the lattice position."""

    robot_grid = get_robot_grid(collision_buffer=collision_buffer)

    for robot in robot_grid.robotDict.values():
        if robot.id not in current_positions:
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot_position = current_positions[robot.id]
        robot.setAlphaBeta(robot_position[0], robot_position[1])

    for robot in robot_grid.robotDict.values():
        if robot_grid.isCollided(robot.id):
            raise ValueError(f"Robot {robot.id} is kaiju-collided. Cannot unwind.")

    robot_grid.pathGenGreedy()
    if robot_grid.didFail:
        if force is False:
            raise TrajectoryError(
                "Failed generating a valid trajectory. "
                "This usually means a deadlock was found."
            )
        else:
            log.warning("Deadlocks found in unwind but proceeding anyway.")

    layout_pids = [robot.id for robot in robot_grid.robotDict.values()]
    if len(set(current_positions.keys()) - set(layout_pids)) > 0:
        # Some connected positioners are not in the layout.
        raise ValueError("Some connected positioners are not in the grid layout.")

    speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]

    _, reverse = robot_grid.getPathPair(speed=speed)

    return reverse


def explode(
    current_positions: dict[int, tuple[float, float]],
    explode_deg=20.0,
    collision_buffer: float | None = None,
):
    """Explodes the grid by a number of degrees."""

    robot_grid = get_robot_grid(collision_buffer=collision_buffer)

    for robot in robot_grid.robotDict.values():
        if robot.id not in current_positions:
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot_position = current_positions[robot.id]
        robot.setAlphaBeta(robot_position[0], robot_position[1])

    robot_grid.pathGenEscape(explode_deg)

    layout_pids = [robot.id for robot in robot_grid.robotDict.values()]
    if len(set(current_positions.keys()) - set(layout_pids)) > 0:
        # Some connected positioners are not in the layout.
        raise ValueError("Some connected positioners are not in the grid layout.")

    speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]

    _, reverse = robot_grid.getPathPair(speed=speed)

    return reverse


def wok_to_positioner(
    hole_id: str,
    site: str,
    fibre_type: str,
    xwok: float,
    ywok: float,
    zwok: float = POSITIONER_HEIGHT,
) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Converts from wok to positioner coordinates.

    Returns arrays with the positioner and tangent coordinates.

    """

    positioner_data = calibration.positionerTable.loc[(site, hole_id)]

    hole_orient = getHoleOrient(site, hole_id)

    if fibre_type == "APOGEE":
        xBeta = positioner_data.apX
        yBeta = positioner_data.apY
    elif fibre_type == "BOSS":
        xBeta = positioner_data.bossX
        yBeta = positioner_data.bossY
    elif fibre_type == "Metrology":
        xBeta = positioner_data.metX
        yBeta = positioner_data.metY
    else:
        raise ValueError(f"Invalid fibre type {fibre_type}.")

    tangent = wokToTangent(
        xwok,
        ywok,
        zwok,
        *hole_orient,
        dx=positioner_data.dx,
        dy=positioner_data.dy,
    )

    alpha, beta, _ = tangentToPositioner(
        tangent[0][0],
        tangent[1][0],
        xBeta,
        yBeta,
        la=positioner_data.alphaArmLen,
        alphaOffDeg=positioner_data.alphaOffset,
        betaOffDeg=positioner_data.betaOffset,
    )

    return (
        numpy.array([alpha, beta]),
        numpy.array([tangent[0][0], tangent[1][0], tangent[2][0]]),
    )


def positioner_to_wok(
    hole_id: str,
    site: str,
    fibre_type: str,
    alpha: float,
    beta: float,
):
    """Convert from positioner to wok coordinates.

    Returns xyz wok and tangent coordinates as a tuple of arrays.

    """

    positioner_data = calibration.positionerTable.loc[(site, hole_id)]
    wok_data = calibration.wokCoords.loc[(site, hole_id)]

    b = wok_data[["xWok", "yWok", "zWok"]]
    iHat = wok_data[["ix", "iy", "iz"]]
    jHat = wok_data[["jx", "jy", "jz"]]
    kHat = wok_data[["kx", "ky", "kz"]]

    if fibre_type == "APOGEE":
        xBeta = positioner_data.apX
        yBeta = positioner_data.apY
    elif fibre_type == "BOSS":
        xBeta = positioner_data.bossX
        yBeta = positioner_data.bossY
    elif fibre_type == "Metrology":
        xBeta = positioner_data.metX
        yBeta = positioner_data.metY
    else:
        raise ValueError(f"Invlid fibre type {fibre_type}.")

    tangent = positionerToTangent(
        alpha,
        beta,
        xBeta,
        yBeta,
        la=positioner_data.alphaArmLen,
        alphaOffDeg=positioner_data.alphaOffset,
        betaOffDeg=positioner_data.betaOffset,
    )

    wok = tangentToWok(
        tangent[0],
        tangent[1],
        0,
        b,
        iHat,
        jHat,
        kHat,
        dx=positioner_data.dx,
        dy=positioner_data.dy,
    )

    return numpy.array(wok), numpy.array([tangent[0], tangent[1], 0])


async def get_snapshot(fps: FPS | None = None) -> Axes:
    """Returns matplotlib axes with the current arrangement of the FPS array."""

    fps = fps or FPS.get_instance()
    if fps.initialised is False:
        await fps.initialise()

    await fps.update_position()

    # Create a robot grid and set the current positions.
    robot_grid = get_robot_grid()

    for robot in robot_grid.robotDict.values():
        if robot.id not in fps.positioners.keys():
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot.setAlphaBeta(fps[robot.id].alpha, fps[robot.id].beta)

    ax: Axes = await run_in_executor(robot_grid.plot_state)

    return ax
