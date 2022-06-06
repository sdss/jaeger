#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-06-05
# @Filename: home.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from . import jaeger_parser


if TYPE_CHECKING:
    from jaeger import FPS

    from . import JaegerCommandType


__all__ = ["home"]


@jaeger_parser.command()
@click.argument(
    "AXIS",
    type=click.Choice(["alpha", "beta", "both"], case_sensitive=False),
)
@click.argument("POSITIONER_IDS", nargs=-1, required=False)
@click.option(
    "--no-datums",
    is_flag=True,
    default=False,
    help="Do not calibrate datums.",
)
@click.option(
    "--motor/--no-motor",
    is_flag=True,
    default=False,
    help="Calibrate/do not calibrate the motors.",
)
@click.option(
    "--cogging-torque/--no-cogging-torque",
    is_flag=True,
    default=False,
    help="Calibrate/do not calibrate cogging torque.",
)
async def home(
    command: JaegerCommandType,
    fps: FPS,
    axis: str,
    positioner_ids: tuple[int, ...] = (),
    no_datums: bool = False,
    motor: bool = False,
    cogging_torque: bool = False,
):
    """Re-homes positioners."""

    if axis.lower() == "both" and positioner_ids == ():
        return command.fail("Cannot home all robots in both axes at the same time.")

    axes = [axis.lower()] if axis.lower() != "both" else ["alpha", "beta"]

    for ax in axes:
        pass

    return command.finish()
