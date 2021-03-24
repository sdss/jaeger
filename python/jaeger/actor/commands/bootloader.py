#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-18
# @Filename: bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import click

from jaeger import JaegerError
from jaeger.commands.bootloader import load_firmware

from . import jaeger_parser


__all__ = ["bootloader"]


last_reported = 0.0


@jaeger_parser.group()
def bootloader():
    """Perform actions in bootloader mode."""

    pass


@bootloader.command()
@click.argument("FIRMWARE-FILE", nargs=1, type=click.Path(exists=True))
@click.option(
    "-s",
    "--positioners",
    type=str,
    help="Comma-separated positioners to upgrade",
)
async def upgrade(command, fps, firmware_file, positioners):
    """Upgrades the firmware for all positioners connected."""

    global last_reported
    last_reported = 0

    if positioners is not None:
        positioner_id = [
            int(positioner.strip()) for positioner in positioners.split(",")
        ]
    else:
        positioner_id = fps.positioner_to_bus.keys()

    command.debug(text="stopping pollers")
    await fps.pollers.stop()

    await fps.update_firmware_version(positioner_id=positioner_id)
    await fps.update_status(positioner_id=positioner_id)

    def report_progress(current_chunk, n_chunks):

        global last_reported

        perc_completed = current_chunk / n_chunks * 100.0

        # Report only after each 10% increase in completion
        if (perc_completed - last_reported) > 10.0:
            command.write("i", text=f"{int(perc_completed)}% completed")
            last_reported = int(perc_completed)

    command.write("i", text=f"starting load of firmware file {firmware_file!r}")

    try:
        result = await load_firmware(
            fps,
            firmware_file,
            positioners=positioner_id,
            show_progressbar=False,
            progress_callback=report_progress,
        )
    except JaegerError as ee:
        command.write("w", text=ee)
        result = False

    if not result:
        return command.fail(error="firmware upgrade failed.")

    command.info(text="firmware loaded. Waiting 10 seconds to exit bootloader mode.")

    await asyncio.sleep(11)

    command.info(text="restarting FPS")
    await fps.initialise()

    # Check that we really are in normal mode
    for positioner in fps.positioners.values():
        if positioner.is_bootloader():
            return command.fail(error="some positioner are still in bootloader mode.")

    return command.finish(text="firmware load complete.")
