#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-18
# @Filename: pollers.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import click

from . import jaeger_parser


__all__ = ["pollers"]


@jaeger_parser.group()
def pollers():
    """Handle the positioner pollers."""

    pass


@pollers.command()
async def list(command, fps):
    """Lists available pollers."""

    poller_status = []
    for name in fps.pollers.names:
        poller_status.append(name + ("*" if fps.pollers[name].running else ""))

    command.finish(text=",".join(poller_status))


@pollers.command()
@click.argument("POLLER", type=str, required=False)
async def stop(command, fps, poller):
    """Stop pollers."""

    if poller is None:
        await fps.pollers.stop()
    else:
        if poller not in fps.pollers.names:
            command.fail(error="poller not found.")
        await fps.pollers[poller].stop()

    command.finish(text="pollers stopped")


@pollers.command()
@click.argument("POLLER", type=str, required=False)
async def start(command, fps, poller):
    """Start pollers."""

    if poller is None:
        fps.pollers.start()
    else:
        if poller not in fps.pollers.names:
            command.fail(error="poller not found.")
        fps.pollers[poller].start()

    command.finish(text="pollers started")
