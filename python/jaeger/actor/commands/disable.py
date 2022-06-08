#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-12-31
# @Filename: disable.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from . import JaegerCommandType, jaeger_parser


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = ["disable", "enable"]


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int, required=False)
async def disable(
    command: JaegerCommandType,
    fps: FPS,
    positioner_id: int | None = None,
):
    """Disables a positioner"""

    if positioner_id is None:
        disabled: list[int] = []

        for positioner in fps.positioners.values():
            if positioner.offline or positioner.disabled:
                disabled.append(positioner.positioner_id)

        return command.finish(disabled=list(sorted(disabled)))

    if positioner_id not in fps:
        return command.fail(f"Positioner {positioner_id} is not in the array.")

    positioner = fps.positioners[positioner_id]

    if positioner.disabled:
        command.warning(f"Positioner {positioner_id} is already disabled.")

    positioner.disabled = True
    fps.disabled.add(positioner.positioner_id)

    return command.finish()


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int)
async def enable(command: JaegerCommandType, fps: FPS, positioner_id: int):
    """Enables a positioner"""

    if positioner_id not in fps:
        return command.fail(f"Positioner {positioner_id} is not in the array.")

    positioner = fps.positioners[positioner_id]

    if positioner.disabled is False and positioner.offline is False:
        command.warning(f"Positioner {positioner_id} is not disabled.")

    positioner.disabled = False
    positioner.offline = False

    return command.finish()
