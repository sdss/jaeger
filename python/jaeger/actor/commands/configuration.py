#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-18
# @Filename: configuration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
from functools import partial

from typing import TYPE_CHECKING

import click
import numpy

from jaeger.design import Configuration, Design, ManualConfiguration
from jaeger.exceptions import TrajectoryError

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["configuration"]


@jaeger_parser.group()
def configuration():
    """Allows to load a configuration, ingest it to the database, and execute it."""
    pass


@configuration.command()
@click.option(
    "--reload",
    is_flag=True,
    help="If the design is currently loaded, creates a new configuration.",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Replace an existing entry.",
)
@click.option("--folded", is_flag=True, help="Loads a folded configuration.")
@click.argument("DESIGNID", type=int, required=False)
async def load(
    command: Command[JaegerActor],
    fps: FPS,
    designid: int | None = None,
    reload: bool = False,
    replace: bool = False,
    folded: bool = False,
):
    """Loads and ingests a configuration from a design in the database."""

    if folded:
        designid = designid or -999
        fps.configuration = ManualConfiguration.create_folded(design_id=designid)
        return command.finish("Manual configuration loaded.")

    if designid is None:
        return command.fail(error="Design ID is required.")

    if reload is True:
        if fps.configuration is None:
            return command.fail(error="No configuration found. Cannot reload.")
        if fps.configuration.design_id != designid:
            return command.fail(error="Loaded configuration does not match designid.")
        fps.configuration.configuration_id = None

    else:
        try:
            design = Design(designid)
        except (ValueError, RuntimeError) as err:
            return command.fail(error=f"Failed retrieving design: {err}")

        fps.configuration = design.configuration

    assert isinstance(fps.configuration, Configuration)

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.ingested is False:
        replace = False

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        partial(fps.configuration.write_to_database, replace=replace),
    )

    configuration = fps.configuration
    boresight = fps.configuration.assignment_data.observed_boresight
    command.debug(
        configuration_loaded=[
            configuration.configuration_id,
            configuration.design.design_id,
            boresight.ra[0],
            boresight.dec[0],
            configuration.design.field.position_angle,
            boresight[0, 0],
            boresight[0, 1],
        ]
    )

    return command.finish(
        text=f"Configuration {fps.configuration.configuration_id} loaded "
        "and written to database."
    )


@configuration.command()
async def execute(command: Command[JaegerActor], fps: FPS):
    """Executes a configuration trajectory."""

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    positions = fps.get_positions(ignore_disabled=True)
    if len(positions) == 0:
        return command.fail("No positioners found.")

    # Check that all non-disabled positioners are folded.
    if not numpy.allclose(positions[:, 1:] - [0, 180], 0, atol=0.1):
        return command.fail(error="Not all the positioners are folded.")

    command.info(text="Calculating trajectory.")
    try:
        trajectory = await asyncio.get_event_loop().run_in_executor(
            None,
            fps.configuration.get_trajectory,
        )
    except Exception as err:
        return command.fail(error=f"Failed getting trajectory: {err}")

    traj_pids = trajectory.keys()
    for pid in traj_pids:
        if pid not in fps:
            return command.fail(
                error=f"Trajectory contains positioner_id={pid} which is not connected."
            )

    command.info(text="Sending and executing trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
