#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-01-27
# @Filename: chiller.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

from . import JaegerCommandType, command_parser


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = ["alerts"]


@command_parser.group()
def alerts(*args):
    """Manages the alerts system."""

    pass


@alerts.command()
async def status(command: JaegerCommandType, fps: FPS):
    """Shows the status of the alerts."""

    return command.finish(
        message={k: int(v) for k, v in command.actor.alerts.keywords.items()}
    )


@alerts.command()
async def reset(command: JaegerCommandType, fps: FPS):
    """Clears all the alerts. Needed after certain alerts have been raised."""

    command.actor.alerts.reset()
    return command.finish(
        message={k: int(v) for k, v in command.actor.alerts.keywords.items()}
    )
