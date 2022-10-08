#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-01-27
# @Filename: chiller.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import click

from jaeger.ieb import Chiller

from . import JaegerCommandType, command_parser


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = ["chiller"]


@command_parser.group()
def chiller(*args):
    """Shows the status and controlls the chiller."""

    pass


@chiller.command()
async def status(command: JaegerCommandType, fps: FPS):
    """Shows the status of the chiller."""

    chiller = Chiller.create()
    if chiller is None:
        return command.fail("Cannot access the chiller.")

    names = [
        ("chiller_temperature_value", "DISPLAY_VALUE"),
        ("chiller_temperature_setpoint", "TEMPERATURE_USER_SETPOINT"),
        ("chiller_flow_value", "STATUS_FLUID_FLOW"),
        ("chiller_flow_setpoint", "FLOW_USER_SETPOINT"),
    ]

    keywords = {}

    for _ in range(10):  # Try ten times or fail.
        try:
            for key, dev_name in names:
                value = round((await chiller.read_device(dev_name))[0], 1)
                keywords[key] = value
        except Exception:
            await asyncio.sleep(1)
            continue

        # Broadcast so that we can call it from chiller set.
        command.info(message=keywords, broadcast=True)

        return command.finish()

    return command.fail("Timed out getting chiller status.")


@chiller.command()
async def disable(command: JaegerCommandType, fps: FPS):
    """Disables the chiller watcher."""

    if not command.actor.chiller:
        return command.fail("The chiller bot is not running.")

    await command.actor.chiller.stop()

    return command.finish("Chiller watcher has been disabled.")


@chiller.command()
@click.argument("MODE", type=click.Choice(["temperature", "flow"]))
@click.argument("VALUE", type=str)
async def set(command: JaegerCommandType, fps: FPS, mode: str, value: str | float):
    """Shows the temperature or flow of the chiller.

    The value of the chiller temperature or flow can be set to auto, disable,
    or a fixed numerical value.

    """

    actor = command.actor
    assert actor

    if isinstance(value, str) and value.lower() not in ["disable", "auto"]:
        try:
            value = float(value)
        except ValueError:
            return command.fail(f"Invalid value {value!r}.")

        if mode == "temperature" and value < 0.1:
            return command.fail("Minimum temperature is 0.1 C.")

        if mode == "flow" and (value < 0.1 or value > 15):
            return command.fail("Invalid flow rate.")

    if command.actor.chiller is None:
        return command.fail("The chiller bot does not exist.")

    if value == "auto":
        setattr(command.actor.chiller, mode, True)
        command.info(f"Chiller {mode} set to auto.")
    elif value == "disable":
        setattr(command.actor.chiller, mode, False)
        command.info(f"Chiller {mode} tracking disabled.")
    else:
        setattr(command.actor.chiller, mode, value)

    await command.actor.chiller.restart()
    await asyncio.sleep(3)
    await command.send_command("jaeger", "chiller status")

    return command.finish()
