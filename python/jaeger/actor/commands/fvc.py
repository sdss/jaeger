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

    fvc = FVC(config["observatory"])

    try:
        fvc.set_command(command)
        filename = await fvc.expose(exposure_time=exposure_time)
    except FVCError as err:
        return command.fail(error=f"Failed taking FVC exposure: {err}")

    return command.finish(fvc_filename=str(filename))


@fvc_parser.command(cancellable=True)
@click.option(
    "--exposure-time",
    type=float,
    help="Exposure time.",
)
@click.option(
    "--fbi-level",
    type=float,
    help="FBI LED levels.",
)
@click.option(
    "--one",
    is_flag=True,
    help="Only runs one FVC correction iteration.",
)
@click.option(
    "--max-iterations",
    type=int,
    help="Maximum number of iterations.",
)
@click.option(
    "--stack",
    type=int,
    default=1,
    help="Number of FVC image to stack.",
)
@click.option(
    "--plot/--no-plot",
    default=True,
    help="Generate and save plots.",
)
@click.option(
    "--apply/--no-apply",
    default=True,
    help="Apply corrections.",
)
@click.option(
    "-m",
    "--max-correction",
    type=float,
    help="Maximum correction allowed, in degrees.",
)
@click.option(
    "--target-90-percentile",
    type=float,
    help="90% percentile target, in microns, at which to stop iterating.",
)
@click.option(
    "-k",
    type=float,
    help="Proportional term of the correction.",
)
@click.option(
    "--use-winpos/--no-use-winpos",
    default=True,
    help="Use windowed positions during centroid extraction.",
)
@click.option(
    "--use-invkin/--no-use-invkin",
    default=True,
    help="Use new inverse kinnematics.",
)
@click.option(
    "--no-write-summary",
    is_flag=True,
    help="Does not try to write a confSummaryF file.",
)
async def loop(
    command: Command[JaegerActor],
    fps: FPS,
    exposure_time: float | None = None,
    fbi_level: float | None = None,
    one: bool = False,
    max_iterations: int | None = None,
    stack: int = 3,
    plot: bool = True,
    apply: bool = True,
    max_correction: float | None = None,
    target_90_percentile: float | None = None,
    k: float | None = None,
    use_winpos: bool = True,
    use_invkin: bool = True,
    no_write_summary: bool = False,
):
    """Executes the FVC correction loop.

    This routine will turn the FBI LEDs on, take FVC exposures, process them,
    calculate the offset correction and applies them. Loops until the desided
    convergence is achieved.

    """

    exposure_time = exposure_time or config["fvc"]["exposure_time"]
    fbi_level = fbi_level or config["fvc"]["fbi_level"]
    assert isinstance(exposure_time, float) and isinstance(fbi_level, float)

    if fps.configuration is None:
        return command.fail("Configuration not loaded.")

    fvc = FVC(fps.observatory, command=command)

    # Check that the rotator is halted.
    axis_cmd = await command.send_command("keys", "getFor=tcc AxisCmdState")
    if axis_cmd.status.did_fail:
        command.warning("Cannot check the status of the rotator.")
    else:
        rot_status = axis_cmd.replies.get("AxisCmdState")[2]
        if rot_status != "Halted":
            return command.fail(f"Cannot expose FVC while the rotator is {rot_status}.")
        else:
            command.debug("The rotator is halted.")

    command.debug("Turning LEDs on.")
    await command.send_command("jaeger", f"ieb fbi led1 {fbi_level}")
    await command.send_command("jaeger", f"ieb fbi led2 {fbi_level}")

    if one is True and apply is True:
        command.warning(
            "One correction will be applied. The confSummaryF "
            "file will not reflect the final state."
        )

    max_iterations = max_iterations or config["fvc"]["max_fvc_iterations"]
    target_90_percentile = target_90_percentile or config["fvc"]["target_90_percentile"]

    current_rms = None
    delta_rms = None

    filename = None
    proc_image_saved = False

    # Flag to determine when to exit the loop.
    reached: bool = False
    failed: bool = False

    try:

        n = 1
        while True:
            command.info(f"FVC iteration {n}")

            filename = None
            fvc.proc_hdu = None
            proc_image_saved: bool = False

            # 1. Expose the FVC
            command.debug("Taking exposure with fliswarm.")
            filename = await fvc.expose(exposure_time=exposure_time, stack=stack)
            command.debug(fvc_filename=str(filename))

            # 2. Process the new image.
            positioner_coords = fps.get_positions_dict()
            await run_in_executor(
                fvc.process_fvc_image,
                filename,
                positioner_coords,
                plot=plot,
                use_winpos=use_winpos,
                use_new_invkin=use_invkin,
            )

            # 3. Set current RMS and delta.
            new_rms = fvc.fitrms * 1000.0

            command.info(fvc_rms=new_rms)

            if current_rms is None:
                pass
            else:
                delta_rms = current_rms - new_rms
                command.info(fvc_deltarms=delta_rms)
            current_rms = new_rms

            command.info(fvc_perc_90=fvc.perc_90 * 1000.0)
            command.info(fvc_percent_reached=fvc.fvc_percent_reached)

            # 4. Check if we have reached the distance criterion.
            if target_90_percentile and fvc.perc_90 * 1000.0 <= target_90_percentile:
                command.info("Target 90% percentile reached.")
                reached = True

            # 4. Update current positions and calculate offsets.
            command.debug("Calculating offsets.")
            await fps.update_position()
            await run_in_executor(
                fvc.calculate_offsets,
                fps.get_positions(),
                k=k,
                max_correction=max_correction,
            )

            # 5. Apply corrections.
            if reached is False and apply is True:
                if n == max_iterations and one is False:
                    command.debug("Not applying correction during the last iteration.")
                else:
                    await fvc.apply_correction()

            # 6. Save processed file.
            proc_path = filename.with_name("proc-" + filename.name)
            command.debug(f"Saving processed image {proc_path}")
            await fvc.write_proc_image(proc_path)
            proc_image_saved = True

            if reached is True:
                break

            if one is True or apply is False:
                command.warning("Cancelling FVC loop after one iteration.")
                break

            if n == max_iterations:
                command.warning("Maximum number of iterations reached.")
                break

            n += 1

    except Exception as err:
        failed = True
        return command.fail(error=f"Failed processing image: {err}")

    finally:

        if no_write_summary is False and failed is False:
            command.info("Saving confSummaryF file.")
            await fvc.write_summary_F()

        command.debug("Turning LEDs off.")
        await command.send_command("jaeger", "ieb fbi led1 0")
        await command.send_command("jaeger", "ieb fbi led2 0")

        if proc_image_saved is False:
            if filename is not None and fvc.proc_hdu is not None:
                proc_path = filename.with_name("proc-" + filename.name)
                command.debug(f"Saving processed image {proc_path}")
                await fvc.write_proc_image(proc_path)
            else:
                command.warning("Cannot write processed image.")

    if reached is True or apply is False:
        return command.finish()
    else:
        return command.fail("The FVC loop failed.")


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
