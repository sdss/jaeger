#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-18
# @Filename: configuration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import numpy
from astropy.time import Time

from jaeger import config
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.kaiju import check_trajectory
from jaeger.target.configuration import Configuration, ManualConfiguration
from jaeger.target.design import Design
from jaeger.target.tools import create_random_configuration

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
@click.option(
    "--generate-paths/--no-generate-paths",
    default=True,
    help="Generates and stores the to and from destination paths.",
)
@click.option(
    "--epoch-delay",
    type=float,
    default=0.0,
    help="A delay in seconds for the epoch for which the configuration is calculated.",
)
@click.option(
    "--from-positions",
    is_flag=True,
    help="Loads a configuration from the current robot positions.",
)
@click.option(
    "--ingest/--no-ingest",
    default=True,
    help="Whether to ingest the configuration into the DB.",
)
@click.option(
    "--write-summary/--no-write-summary",
    default=True,
    help="Whether to write the summary file for the configuration. "
    "Ignored if --no-ingest.",
)
@click.argument("DESIGNID", type=int, required=False)
async def load(
    command: Command[JaegerActor],
    fps: FPS,
    designid: int | None = None,
    reload: bool = False,
    replace: bool = False,
    from_positions: bool = False,
    generate_paths: bool = False,
    epoch_delay: float = 0.0,
    ingest: bool = False,
    write_summary: bool = False,
):
    """Creates and ingests a configuration from a design in the database."""

    command.info(f"Loading design {designid}.")

    if reload is True:
        if fps.configuration is None:
            return command.fail(error="No configuration found. Cannot reload.")
        designid = fps.configuration.design_id
        fps.configuration.configuration_id = None

    elif from_positions is True:
        await fps.update_position()
        positions = fps.get_positions_dict()
        fps.configuration = ManualConfiguration.create_from_positions(positions)

    else:
        if designid is None:
            return command.fail(error="Design ID is required.")

        try:
            # Define the epoch for the configuration.
            epoch = Time.now().jd + epoch_delay / 86400.0
            design = await Design.create_async(designid, epoch=epoch)
        except (ValueError, RuntimeError, JaegerError) as err:
            return command.fail(error=f"Failed retrieving design: {err}")

        fps.configuration = design.configuration

    assert isinstance(fps.configuration, Configuration)

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.ingested is False:
        replace = False

    configuration = fps.configuration
    configuration.set_command(command)

    if generate_paths:
        try:
            command.info("Calculating trajectories.")
            await configuration.decollide_and_get_paths()
        except Exception as err:
            return command.fail(error=f"Failed generating paths: {err}")

    if ingest:
        fps.configuration.write_to_database(replace=replace)
    else:
        command.warning("Not ingesting configuration. Configuration ID is -999.")
        fps.configuration.configuration_id = -999

    if ingest and write_summary:
        summary_file = fps.configuration.write_summary(overwrite=True)
    else:
        summary_file = ""

    boresight = fps.configuration.assignment_data.boresight

    command.debug(
        configuration_loaded=[
            configuration.configuration_id,
            configuration.design.design_id if configuration.design else -999,
            boresight.ra[0],
            boresight.dec[0],
            configuration.design.field.position_angle if configuration.design else 0,
            boresight[0, 0],
            boresight[0, 1],
            summary_file,
        ]
    )

    snapshot = await configuration.save_snapshot()
    command.info(configuration_snapshot=snapshot)

    return command.finish(f"Configuration {fps.configuration.configuration_id} loaded.")


@configuration.command()
async def execute(command: Command[JaegerActor], fps: FPS):
    """Executes a configuration trajectory (folded to targets)."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.from_destination is not None:
        from_destination = fps.configuration.from_destination
        command.info(text="Using stored trajectory (from destination).")
    else:
        command.info(text="Calculating trajectory.")
    try:
        from_destination = await fps.configuration.decollide_and_get_paths()
    except Exception as err:
        return command.fail(error=f"Failed getting trajectory: {err}")

    if not await check_trajectory(from_destination, fps=fps, atol=1):
        return command.fail(error="Trajectory validation failed.")

    command.info(text="Sending and executing forward trajectory.")

    try:
        await fps.send_trajectory(from_destination, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish()


@configuration.command()
async def reverse(command: Command[JaegerActor], fps: FPS):
    """Executes a reverse trajectory (targets to folded)."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    to_destination = fps.configuration.to_destination
    if to_destination is None:
        return command.fail(
            error="The configuration does not have a to_destination path. Use unwind."
        )

    if not await check_trajectory(to_destination, fps=fps, atol=1):
        return command.fail(error="Trajectory validation failed.")

    command.info(text="Sending and executing reverse trajectory.")

    try:
        await fps.send_trajectory(to_destination, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish()


@configuration.command()
@click.argument("SEED", type=int, required=False)
@click.option("--danger", is_flag=True, help="Use full range of alpha and beta.")
@click.option(
    "--uniform",
    type=str,
    default=None,
    help="Comma-separated alpha and beta ranges.",
)
@click.option(
    "--collision-buffer",
    type=click.FloatRange(1.6, 3.0),
    help="Custom collision buffer",
)
async def random(
    command: Command[JaegerActor],
    fps: FPS,
    seed: int | None = None,
    danger: bool = False,
    uniform: str | None = None,
    collision_buffer: float | None = None,
):
    """Executes a random, valid configuration."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    command.debug(text="Checking that all positioners are folded.")

    # Check that all positioners are folded.
    await fps.update_position()
    positions = fps.get_positions()

    if len(positions) == 0:
        return command.fail("No positioners connected")

    alphaL, betaL = config["kaiju"]["lattice_position"]
    if not numpy.allclose(positions[:, 1:] - [alphaL, betaL], 0, atol=1):
        return command.fail(error="Not all the positioners are folded.")

    command.info(text="Creating random configuration.")

    uniform_unpack: tuple[float, ...] | None
    if uniform:
        try:
            uniform_unpack = tuple(map(float, uniform.split(",")))
        except Exception as err:
            return command.fail(error=f"Failed calculating uniform ranges: {err}")
    else:
        uniform_unpack = None

    configuration = await create_random_configuration(
        seed=seed,
        uniform=uniform_unpack,
        safe=not danger,
        collision_buffer=collision_buffer,
    )

    try:
        command.info("Getting trajectory.")
        trajectory = await configuration.decollide_and_get_paths(decollide=False)
    except JaegerError as err:
        return command.fail(error=f"jaeger random failed: {err}")

    command.info("Executing random trajectory.")

    # Make this the FPS configuration
    assert command.actor
    command.actor.fps.configuration = configuration

    try:
        await fps.send_trajectory(trajectory, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish()
