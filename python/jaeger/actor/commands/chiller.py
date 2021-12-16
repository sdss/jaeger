from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import click
from numpy import broadcast

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
@click.argument("MODE", type=click.Choice(["temperature", "flow"]))
@click.argument("VALUE", type=float)
async def set(command: JaegerCommandType, fps: FPS, mode: str, value: float | int):
    """Shows the temperature or flow of the chiller."""

    chiller = Chiller.create()

    if mode == "temperature":
        dev_name = "TEMPERATURE_USER_SETPOINT"
    else:
        dev_name = "FLOW_USER_SETPOINT"

    device = chiller.get_device(dev_name)

    value = int(value * 10)

    for _ in range(10):
        try:
            await device.write(value)
        except Exception:
            await asyncio.sleep(1)
            continue

        await command.send_command("jaeger", "chiller status")
        return command.finish("Value set.")

    return command.fail("Timed out setting chiller values.")
