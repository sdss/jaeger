#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import math

from typing import Tuple

import click

from clu.command import Command
from clu.parsers.click import pass_args

from jaeger.fps import FPS
from jaeger.ieb import IEB
from jaeger.testing import VirtualFPS

from .. import JaegerActor
from . import jaeger_parser


__all__ = ["ieb"]


async def _get_category_data(command, category) -> list:

    ieb = command.actor.fps.ieb
    schema = command.actor.model.schema

    items = schema["properties"][category]["items"]
    measured = []

    async with ieb:
        for item in items:
            name = item["title"]
            type_ = item["type"]
            device = ieb.get_device(name)
            value = (await device.read(connect=False))[0]
            if type_ == "boolean" and device.__type__ == "relay":
                value = True if value == "closed" else False
            elif type_ == "integer":
                value = int(value)
            elif type_ == "number":
                if "multipleOf" in item:
                    precision = int(-math.log10(item["multipleOf"]))
                else:
                    precision = 3
                value = round(value, precision)
            measured.append(value)

    return measured


@jaeger_parser.group()
@pass_args()
def ieb(command, fps):
    """Manages the IEB."""

    ieb = fps.ieb

    if not ieb or ieb.disabled:
        command.fail(error="ieb not connected.")
        raise click.Abort()

    return


@ieb.command()
async def enable(command, fps):
    """Re-enables the IEB."""

    if fps.ieb is None or not isinstance(fps.ieb, IEB):
        return command.fail("IEB object does not exist.")

    fps.ieb.disabled = False

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
@click.argument("DEVICES", type=str, nargs=-1)
@click.option(
    "--on/--off",
    default=None,
    help="the desired state of the device. If not passed, switches the current state.",
)
@click.option(
    "--cycle",
    is_flag=True,
    help="power cycles a relay. The final status is on.",
)
@click.option(
    "--delay",
    type=float,
    default=1,
    help="When powering multiple devices, the delay to wait between them.",
)
async def switch(command, fps, devices=(), on=None, cycle=False, delay=1):
    """Switches the status of an on/off device."""

    ieb = fps.ieb

    if len(devices) == 0:
        return command.fail(error="No devices specified.")

    for idev, device in enumerate(devices):

        if len(devices) > 1 and idev != 0:
            await asyncio.sleep(delay)

        if cycle:
            on = False

        try:
            device_obj = ieb.get_device(device)
            dev_name = device_obj.name
            category = device_obj.category.lower()
        except ValueError:
            return command.fail(error=f"cannot find device {device!r}.")

        if device_obj.module.mode not in ["holding_register", "coil"]:
            return command.fail(error=f"{dev_name!r} is not an output.")

        if on is None:  # The --on/--off was not passed
            current_status = (await device_obj.read())[0]
            if current_status == "closed":
                on = False
            elif current_status == "open":
                on = True
            else:
                return command.fail(
                    error=f"invalid status for device {dev_name!r}: {current_status!r}."
                )

        try:
            if on is True:
                await device_obj.close()
            elif on is False:
                await device_obj.open()
        except Exception:
            return command.fail(error=f"failed to set status of device {dev_name!r}.")

        if cycle:
            command.write("d", text="waiting 1 second before powering up.")
            await asyncio.sleep(1)
            try:
                await device_obj.close()
            except Exception:
                return command.fail(
                    error=f"failed to power device {dev_name!r} back on."
                )

        # If we read the status immediately sometimes we still get the old one.
        # Sleep a bit to avoid that.
        await asyncio.sleep(0.2)

        status = "on" if (await device_obj.read())[0] == "closed" else "off"

        command.info(
            message={
                "text": f"device {dev_name!r} is now {status!r}.",
                category: await _get_category_data(command, category),
            }
        )

    return command.finish()


async def _power_sequence(command, ieb, seq, mode="on", delay=3) -> bool:
    """Applies the power on/off sequence."""

    # To speed up tests
    if isinstance(command.actor.fps, VirtualFPS):
        delay = 0.01

    relay_result = "closed" if mode == "on" else "open"

    command.info(text=f"Running power {mode} sequence")

    # First check that the SYNC line is open.
    sync = ieb.get_device("SYNC")
    if (await sync.read())[0] == "closed":
        command.debug(text="SYNC line is high. Opening it.")
        await sync.open()

    if (await sync.read())[0] != "open":
        command.fail(error="Failed opening SYNC line.")
        return False

    command.debug(power_sync=[False])

    for devname in seq:
        do_delay = False
        if isinstance(devname, str):
            dev = ieb.get_device(devname)
            category = dev.category.lower()

            if (await dev.read())[0] == relay_result:
                command.debug(text=f"{devname} already powered {mode}.")
            else:
                command.debug(text=f"Powering {mode} {devname}.")
                await dev.close() if mode == "on" else await dev.open()
                await asyncio.sleep(0.2)
                if (await dev.read())[0] != relay_result:
                    command.fail(error=f"Failed powering {mode} {devname}.")
                    return False

                do_delay = True

            command.debug({category: await _get_category_data(command, category)})

        elif isinstance(devname, (tuple, list)):
            devname = list(devname)
            devs = [ieb.get_device(dn) for dn in devname]
            category = devs[0].category.lower()

            status = list(await asyncio.gather(*[dev.read() for dev in devs]))

            keep = []
            for ii, res in enumerate(status):
                if res[0] == relay_result:
                    command.debug({"text": f"{devname[ii]} already powered {mode}."})
                    continue
                keep.append(ii)

            devs = [devs[ii] for ii in keep]
            devname = [devname[ii] for ii in keep]

            if len(devs) > 0:
                command.debug(text=f"Powering {mode} {', '.join(devname)}")
                await asyncio.gather(
                    *[dev.close() if mode == "on" else dev.open() for dev in devs]
                )
                do_delay = True

            status = list(await asyncio.gather(*[dev.read() for dev in devs]))
            for ii, res in enumerate(status):
                if res[0] != relay_result:
                    command.fail(error=f"Failed powering {mode} {devname[ii]}.")
                    return False

            command.debug({category: await _get_category_data(command, category)})

        else:
            command.fail(error=f"Invalid relay {devname!r}.")
            return False

        if do_delay:
            await asyncio.sleep(delay)

    return True


@ieb.group()
@pass_args()
def power(command, fps):
    """Runs the power on/off sequences."""

    pass


@power.command()
@click.option("--no-gfas", is_flag=True, help="Do not power the GFAs.")
async def on(command, fps: FPS, no_gfas: bool = False):
    """Powers on all the FPS IEB components."""

    command.info(text="Turning pollers off.")
    await fps.pollers.stop()

    ieb = fps.ieb
    if not isinstance(ieb, IEB) or ieb.disabled:
        return command.fail(error="IEB is not conencted or is disabled.")

    # Sequence of relays to power on. Tuples indicate relays that can be powered
    # on concurrently.
    on_seq = [
        ("CM1", "CM2", "CM3", "CM4", "CM5", "CM6"),
        "PS1",
        "PS2",
        "PS3",
        "PS4",
        "PS5",
        "PS6",
        "NUC1",
        "NUC2",
        "NUC3",
        "NUC4",
        "NUC5",
        "NUC6",
    ]

    if no_gfas is False:
        on_seq += [
            "GFA1",
            "GFA2",
            "GFA3",
            "GFA4",
            "GFA5",
            "GFA6",
        ]

    if not (await _power_sequence(command, ieb, on_seq, mode="on")):
        return

    return command.finish(text="Power on sequence complete.")


@power.command()
@click.option("--nucs", is_flag=True, help="Also power down NUCs")
async def off(command, fps, nucs):
    """Powers off all the FPS IEB components."""

    command.info(text="Turning pollers off.")
    await fps.pollers.stop()

    ieb = fps.ieb
    if not isinstance(ieb, IEB) or ieb.disabled:
        return command.fail(error="IEB is not conencted or is disabled.")

    # Sequence of relays to power off. Tuples indicate relays that can be powered
    # off concurrently.
    off_seq = [
        "PS1",
        "PS2",
        "PS3",
        "PS4",
        "PS5",
        "PS6",
        "GFA1",
        "GFA2",
        "GFA3",
        "GFA4",
        "GFA5",
        "GFA6",
    ]

    if nucs:
        off_seq += ["NUC1", "NUC2", "NUC3", "NUC4", "NUC5", "NUC6"]

    if not (await _power_sequence(command, ieb, off_seq, mode="off")):
        return

    return command.finish(text="Power off sequence complete.")


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

    return command.finish()


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
