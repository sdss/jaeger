#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-10
# @Filename: tools.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os
import pathlib
import re

from typing import TYPE_CHECKING

import numpy
import pandas

from coordio.conv import (
    positionerToTangent,
    tangentToPositioner,
    tangentToWok,
    wokToTangent,
)
from coordio.defaults import POSITIONER_HEIGHT, calibration, getHoleOrient
from sdsstools import yanny

import jaeger.target
from jaeger import config, log
from jaeger.exceptions import JaegerError
from jaeger.kaiju import (
    decollide_in_executor,
    get_path_pair_in_executor,
    get_robot_grid,
)


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = [
    "wok_to_positioner",
    "positioner_to_wok",
    "copy_summary_file",
    "read_confSummary",
]


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

    wok_coords = numpy.array(wok)

    return wok_coords, numpy.array([tangent[0], tangent[1], 0])


async def create_random_configuration(
    fps: FPS,
    seed: int | None = None,
    safe=True,
    uniform: tuple[float, ...] | None = None,
    collision_buffer: float | None = None,
    max_deadlocks: int = 6,
    deadlock_retries: int = 5,
    n_failed: int = 0,
    max_retries: int = 5,
    path_generation_mode: str | None = None,
    **kwargs,
):
    """Creates a random configuration using Kaiju."""

    from jaeger.target.configuration import ManualConfiguration

    seed = seed or numpy.random.randint(0, 1000000)
    numpy.random.seed(seed)

    robot_grid = get_robot_grid(fps, seed=seed, collision_buffer=collision_buffer)

    alphaL, betaL = config["kaiju"]["lattice_position"]

    # We use Kaiju for convenience in the non-safe mode.
    for robot in robot_grid.robotDict.values():
        if robot.isOffline:
            continue

        if uniform is not None:
            alpha0, alpha1, beta0, beta1 = uniform
            robot.setAlphaBeta(
                numpy.random.uniform(alpha0, alpha1),
                numpy.random.uniform(beta0, beta1),
            )

        else:
            if safe:
                safe_mode = config["safe_mode"]
                if isinstance(safe_mode, bool) or safe_mode is None:
                    safe_mode = {"min_beta": 165, "max_beta": 175}

                robot.setAlphaBeta(
                    numpy.random.uniform(0, 359.9),
                    numpy.random.uniform(
                        safe_mode["min_beta"],
                        175.0,
                    ),
                )

            else:
                robot.setXYUniform()

        robot.setDestinationAlphaBeta(alphaL, betaL)

    # Confirm that the configuration is valid. This should only matter
    # for full range random configurations.
    try:
        robot_grid, _ = await decollide_in_executor(robot_grid, simple=True)
        grid_data = {
            robot.id: (robot.alpha, robot.beta)
            for robot in robot_grid.robotDict.values()
        }
    except JaegerError:
        raise JaegerError("Decollision failed. Cannot create random configuration.")

    _, _, did_fail, deadlocks = await get_path_pair_in_executor(
        robot_grid,
        path_generation_mode=path_generation_mode,
    )

    # If too many deadlocks, just try a new seed.
    n_deadlock = len(deadlocks)
    if did_fail and n_deadlock > max_deadlocks:
        if n_failed >= max_retries:
            raise JaegerError("Reached the limit of retries.")

        log.warning("Too many deadlocked robots. Trying new seed.")
        return await create_random_configuration(
            fps,
            safe=safe,
            uniform=uniform,
            collision_buffer=collision_buffer,
            deadlock_retries=deadlock_retries,
            n_failed=n_failed + 1,
            path_generation_mode=path_generation_mode,
        )

    if did_fail and n_deadlock > 0:
        # Now the fun part, if there are only a few deadlocks, try assigning them
        # a random position.
        log.warning(f"Found {n_deadlock} deadlocked robots. Trying to unlock.")
        for nn in range(1, deadlock_retries + 1):
            log.info(f"Retry {nn} out of {deadlock_retries}.")

            to_replace_robot = numpy.random.choice(deadlocks)

            robot_grid = get_robot_grid(
                fps,
                seed=seed + 1,
                collision_buffer=collision_buffer,
            )

            for robot in robot_grid.robotDict.values():
                if robot.isOffline:
                    continue

                if robot.id == to_replace_robot:
                    robot.setXYUniform()
                else:
                    robot.setAlphaBeta(*grid_data[robot.id])

            try:
                robot_grid, _ = await decollide_in_executor(robot_grid, simple=True)
                grid_data = {
                    robot.id: (robot.alpha, robot.beta)
                    for robot in robot_grid.robotDict.values()
                }
            except JaegerError:
                raise JaegerError(
                    "Failed creating random configuration: cannot remove deadlocks."
                )

            _, _, did_fail, deadlocks = await get_path_pair_in_executor(
                robot_grid,
                path_generation_mode=path_generation_mode,
            )
            if did_fail is False:
                log.info("Random configuration has been unlocked.")
                break
            else:
                log.info(f"{len(deadlocks)} deadlocks remaining.")

            if nn == deadlock_retries:
                log.warning("Failed unlocking. Trying new seed.")
                return await create_random_configuration(
                    fps,
                    seed=seed + 1,
                    safe=safe,
                    uniform=uniform,
                    collision_buffer=collision_buffer,
                    deadlock_retries=deadlock_retries,
                    path_generation_mode=path_generation_mode,
                )

    pT = calibration.positionerTable.copy().reset_index()

    # Build an assignment dictionary.
    data = {}
    for pid in grid_data:
        holeID = pT.loc[pT.positionerID == pid].holeID.values[0]
        data[holeID] = {
            "alpha": grid_data[pid][0],
            "beta": grid_data[pid][1],
            "fibre_type": "Metrology",
        }

    return ManualConfiguration(data, **kwargs)


def copy_summary_file(
    configuration_id0: int,
    configuration_id1: int,
    design_id1: int | None = None,
    flavour: str = "",
):
    """Copies a summary file optionally modifying its design_id."""

    orig_file = jaeger.target.Configuration._get_summary_file_path(
        configuration_id0,
        config["observatory"],
        flavour,
    )

    if not os.path.exists(orig_file):
        return

    new_file = jaeger.target.Configuration._get_summary_file_path(
        configuration_id1,
        config["observatory"],
        flavour,
    )

    new_path = pathlib.Path(new_file)
    new_path.parent.mkdir(parents=True, exist_ok=True)

    summary_data = open(orig_file, "r").read()
    summary_data = re.sub(
        r"(configuration_id\s)[0-9]+",
        rf"\g<1>{configuration_id1}",
        summary_data,
    )

    if design_id1:
        summary_data = re.sub(
            rf"confSummary(F?)\-{configuration_id0}\.par",
            rf"confSummary\g<1>-{configuration_id1}.par",
            summary_data,
        )

        summary_data = re.sub(
            r"(design_id\s)[0-9]+",
            rf"\g<1>{design_id1}",
            summary_data,
        )

        summary_data = re.sub(
            r"cloned_from -999",
            f"cloned_from {configuration_id0}",
            summary_data,
        )

    with open(new_path, "w") as f:
        f.write(summary_data)


def read_confSummary(input: str | pathlib.Path | int, flavour: str = "") -> tuple:
    """Reads a configuration summary file and returns the header and data frame."""

    if isinstance(input, (str, pathlib.Path)):
        path = input
    else:
        sdsscore_dir = pathlib.Path(os.environ["SDSSCORE_DIR"])
        summary_files = sdsscore_dir / "apo" / "summary_files"
        conf_xx = summary_files / f"{int(input/100):04d}XX"
        path = conf_xx / f"confSummary{flavour}-{input}.par"

    if not os.path.exists(str(path)):
        raise FileNotFoundError(f"{path} does not exist.")

    y = yanny(str(path))
    header = dict(y)

    fibermap = header.pop("FIBERMAP")
    fibermap = fibermap[[col for col in fibermap.dtype.names if col != "mag"]]

    df = pandas.DataFrame(fibermap)

    for col in df.select_dtypes("object").columns:
        df[col] = df[col].str.decode("utf-8")

    for key, value in header.items():
        try:
            header[key] = int(value)
        except ValueError:
            try:
                header[key] = float(value)
            except ValueError:
                pass

    return header, df.set_index(["positionerId", "fiberType"])
