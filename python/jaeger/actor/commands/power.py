#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-12-04
# @Filename: power.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any

import click

from clu.parsers.click import pass_args
from drift import Relay

from jaeger import config
from jaeger.ieb import IEB, _get_category_data
from jaeger.testing import VirtualFPS

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger import FPS

    from .. import JaegerActor


__all__ = ["power"]


async def switch(
    command: Command[JaegerActor],
    fps: FPS,
    devices: list[str],
    on: bool | None = None,
    cycle: bool = False,
    delay: float = 1.0,
    force: bool = False,
):
    """Switches the status of an on/off device."""

    ieb = fps.ieb
    assert isinstance(ieb, IEB)

    if len(devices) == 0:
        return command.fail(error="No devices specified.")

    for idev, device in enumerate(devices):
        if device.upper() in config["ieb"]["disabled_devices"] and on is True:
            if force is False:
                command.warning(text=f"{device} is disabled. Skipping.")
                continue
            else:
                command.warning(text=f"{device} is disabled but overriding.")

        if len(devices) > 1 and idev != 0:
            await asyncio.sleep(delay)

        if cycle:
            on = False

        try:
            device_obj = ieb.get_device(device)
            assert isinstance(device_obj, Relay)
            dev_name = device_obj.name
        except ValueError:
            return command.fail(error=f"Cannot find device {device!r}.")

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
                    error=f"Invalid status for device {dev_name!r}: {current_status!r}."
                )

        try:
            if on is True:
                await device_obj.close()
            elif on is False:
                await device_obj.open()
        except Exception:
            return command.fail(error=f"Failed to set status of device {dev_name!r}.")

        if cycle:
            command.write("d", text="Waiting 1 second before powering up.")
            await asyncio.sleep(1)
            try:
                await device_obj.close()
            except Exception:
                return command.fail(
                    error=f"Failed to power device {dev_name!r} back on."
                )

        # If we read the status immediately sometimes we still get the old one.
        # Sleep a bit to avoid that.
        await asyncio.sleep(0.2)

        status = "on" if (await device_obj.read())[0] == "closed" else "off"

        message: dict[str, Any] = {"text": f"Device {dev_name!r} is now {status!r}."}
        category = device_obj.category
        if category:
            message[category] = await _get_category_data(command, category)

        command.info(message=message)

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

    disabled = config["ieb"]["disabled_devices"]

    for devname in seq:
        do_delay = False
        if isinstance(devname, str):
            if devname.upper() in config["ieb"]["disabled_devices"]:
                command.warning(text=f"{devname} is disabled. Skipping.")
                continue

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
            devname = list([dn for dn in devname if dn not in disabled])
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


@jaeger_parser.group()
@pass_args()
def power(command, fps):
    """Runs the power on/off sequences and powers individual devices."""

    pass


@power.command()
@click.argument("DEVICES", type=str, nargs=-1)
@click.option("--gfas", is_flag=True, help="Power on the GFAs.")
@click.option(
    "--delay",
    type=float,
    default=3,
    help="When powering multiple devices, the delay to wait between them.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Forces a device to turn on/off even if disabled.",
)
async def on(
    command: Command[JaegerActor],
    fps: FPS,
    devices: list[str],
    gfas: bool = False,
    delay: float = 3,
    force: bool = True,
):
    """Powers on all the FPS IEB components or a specific device."""

    if len(devices) > 0:
        return await switch(command, fps, devices, on=True, force=force, delay=delay)

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

    if gfas is True:
        on_seq += [
            "GFA1",
            "GFA2",
            "GFA3",
            "GFA4",
            "GFA5",
            "GFA6",
        ]

    if not (await _power_sequence(command, ieb, on_seq, mode="on", delay=int(delay))):
        return

    command.info("Waiting 15 seconds and reloading positioners.")
    await asyncio.sleep(15)

    await fps.initialise()

    return command.finish(text="Power on sequence complete.")


@power.command()
@click.argument("DEVICES", type=str, nargs=-1)
@click.option("--nucs", is_flag=True, help="Also power down NUCs")
@click.option(
    "--delay",
    type=float,
    default=1,
    help="When powering multiple devices, the delay to wait between them.",
)
async def off(
    command: Command[JaegerActor],
    fps: FPS,
    devices: list[str],
    nucs: bool = False,
    delay: float = 1,
):
    """Powers off all the FPS IEB components or a specific device."""

    if len(devices) > 0:
        return await switch(command, fps, devices, on=False, force=True, delay=delay)

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

    if not (await _power_sequence(command, ieb, off_seq, mode="off", delay=int(delay))):
        return

    command.info("Waiting 15 seconds and reloading positioners.")
    await asyncio.sleep(15)

    await fps.initialise()

    return command.finish(text="Power off sequence complete.")
