#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-27
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Optional, cast

import click

from drift import DriftError, Relay

from jaeger import config
from jaeger.exceptions import FVCError
from jaeger.fvc import FVC
from jaeger.ieb import FVC_IEB
from jaeger.target.configuration import ManualConfiguration
from jaeger.utils import run_in_executor

from . import jaeger_parser


if TYPE_CHECKING:
    from clu import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor, JaegerCommandType
    from jaeger.target.configuration import BaseConfiguration


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
    "--centroid-method",
    type=click.Choice(
        [
            "nudge",
            "simple",
            "winpos",
            "sep",
            "zbplus",
            "zbplus2",
            "zbminus",
        ]
    ),
    help="The centroid method used to extract sources.",
)
@click.option(
    "--use-invkin/--no-use-invkin",
    default=True,
    help="Use new inverse kinnematics.",
)
@click.option(
    "--polids",
    type=str,
    help="Comma-separated ZB orders to use for the FVC transformation.",
)
@click.option(
    "--no-write-summary",
    is_flag=True,
    help="Does not try to write a confSummaryF file.",
)
async def loop(command: Command[JaegerActor], fps: FPS, **kwargs):
    """Executes the FVC correction loop.

    This routine will turn the FBI LEDs on, take FVC exposures, process them,
    calculate the offset correction and applies them. Loops until the desided
    convergence is achieved.

    """

    result = await take_fvc_loop(
        command,
        fps,
        configuration=fps.configuration,
        **kwargs,
    )

    if result is not False:
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


@fvc_parser.command()
async def snapshot(command: JaegerCommandType, fps: FPS):
    """Takes an FPS snapshot with the FVC. Roughly equivalent to fvc loop --no-apply."""

    # Create a configuration from positions but don't make it active in the FPS.
    positions = fps.get_positions_dict()
    configuration = ManualConfiguration.create_from_positions(
        command.actor.observatory,
        positions,
    )

    result = await take_fvc_loop(
        command,
        fps,
        apply=False,
        configuration=configuration,
        no_write_summary=True,
    )

    if (
        result is False
        or result.fvc_transform is None
        or result.fvc_transform.positionerTableMeas is None
    ):
        return command.fail("Failed getting FVC data.")

    # Pandas dataframe.
    ptm = result.fvc_transform.positionerTableMeas
    ptm = ptm.loc[:, ["positionerID", "alphaMeas", "betaMeas"]]
    ptm.set_index("positionerID", inplace=True)

    TOL = 5

    positions = {}
    highlight = []
    for pid in ptm.index:
        alpha = fps[pid].alpha
        beta = fps[pid].beta
        alpha_meas = ptm.loc[pid].alphaMeas
        beta_meas = ptm.loc[pid].betaMeas
        positions[pid] = {"alpha": alpha_meas, "beta": beta_meas}

        if alpha is None or beta is None:
            command.warning(f"Positioner {pid} position is unknown.")
            highlight.append(pid)
        elif abs(alpha_meas - alpha) > TOL or abs(beta_meas - beta) > TOL:
            command.warning(
                f"Positioner {pid} is off by more than {TOL} degrees! "
                f"Measured position: ({alpha_meas:.2f}, {beta_meas:.2f})."
            )
            highlight.append(pid)

    await fps.save_snapshot(
        positions=positions,
        highlight=highlight,
        show_disabled=False,
    )

    return command.finish()


async def take_fvc_loop(
    command: JaegerCommandType,
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
    centroid_method: str | None = None,
    use_invkin: bool = True,
    no_write_summary: bool = False,
    configuration: BaseConfiguration | None = None,
    polids: list[int] | str | None = None,
):
    """Helper to take an FVC loop that can be called externally."""

    exposure_time = exposure_time or config["fvc"]["exposure_time"]
    fbi_level = fbi_level if fbi_level is not None else config["fvc"]["fbi_level"]
    assert isinstance(exposure_time, float) and isinstance(fbi_level, (float, int))

    if isinstance(polids, str):
        try:
            polids = list(map(int, polids.split(",")))
        except Exception:
            command.warning("Failed parsing ZB polynomials. Reverting to defaults.")
            polids = None

    configuration = configuration or fps.configuration

    if configuration is None:
        command.error("Configuration not loaded.")
        return False

    fvc = FVC(fps.observatory, command=command)

    # Check that the rotator is halted.
    if config["fvc"]["check_rotator"] is True:
        axis_cmd = await command.send_command("keys", "getFor=tcc AxisCmdState")
        if axis_cmd.status.did_fail:
            command.warning("Cannot check the status of the rotator.")
        else:
            rot_status = axis_cmd.replies.get("AxisCmdState")[2]
            if rot_status != "Halted":
                command.error(f"Cannot expose FVC while the rotator is {rot_status}.")
                return False
            else:
                command.debug("The rotator is halted.")

    command.debug("Turning LEDs on.")
    await command.send_command("jaeger", f"ieb fbi led1 led2 {fbi_level}")

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
            proc_image_saved: bool = False

            # 1. Expose the FVC
            command.debug("Taking exposure with fliswarm.")
            filename = await fvc.expose(exposure_time=exposure_time, stack=stack)
            command.debug(fvc_filename=str(filename))

            fvc.iteration = n

            # 2. Process the new image.
            positioner_coords = fps.get_positions_dict()
            await run_in_executor(
                fvc.process_fvc_image,
                filename,
                positioner_coords,
                configuration=configuration,
                plot=plot,
                polids=polids,
                centroid_method=centroid_method,
                use_new_invkin=use_invkin,
                # loop=asyncio.get_running_loop(),  # Disable for now
            )

            # 3. Set current RMS and delta.
            new_rms = round(fvc.fitrms * 1000.0, 2)

            command.info(fvc_rms=new_rms)

            if current_rms is None:
                pass
            else:
                delta_rms = current_rms - new_rms
                command.info(fvc_deltarms=delta_rms)
            current_rms = new_rms

            command.info(fvc_perc_90=round(fvc.perc_90 * 1000.0, 2))
            command.info(fvc_percent_reached=round(fvc.fvc_percent_reached, 1))

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
            fvc.proc_image_path = str(proc_path)
            command.debug(f"Asynchronously saving processed image {proc_path}")
            await fvc.update_ieb_info()
            asyncio.create_task(fvc.write_proc_image(proc_path, broadcast=True))
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
        command.error(error=f"Failed processing image: {err}")
        return False

    finally:
        try:
            if (
                not isinstance(fps.configuration, ManualConfiguration)
                and no_write_summary is False
                and failed is False
            ):
                command.info("Asynchronously saving confSummaryF file.")
                asyncio.create_task(run_in_executor(fvc.write_summary_F, plot=False))

            if proc_image_saved is False:
                if filename is not None and fvc.proc_hdu is not None:
                    proc_path = filename.with_name("proc-" + filename.name)
                    command.debug(f"Asynchronously saving processed image {proc_path}")
                    asyncio.create_task(fvc.write_proc_image(proc_path, broadcast=True))
                else:
                    command.warning("Cannot write processed image.")
        except Exception:
            pass

        command.debug("Turning LEDs off.")
        await command.send_command("jaeger", "ieb fbi led1 led2 0")

    if reached is True or apply is False:
        return fvc
    else:
        return False
