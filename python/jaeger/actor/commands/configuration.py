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
    "--generate-paths",
    is_flag=True,
    help="Generates and stores the to and from destination paths.",
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
    generate_paths: bool = False,
):
    """Creates and ingests a configuration from a design in the database."""

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
            design = await Design.create_async(designid)
        except (ValueError, RuntimeError, JaegerError) as err:
            return command.fail(error=f"Failed retrieving design: {err}")

        fps.configuration = design.configuration

    assert isinstance(fps.configuration, Configuration)

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.ingested is False:
        replace = False

    fps.configuration.write_to_database(replace=replace)

    configuration = fps.configuration
    assert configuration.design

    boresight = fps.configuration.assignment_data.boresight

    command.debug(
        configuration_loaded=[
            configuration.configuration_id,
            configuration.design.design_id,
            boresight.ra[0],
            boresight.dec[0],
            configuration.design.field["position_angle"],
            boresight[0, 0],
            boresight[0, 1],
        ]
    )

    if generate_paths:
        try:
            await configuration.get_trajectory(decollide=True)
        except (TrajectoryError, JaegerError) as err:
            return command.fail(
                error=f"Failed generating paths: {err} "
                "The configuration has been loaded and written to the database."
            )

    return command.finish(
        text=f"Configuration {fps.configuration.configuration_id} loaded "
        "and written to database."
    )


@configuration.command()
async def execute(command: Command[JaegerActor], fps: FPS):
    """Executes a configuration trajectory (folded to targets)."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    if fps.configuration is None or fps.configuration.ingested is False:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.from_destination is not None:
        from_destination = fps.configuration.from_destination
        command.info(text="Using stored trajectory (from destination).")
    else:
        command.info(text="Calculating trajectory.")
    try:
        from_destination = await fps.configuration.get_trajectory()
    except Exception as err:
        return command.fail(error=f"Failed getting trajectory: {err}")

    if not check_trajectory(from_destination, fps=fps, atol=1):
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

    if not check_trajectory(to_destination, fps=fps, atol=1):
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
        trajectory = await configuration.get_trajectory(decollide=False)
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
