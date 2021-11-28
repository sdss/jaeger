#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-27
# @Filename: can.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

from jaeger.can import JaegerCAN

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger import FPS

    from ..actor import JaegerActor

__all__ = ["can"]


@jaeger_parser.group()
def can():
    """Allows to connect/disconnect the CAN interfaces."""


@can.command()
async def connect(command: Command[JaegerActor], fps: FPS):
    """Connect the CAN interfaces."""

    if isinstance(fps.can, JaegerCAN):
        fps.can.stop()

    await fps.start_can()
    if fps.pid_lock is not None:
        fps.pid_lock.close()
        fps.pid_lock = None

    return command.finish(text="CAN has been reconnected.")


@can.command()
async def disconnect(command: Command[JaegerActor], fps: FPS):
    """Connect the CAN interfaces."""

    if isinstance(fps.can, JaegerCAN):
        fps.can.stop()

    return command.finish(text="CAN has been disconnected.")
