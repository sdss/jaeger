#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-24
# @Filename: snapshot.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor


__all__ = ["snapshot"]


@jaeger_parser.command()
async def snapshot(command: Command[JaegerActor], fps: FPS):
    """Takes a snapshot image."""

    filename = await fps.save_snapshot()

    return command.finish(snapshot=filename)
