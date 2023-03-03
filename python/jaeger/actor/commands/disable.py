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

from jaeger import config

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

    permanently_disabled = config["fps"]["disabled_positioners"]
    if config["fps"]["offline_positioners"] is not None:
        permanently_disabled += list(config["fps"]["offline_positioners"].keys())

    if positioner_id:
        if positioner_id not in fps:
            return command.fail(f"Positioner {positioner_id} is not in the array.")

        positioner = fps.positioners[positioner_id]

        if positioner.disabled:
            command.warning(f"Positioner {positioner_id} is already disabled.")

        positioner.disabled = True
        fps.disabled.add(positioner.positioner_id)

    manually_disabled: list[int] = []
    for positioner in fps.positioners.values():
        if positioner.offline or positioner.disabled:
            if positioner.positioner_id not in permanently_disabled:
                manually_disabled.append(positioner.positioner_id)

    return command.finish(
        permanently_disabled=list(sorted(permanently_disabled)),
        manually_disabled=list(sorted(manually_disabled)),
    )


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int)
async def enable(command: JaegerCommandType, fps: FPS, positioner_id: int):
    """Enables a positioner"""

    permanently_disabled = config["fps"]["disabled_positioners"]
    if config["fps"]["offline_positioners"] is not None:
        permanently_disabled += list(config["fps"]["offline_positioners"].keys())

    if positioner_id not in fps:
        return command.fail(f"Positioner {positioner_id} is not in the array.")

    if positioner_id in permanently_disabled:
        return command.fail(
            f"Positioner {positioner_id} is permanently "
            "disabled and cannot be enabled."
        )

    positioner = fps.positioners[positioner_id]

    if positioner.disabled is False and positioner.offline is False:
        command.warning(f"Positioner {positioner_id} is not disabled.")

    positioner.disabled = False
    positioner.offline = False

    return command.finish()
