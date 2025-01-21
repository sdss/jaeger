#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2025-01-15
# @Filename: calibrations.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import click
import numpy
import pandas
import polars

from coordio.defaults import calibration

from jaeger.fvc import FVC
from jaeger.target.configuration import ManualConfiguration
from jaeger.target.tools import get_wok_data
from jaeger.utils.helpers import run_in_executor

from . import jaeger_parser


if TYPE_CHECKING:
    from clu import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor


__all__ = ["calibrations_parser"]


@jaeger_parser.group(name="calibrations")
def calibrations_parser():
    """Commands to command the FVC."""

    pass


@calibrations_parser.command()
@click.argument("POSITIONER-IDS", type=int, nargs=-1, required=False)
@click.option(
    "--take-fvc-images",
    is_flag=True,
    help="Take FVC images before and after flashing new positions and "
    "checks that the wok positions have not changed.",
)
async def reset_offsets(
    command: Command[JaegerActor],
    fps: FPS,
    positioner_ids: list[int] | None = None,
    take_fvc_images: bool = False,
):
    """Sets the positions of a robot to its current (alpha,beta) + offsets.

    Reads the positionerTable data for each positioner and resets it position
    to the current alpha and beta plus the offsets. As it does so, it zeroes the
    values for alphaOffset and betaOffset in the positionerTable.

    If called without a list of positioners, resets all positioners.

    """

    if not hasattr(calibration, "positionerTable"):
        raise RuntimeError("Calibration data not loaded.")

    if not hasattr(calibration, "positionerTableFile"):
        raise RuntimeError("Calibration file for the positionerTable cannot be found.")

    positioner_file = calibration.positionerTableFile  # type: ignore
    positioner_table_pandas = calibration.positionerTable.reset_index()
    positioner_table_orig = calibration.positionerTable.copy()

    positioner_data = polars.DataFrame(calibration.positionerTable.reset_index())
    positioner_data = positioner_data.with_row_count("id")  # To match the file columns.

    await fps.update_position()

    if positioner_ids is None or len(positioner_ids) == 0:
        positioner_ids = list(fps.positioners.keys())

    positions = fps.get_positions_dict()
    obs = command.actor.observatory
    fps.configuration = ManualConfiguration.create_from_positions(obs, positions)

    met_before: polars.DataFrame | None = None
    if take_fvc_images:
        try:
            met_before = await _take_fvc_image(
                command,
                fps,
                positioner_table=positioner_table_orig,
            )
        except Exception as ee:
            return command.fail(f"Failed to take FVC images: {ee}")

    for nn, pid in enumerate(positioner_ids):
        command.info(f"Processing positioner {pid} ({nn + 1}/{len(positioner_ids)}).")

        positioner = fps.positioners[pid]

        pid_data = positioner_data.filter(polars.col.positionerID == pid)
        if len(pid_data) == 0:
            raise RuntimeError(f"Positioner {pid} not found in positionerTable.")

        new_alpha = positioner.alpha + pid_data[0, "alphaOffset"]
        new_beta = positioner.beta + pid_data[0, "betaOffset"]

        # Flash the new position
        command.info(f"Setting positioner {pid} to ({new_alpha}, {new_beta}).")
        result = await positioner.set_position(new_alpha, new_beta)
        if not result:
            raise RuntimeError(f"Failed to set position for positioner {pid}.")

        # Check the new position.
        await asyncio.sleep(1)
        await positioner.update_position()

        if (
            not positioner.alpha
            or not positioner.beta
            or not numpy.allclose(
                [positioner.alpha, positioner.beta],
                [new_alpha, new_beta],
                atol=0.1,
            )
        ):
            raise RuntimeError(f"Failed to set position for positioner {pid}.")

        # Update the positionerTable data.
        positioner_data[pid_data["id"], "alphaOffset"] = 0
        positioner_data[pid_data["id"], "betaOffset"] = 0
        positioner_data.rename({"id": ""}).write_csv(positioner_file)
        command.info("Offsets reset in positionerTable.")

    command.info("All positioner offsets have been reset.")

    if not take_fvc_images:
        return command.finish()

    new_positioner_table = pandas.read_csv(positioner_file, comment="#", index_col=0)
    new_positioner_table.set_index(["site", "holeID"], inplace=True)
    calibration.positionerTable = new_positioner_table
    get_wok_data.cache_clear()

    await fps.initialise()
    fps.configuration = ManualConfiguration.create_from_positions(obs, positions)

    met_after: polars.DataFrame | None = None
    if take_fvc_images:
        try:
            met_after = await _take_fvc_image(
                command,
                fps,
                positioner_table=positioner_table_pandas,
            )
        except Exception as ee:
            return command.fail(f"Failed to take FVC images: {ee}")

        assert met_before is not None and met_after is not None

        # Now compare before and after.
        for pid in positioner_ids:
            before = met_before.filter(polars.col.positioner_id == pid)
            after = met_after.filter(polars.col.positioner_id == pid)

            xwok_before = before[0, "xwok_report_metrology"]
            ywok_before = before[0, "ywok_report_metrology"]
            xwok_after = after[0, "xwok_report_metrology"]
            ywok_after = after[0, "ywok_report_metrology"]

            if not numpy.allclose(
                [xwok_before, ywok_before],
                [xwok_after, ywok_after],
                atol=0.015,
            ):
                command.error(f"Positioner {pid} has different wok positions.")


async def _take_fvc_image(
    command: Command,
    fps: FPS,
    positioner_table: pandas.DataFrame | None = None,
) -> polars.DataFrame:
    """Takes an FVC image and returns the fibre data."""

    command.info("Taking FVC images before resetting offsets.")
    fvc = FVC(fps.observatory, command=command)

    command.debug("Turning LEDs on.")
    led_cmd = await command.send_command("jaeger", "ieb fbi led1 led2 9")
    if led_cmd.status.did_fail:
        raise RuntimeError("Failed to turn LEDs on.")

    filename = await fvc.expose(exposure_time=5)

    command.debug("Turning LEDs off.")
    led_cmd = await command.send_command("jaeger", "ieb fbi led1 led2 0")
    if led_cmd.status.did_fail:
        raise RuntimeError("Failed to turn LEDs off.")

    positioner_coords = fps.get_positions_dict()
    _, fibre_data, _ = await run_in_executor(
        fvc.process_fvc_image,
        filename,
        positioner_coords,
        configuration=fps.configuration,
        plot=False,
        fvc_transform_kwargs={"positionerTable": positioner_table},
    )

    return fibre_data.filter(polars.col.fibre_type == "Metrology")
