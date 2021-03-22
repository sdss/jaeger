#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import click

from clu.parser import pass_args

from jaeger.fps import IEB
from jaeger.testing import VirtualFPS

from . import jaeger_parser


__all__ = ["ieb"]


@jaeger_parser.group()
@pass_args()
def ieb(command, fps):
    """Manages the IEB."""

    ieb = fps.ieb

    if not ieb or ieb.disabled:
        command.fail(text="ieb not connected.")
        raise click.Abort()

    return


@ieb.command()
async def status(command, fps):
    """Outputs the status of the devices."""

    ieb = fps.ieb

    categories = set()
    for module in ieb.modules.values():
        new_categories = set(
            list(
                dev.category
                for dev in module.devices.values()
                if dev.category is not None
            )
        )
        categories = categories.union(new_categories)

    for category in categories:
        data = await ieb.read_category(category)
        measured = []
        for key, value in data.items():
            dev_name = ieb.get_device(key).name
            meas, units = value
            meas = round(meas, 3) if not isinstance(meas, str) else meas
            if meas == "closed":
                meas = "on"
            elif meas == "open":
                meas = "off"
            value_unit = f"{meas}" if not units else f"{meas} {units}"
            measured.append(f"{dev_name.lower()}={value_unit}")
        command.write("i", message="; ".join(measured))

    return command.finish()


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
    except ValueError:
        return command.fail(text=f"cannot find device {device!r}.")

    if device_obj.module.mode != "output":
        return command.fail(text=f"{dev_name!r} is not a relay.")

    if on is None:  # The --on/--off was not passed
        current_status = (await device_obj.read())[0]
        if current_status == "closed":
            on = False
        elif current_status == "open":
            on = True
        else:
            return command.fail(
                text=f"invalid status for device {dev_name!r}: {current_status!r}."
            )

    try:
        if on is True:
            await device_obj.close()
        elif on is False:
            await device_obj.open()
    except Exception:
        return command.fail(text=f"failed to set status of device {dev_name!r}.")

    if cycle:
        command.write("d", text="waiting 1 second before powering up.")
        await asyncio.sleep(1)
        try:
            await device_obj.close()
        except Exception:
            return command.fail(text=f"failed to power device {dev_name!r} back on.")

    status = "on" if (await device_obj.read())[0] == "closed" else "off"

    return command.finish(text=f"device {dev_name!r} is now {status!r}.")


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
        await sync.open()  # type: ignore

    if (await sync.read())[0] != "open":
        command.fail(error="Failed opening SYNC line.")
        return False

    command.debug(sync="off")

    for devname in seq:
        if isinstance(devname, str):
            dev = ieb.get_device(devname)

            if (await dev.read())[0] == relay_result:
                command.debug(text=f"{devname} alredy powered {mode}.")
            else:
                command.debug(text=f"Powering {mode} {devname}.")
                await dev.close() if mode == "on" else dev.open()  # type: ignore
                if (await dev.read())[0] != relay_result:
                    command.fail(error=f"Failed powering {mode} {devname}.")
                    return False

            command.debug({devname.lower(): "on"})

        elif isinstance(devname, (tuple, list)):
            devname = list(devname)
            devs = [ieb.get_device(dn) for dn in devname]

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

            await asyncio.gather(*[dev.close() for dev in devs])  # type: ignore

            status = list(await asyncio.gather(*[dev.read() for dev in devs]))
            for ii, res in enumerate(status):
                if res[0] != "closed":
                    command.fail(error=f"Failed powering {mode} {devname[ii]}.")
                    return False

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
