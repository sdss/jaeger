#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Tuple

import click

from clu.parsers.click import pass_args

from jaeger.fps import FPS
from jaeger.ieb import IEB, _get_category_data

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from .. import JaegerActor


__all__ = ["ieb"]


@jaeger_parser.group()
@pass_args()
@click.pass_context
def ieb(ctx, command, fps):
    """Manages the IEB."""

    ieb = fps.ieb

    if not ieb or (ieb.disabled and ctx.invoked_subcommand != "enable"):
        command.fail(error="ieb not connected.")
        raise click.Abort()

    return


@ieb.command()
async def enable(command, fps):
    """Re-enables the IEB."""

    if fps.ieb is None or not isinstance(fps.ieb, IEB):
        return command.fail("IEB object does not exist.")

    fps.ieb.enable()

    return command.finish()


@ieb.command()
async def disable(command, fps):
    """Disables the IEB."""

    if fps.ieb is None or not isinstance(fps.ieb, IEB):
        return command.fail("IEB object does not exist.")

    fps.ieb.disabled = True

    return command.finish()


@ieb.command()
async def status(command, fps):
    """Outputs the status of the devices."""

    categories = sorted(fps.ieb.get_categories())

    status_data = {}
    for category in categories:
        if category not in command.actor.model.schema["properties"]:
            command.warning(f"Unknown device category {category!r}.")
        status_data[category] = await _get_category_data(command, category)

    return command.finish(message=status_data, concatenate=True)


@ieb.command()
@click.argument("device_names", metavar="DEVICES", type=str, nargs=-1)
@click.argument("VALUE", type=click.FloatRange(0, 100))
async def fbi(command, fps: FPS, device_names: Tuple[str], value: float):
    """Control the power output (0-100%) of the fibre back illuminator (FBI)."""

    raw_value = 32 * int(1023 * (value / 100))

    if not isinstance(fps.ieb, IEB) or fps.ieb.disabled:
        return command.fail(error="IEB is not conencted or is disabled.")

    if len(device_names) == 0:
        return command.fail(error="No devices provided.")

    for device_name in device_names:
        try:
            device = fps.ieb.get_device(device_name)
        except ValueError:
            return command.fail(error=f"Cannot find device {device_name!r}.")

        if device.mode != "holding_register":
            return command.fail(
                error=f"Invalid device mode for {device_name!r}: {device.__type__}."
            )

        await device.write(raw_value)

    await asyncio.sleep(0.2)
    fbi_led = await _get_category_data(command, "fbi_led")

    return command.finish(fbi_led=fbi_led)


@ieb.command()
@click.option("-v", "--verbose", is_flag=True, help="Shows extra information.")
async def info(command: Command[JaegerActor], fps: FPS, verbose=False):
    """Shows information about the IEB layout in a human-readable format."""

    ieb = fps.ieb

    if not isinstance(ieb, IEB):
        return command.fail(error="IEB is not conencted or is disabled.")

    modules = sorted(ieb.modules.keys())
    categories = set()

    command.info(text="Modules:")

    for module_name in modules:
        module = ieb.modules[module_name]

        command.info(text=f"  {module.name}:")

        if module.description != "":
            command.info(text=f"    description: {module.description}")

        if verbose:
            if module.model:
                command.info(text=f"    model: {module.model}")
            if module.mode:
                command.info(text=f"    mode: {module.mode}")
            if module.channels:
                command.info(text=f"    channels: {module.channels}")

        command.info(text="    Devices:")

        for dev in ieb[module_name].devices.values():
            command.info(text=f"      {dev.name}:")
            if dev.category != "":
                command.info(text=f"        category: {dev.category}")
                categories.add(dev.category)
            if dev.description != "":
                command.info(text=f"        description: {dev.description}")

            if verbose:
                if dev.address:
                    command.info(text=f"        address: {dev.address}")
                if dev.channel:
                    command.info(text=f"        channel: {dev.channel}")
                if dev.__type__:
                    command.info(text=f"        type: {dev.__type__}")
                    if dev.__type__ == "relay" and dev.relay_type:
                        command.info(text=f"        relay type: {dev.relay_type}")

    if len(categories) > 0 and command.actor and command.actor.model:
        command.info(text="")
        command.info(text="Keywords:")

        for category in sorted(categories):
            cat_data = command.actor.model.schema["properties"][category]
            items = [item["title"] for item in cat_data["items"]]
            command.info(text=f"  {category}=[{', '.join(items)}]")

    return command.finish()
