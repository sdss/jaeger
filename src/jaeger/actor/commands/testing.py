#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-01-04
# @Filename: testing.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from . import JaegerCommandType, jaeger_parser


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = ["testing"]


@jaeger_parser.group()
def testing():
    """Commands for testing. Use with caution."""

    pass


@testing.command(name="fake-configuration")
@click.argument("RA", type=float)
@click.argument("DEC", type=float)
@click.argument("PA", type=float, required=False, default=0.0)
async def disable(
    command: JaegerCommandType,
    fps: FPS,
    ra: float,
    dec: float,
    pa: float = 0.0,
):
    """Outputs the ``configuration_loaded`` keyword for a given field."""

    command.info(
        configuration_loaded=[-999, -999, -999, ra, dec, pa, -999.0, -999.0, "/data"]
    )

    return command.finish()
