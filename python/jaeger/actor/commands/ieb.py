#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import math

import click

from clu.parsers.click import pass_args

from jaeger.fps import FPS
from jaeger.ieb import IEB
from jaeger.testing import VirtualFPS

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
async def status(command, fps):
    """Outputs the status of the devices."""

    categories = fps.ieb.get_categories()

    status_data = {}
    for category in categories:
        if category not in command.actor.model.schema["properties"]:
            command.warning(f"Unknown device category {category!r}.")
        status_data[category] = await _get_category_data(command, category)

    return command.finish(message=status_data, concatenate=True)


@ieb.command()
@click.argument("DEVICE", type=str)
@click.option(
    "--on/--off",
    default=None,
    help="the value of the device. If not provided, switches the current status.",
)
@click.option(
    "--cycle",
    is_flag=True,
    help="power cycles a relay. The final status is on.",
)
async def switch(command, fps, device, on, cycle):
    """Switches the status of an on/off device."""

    ieb = fps.ieb

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

    command.debug(message={category: await _get_category_data(command, category)})

    if cycle:
        command.write("d", text="waiting 1 second before powering up.")
        await asyncio.sleep(1)
        try:
            await device_obj.close()
        except Exception:
            return command.fail(error=f"failed to power device {dev_name!r} back on.")

    status = "on" if (await device_obj.read())[0] == "closed" else "off"

    return command.finish(
        message={
            "text": f"device {dev_name!r} is now {status!r}.",
            category: await _get_category_data(command, category),
        }
    )


async def _power_sequence(command, ieb, seq, mode="on", delay=1) -> bool:
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

    command.debug(sync=False)

    for devname in seq:
        if isinstance(devname, str):
            dev = ieb.get_device(devname)
            category = dev.category.lower()

            if (await dev.read())[0] == relay_result:
                command.debug(text=f"{devname} alredy powered {mode}.")
            else:
                command.debug(text=f"Powering {mode} {devname}.")
                await dev.close() if mode == "on" else dev.open()
                if (await dev.read())[0] != relay_result:
                    command.fail(error=f"Failed powering {mode} {devname}.")
                    return False

            command.debug({category: await _get_category_data(command, category)})

        elif isinstance(devname, (tuple, list)):
            devname = list(devname)
            devs = [ieb.get_device(dn) for dn in devname]
            category = devs[0].category.lower()

            status = list(await asyncio.gather(*[dev.read() for dev in devs]))

            keep = []
            for ii, res in enumerate(status):
                if res[0] == relay_result:
                    command.debug(
                        {
                            "text": f"{devname[ii]} alredy powered {mode}.",
                            devname[ii].lower(): relay_result,
                        }
                    )
                    continue
                keep.append(ii)

            devs = [devs[ii] for ii in keep]
            devname = [devname[ii] for ii in keep]

            command.debug(text=f"Powering {mode} {', '.join(devname)}")

            await asyncio.gather(*[dev.close() for dev in devs])

            status = list(await asyncio.gather(*[dev.read() for dev in devs]))
            for ii, res in enumerate(status):
                if res[0] != "closed":
                    command.fail(error=f"Failed powering {mode} {devname[ii]}.")
                    return False

            command.debug({category: await _get_category_data(command, category)})

        else:
            command.fail(error=f"Invalid relay {devname!r}.")
            return False

        await asyncio.sleep(delay)

    return True


@ieb.group()
@pass_args()
def power(command, fps):
    """Runs the power on/off sequences."""

    pass


@power.command()
async def on(command, fps):
    """Powers on all the FPS IEB components."""

    ieb: IEB = fps.ieb

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
        "GFA1",
        "GFA2",
        "GFA3",
        "GFA4",
        "GFA5",
        "GFA6",
        "NUC1",
        "NUC2",
        "NUC3",
        "NUC4",
        "NUC5",
        "NUC6",
    ]

    if not (await _power_sequence(command, ieb, on_seq, mode="on")):
        return

    return command.finish(text="Power on sequence complete.")


@power.command()
@click.option("--nucs", is_flag=True, help="Also power down NUCs")
async def off(command, fps, nucs):
    """Powers off all the FPS IEB components."""

    ieb: IEB = fps.ieb

    # Sequence of relays to power off. Tuples indicate relays that can be powered
    # off concurrently.
    off_seq = [
        ("CM1", "CM2", "CM3", "CM4", "CM5", "CM6"),
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
        off_seq += [
            "NUC1",
            "NUC2",
            "NUC3",
            "NUC4",
            "NUC5",
            "NUC6",
        ]

    if not (await _power_sequence(command, ieb, off_seq, mode="off")):
        return

    return command.finish(text="Power off sequence complete.")


@ieb.command()
@click.argument("device_name", metavar="DEVICE", type=str)
@click.argument("VALUE", type=click.FloatRange(0, 100))
async def fbi(command, fps: FPS, device_name: str, value: float):
    """Control the power output (0-100%) of the fibre back illuminator (FBI)."""

    raw_value = 32 * int(1023 * (value / 100))

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
