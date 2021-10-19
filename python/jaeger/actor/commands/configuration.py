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

from jaeger.design import Design

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
@click.argument("DESIGNID", type=int)
async def load(
    command: Command[JaegerActor],
    fps: FPS,
    designid: int,
    reload: bool = False,
    replace: bool = False,
):
    """Loads and ingests a configuration from a design in the database."""

    if reload is True:
        if fps.configuration is None:
            return command.fail(error="No configuration found. Cannot reload.")
        if fps.configuration.design.design_id != designid:
            return command.fail(error="Loaded configuration does not match designid.")
        fps.configuration.configuration_id = None

    else:
        try:
            design = Design(designid)
        except (ValueError, RuntimeError) as err:
            return command.fail(error=f"Failed retrieving design: {err}")

        fps.configuration = design.configuration

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
