#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-27
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, cast

import click

from clu.parsers.click import cancellable
from drift import DriftError, Relay

from jaeger import config
from jaeger.exceptions import FVCError
from jaeger.fvc import FVC
from jaeger.ieb import FVC as FVC_IEB
from jaeger.utils import run_in_executor

from . import jaeger_parser


if TYPE_CHECKING:
    from clu import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor


__all__ = ["fvc_parser"]


# Reusable FVC instance. Do not reinitialise so that we don't lose PID info.
fvc = FVC(config["observatory"])


@jaeger_parser.group(name="fvc")
def fvc_parser():
    """Commands to command the FVC."""

    pass


@fvc_parser.command()
@click.argument("EXPOSURE-TIME", default=None, type=float, required=False)
async def expose(
    command: Command[JaegerActor],
    fps: FPS,
    exposure_time: Optional[float] = None,
):
    """Takes an exposure with the FVC."""

    exposure_time = exposure_time or config["fvc"]["exposure_time"]
    assert isinstance(exposure_time, float)

    command.info("Taking FVC exposure with fliswarm.")

    try:
        fvc.set_command(command)
        filename = await fvc.expose(exposure_time=exposure_time)
    except FVCError as err:
        return command.fail(error=f"Failed taking FVC exposure: {err}")

    return command.finish(fvc_filename=str(filename))


@fvc_parser.command()
@click.option("--exposure-time", type=float, help="Exposure time.")
@click.option("--fbi-level", default=1.0, type=float, help="FBI LED levels.")
@click.option("--use-last", is_flag=True, help="Uses the last available exposure.")
@click.option("--one", is_flag=True, help="Only runs one FVC correction iteration.")
@click.option("--plot/--no-plot", default=True, help="Generate and save plots.")
@cancellable()
async def loop(
    command: Command[JaegerActor],
    fps: FPS,
    exposure_time: float | None = None,
    fbi_level: float = 1.0,
    use_last: bool = False,
    one: bool = False,
    plot: bool = True,
):
    """Executes the FVC correction loop.

    This routine will turn the FBI LEDs on, take FVC exposures, process them,
    calculate the offset correction and applies them. Loops until the desided
    convergence is achieved.

    """

    exposure_time = exposure_time or config["fvc"]["exposure_time"]
    assert isinstance(exposure_time, float)

    if fps.configuration is None:
        return command.fail("Configuration not loaded.")

    fvc.set_command(command)

    n = 1
    while True:
        command.info(f"FVC iteration {n}")

        if n == 1:
            command.debug("Turning LEDs on.")
            await command.send_command("jaeger", f"ieb fbi led1 {fbi_level}")
            await command.send_command("jaeger", f"ieb fbi led2 {fbi_level}")

        command.debug("Taking exposure with fliswarm.")
        filename = await fvc.expose(exposure_time=exposure_time)

        raw_hdu, measured, centroids = await run_in_executor(
            fvc.process_fvc_image,
            filename,
            plot=plot,
        )

        new_file = filename.with_name("proc-" + filename.name)
        await fvc.write_proc_image(new_file, raw_hdu, measured, centroids)

        n += 1

        break

    command.debug("Turning LEDs off.")
    await command.send_command("jaeger", "ieb fbi led1 0")
    await command.send_command("jaeger", "ieb fbi led2 0")

    command.finish("FVC loop complete.")


@fvc_parser.command()
async def status(command: Command[JaegerActor], fps: FPS):
    """Reports the status of the FVC."""

    fvc_ieb = FVC_IEB.create()

    try:
        status = {}
        categories = fvc_ieb.get_categories()
        for category in sorted(categories):
            cat_data = await fvc_ieb.read_category(category)
            status[category] = []
            for cd in cat_data:
                value = cat_data[cd][0]
                if value == "closed":
                    value = True
                elif value == "open":
                    value = False
                else:
                    value = round(value, 1)
                status[category].append(value)

        command.finish(status)

    except DriftError:
        return command.fail(error="FVC IEB is unavailable or failed to connect.")


async def _power_device(device: str, mode: str):
    """Power on/off the device."""

    fvc_ieb = FVC_IEB.create()

    dev: Relay = cast(Relay, fvc_ieb.get_device(device))
    if mode == "on":
        await dev.close()
    else:
        await dev.open()


async def _execute_on_off_command(
    command: Command[JaegerActor], device: str, mode: str
):
    """Executes the on/off command."""

    mode = mode.lower()

    try:
        await _power_device(device, mode)
        command.info(text=f"{device} is now {mode}.")
    except DriftError:
        return command.fail(error=f"Failed to turn {device} {mode}.")

    await command.send_command("jaeger", "fvc status")

    return command.finish()


@fvc_parser.command()
@click.argument("MODE", type=click.Choice(["on", "off"], case_sensitive=False))
async def camera(command: Command[JaegerActor], fps: FPS, mode: str):
    """Turns camera on/off."""

    await _execute_on_off_command(command, "FVC", mode)


@fvc_parser.command()
@click.argument("MODE", type=click.Choice(["on", "off"], case_sensitive=False))
async def NUC(command: Command[JaegerActor], fps: FPS, mode: str):
    """Turns NUC on/off."""

    await _execute_on_off_command(command, "NUC", mode)


@fvc_parser.command()
@click.argument("LEVEL", type=int)
async def led(command: Command[JaegerActor], fps: FPS, level: int):
    """Sets the level of the FVC LED."""

    fvc_ieb = FVC_IEB.create()
    led = fvc_ieb.get_device("LED1")

    raw_value = 32 * int(1023 * (level / 100))
    await led.write(raw_value)

    await command.send_command("jaeger", "fvc status")

    return command.finish()
