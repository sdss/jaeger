#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-24
# @Filename: snapshot.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor


__all__ = ["snapshot"]


@jaeger_parser.command()
@click.argument("PATH", required=False, type=click.Path(exists=False, dir_okay=False))
async def snapshot(command: Command[JaegerActor], fps: FPS, path: str | None = None):
    """Takes a snapshot image."""

    if path is not None:
        path = str(path)

    try:
        filename = await fps.save_snapshot(path, write_to_actor=False)
    except Exception as err:
        return command.fail(f"Snapshot failed with error: {err}")

    return command.finish(snapshot=filename)
