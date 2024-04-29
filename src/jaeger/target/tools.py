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
from functools import cache

from typing import TYPE_CHECKING

import numpy
import polars

from coordio.defaults import calibration
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
    "get_wok_data",
    "copy_summary_file",
    "read_confSummary",
    "get_fibermap_table",
]


@cache
def get_wok_data(observatory: str):
    """Returns a Polars frame with wok calibration data.

    Parameters
    ----------
    observatory
        The observatory for which to return the wok data.

    Returns
    -------
    wok_data
        A Polars data frame with the combined positioner table,
        wok coordinates, and fibre assignments.

    """

    positionerTable = polars.from_pandas(calibration.positionerTable.reset_index())
    wokCoords = polars.from_pandas(calibration.wokCoords.reset_index())
    fibre_ass = polars.from_pandas(calibration.fiberAssignments.reset_index())

    if positionerTable is None or wokCoords is None or fibre_ass is None:
        raise ValueError("FPS calibrations not loaded.")

    # Consolidate both tables. Reject duplicate (_right) columns.
    wok_data = (
        positionerTable.join(
            wokCoords,
            on=["site", "holeID"],
            how="inner",
        )
        .join(
            fibre_ass,
            on=["site", "holeID"],
            how="inner",
        )
        .select(~polars.selectors.ends_with("_right"))
    )

    return wok_data.filter(polars.col("site") == observatory).sort("holeID")


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

    # Build an assignment dictionary.
    data = {}
    for pid in grid_data:
        data[pid] = (grid_data[pid][0], grid_data[pid][1])

    return ManualConfiguration(data, fps.observatory, **kwargs)


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


def read_confSummary(
    input: str | pathlib.Path | int,
    flavour: str = "",
) -> tuple[dict, polars.DataFrame]:
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

    df = polars.DataFrame(fibermap)
    df = df.with_columns(polars.selectors.binary().cast(polars.String()))

    for key, value in header.items():
        try:
            header[key] = int(value)
        except ValueError:
            try:
                header[key] = float(value)
            except ValueError:
                pass

    return header, df.sort(["positionerId", "fiberType"])


def get_fibermap_table(length: int) -> tuple[numpy.ndarray, dict]:
    """Returns a stub for the FIBERMAP table and a default entry,"""

    fiber_map_data = [
        ("positionerId", numpy.int16, -999),
        ("holeId", "U7", ""),
        ("fiberType", "U10", ""),
        ("assigned", numpy.int16, 0),
        ("on_target", numpy.int16, 0),
        ("valid", numpy.int16, 0),
        ("decollided", numpy.int16, 0),
        ("xwok", numpy.float64, -999.0),
        ("ywok", numpy.float64, -999.0),
        ("zwok", numpy.float64, -999.0),
        ("xFocal", numpy.float64, -999.0),
        ("yFocal", numpy.float64, -999.0),
        ("alpha", numpy.float32, -999.0),
        ("beta", numpy.float32, -999.0),
        ("racat", numpy.float64, -999.0),
        ("deccat", numpy.float64, -999.0),
        ("pmra", numpy.float32, -999.0),
        ("pmdec", numpy.float32, -999.0),
        ("parallax", numpy.float32, -999.0),
        ("ra", numpy.float64, -999.0),
        ("dec", numpy.float64, -999.0),
        ("ra_observed", numpy.float64, -999.0),
        ("dec_observed", numpy.float64, -999.0),
        ("alt_observed", numpy.float64, -999.0),
        ("az_observed", numpy.float64, -999.0),
        ("lambda_design", numpy.float32, -999.0),
        ("lambda_eff", numpy.float32, -999.0),
        ("coord_epoch", numpy.float32, -999.0),
        ("spectrographId", numpy.int16, -999),
        ("fiberId", numpy.int16, -999),
        ("mag", numpy.dtype(("<f4", (5,))), [-999.0] * 5),
        ("optical_prov", "U30", ""),
        ("bp_mag", numpy.float32, -999.0),
        ("gaia_g_mag", numpy.float32, -999.0),
        ("rp_mag", numpy.float32, -999.0),
        ("h_mag", numpy.float32, -999.0),
        ("catalogid", numpy.int64, -999),
        ("carton_to_target_pk", numpy.int64, -999),
        ("cadence", "U100", ""),
        ("firstcarton", "U100", ""),
        ("program", "U100", ""),
        ("category", "U100", ""),
        ("sdssv_boss_target0", numpy.int64, 0),
        ("sdssv_apogee_target0", numpy.int64, 0),
        ("delta_ra", numpy.float64, 0.0),
        ("delta_dec", numpy.float64, 0.0),
    ]

    names, formats, defaults = zip(*fiber_map_data)

    fibermap = numpy.empty((length,), dtype={"names": names, "formats": formats})

    # Define a default row with all set to "" or -999. depending on column data type.
    default = {}
    for i in range(len(names)):
        name = names[i]
        default[name] = defaults[i]

    return (fibermap, default)
