#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-01
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os
import pathlib
from unittest.mock import Mock

from typing import TYPE_CHECKING, Union

import numpy
import pandas
import sep
from astropy.io import fits
from astropy.table import Table
from matplotlib import pyplot as plt

from clu.command import Command
from coordio.defaults import fiducialCoords, positionerTable, wokCoords
from coordio.transforms import RoughTransform, ZhaoBurgeTransform

from jaeger import config
from jaeger.exceptions import FVCError
from jaeger.fps import FPS
from jaeger.ieb import IEB


if TYPE_CHECKING:

    from kaiju import RobotGridCalib

    from jaeger.actor import JaegerActor


__all__ = ["take_image"]


CommandOrFakeT = Union[Command[JaegerActor], Mock]


async def take_image(
    command: Command[JaegerActor],
    exposure_time: float = 5.0,
) -> pathlib.Path:
    """Takes an exposure with the FVC and blocks until the exposure is complete.

    Returns the path to the new image.

    """

    expose_command = command.send_command(
        "fliswarm",
        f"talk -c fvc expose {exposure_time}",  # There should be only one FVC per site.
    )

    assert isinstance(expose_command, Command)
    await expose_command

    if expose_command.status.did_fail:
        raise FVCError("The FVC exposure failed.")

    for reply in expose_command.replies:
        for keyword in reply.keywords:
            if keyword.name.lower() == "filename":
                return pathlib.Path(keyword.values[-1].native)

    raise FVCError("The exposure succeeded but did not output the filename.")


def process_fvc_image(
    path: pathlib.Path | str,
    target_coords: pandas.DataFrame,
    plot: bool = False,
    command: CommandOrFakeT = Mock(),
    polids: numpy.ndarray | list | None = None,
) -> tuple[fits.ImageHDU, pandas.DataFrame]:
    """Processes a raw FVC image.

    Parameters
    ----------
    path
        The path to the raw FVC image.
    target_coords
        A Pandas data frame with the expected coordinates of the targets. Only the
        columns ``xWokMetExpect`` and ``yWokMetExpect`` are used.
    plot
        Whether to save additional debugging plots along with the processed image.

    Returns
    -------
    result
        A tuple with the read raw image HDU (with columns flipped) as the first
        argument and the expected coordinates, as a data frame, as the second.
        The data frame is the same as the input target coordinates but with the
        columns ``xWokMetMeas`` and ``yWokMetMeas`` appended.

    """

    path = str(path)
    if not os.path.exists(path):
        raise FVCError("FVC image does not exist.")

    command.info(f"Processing image {path}")

    proc_path_base = path[0 : path.find(".fit")]

    hdus = fits.open(path)

    # Invert columns
    hdus[1].data = hdus[1].data[:, ::-1]
    image_data = hdus[1].data

    centroids = extract(image_data)

    xCMM = fiducialCoords.xWok.to_numpy()
    yCMM = fiducialCoords.yWok.to_numpy()
    xyCMM = numpy.array([xCMM, yCMM]).T

    xyCCD = centroids[["x", "y"]].to_numpy()

    # Get close enough to associate the correct centroid with the correct fiducial...
    x_wok_expect = numpy.concatenate([xCMM, target_coords.xWokMetExpect.to_numpy()])
    y_wok_expect = numpy.concatenate([yCMM, target_coords.yWokMetExpect.to_numpy()])
    xy_wok_expect = numpy.array([x_wok_expect, y_wok_expect]).T

    rt = RoughTransform(xyCCD, xy_wok_expect)
    xy_wok_rough = rt.apply(xyCCD)

    # First associate fiducials and build first round just use outer fiducials
    rCMM = numpy.sqrt(xyCMM[:, 0] ** 2 + xyCMM[:, 1] ** 2)
    keep = rCMM > 310
    xyCMMouter = xyCMM[keep, :]

    arg_found, fid_rough_dist = arg_nearest_neighbor(xyCMMouter, xy_wok_rough)
    command.debug(f"Max fiducial rough distance: {numpy.max(fid_rough_dist)}")

    xy_fiducial_CCD = xyCCD[arg_found]
    xy_fiducial_wok_rough = xy_wok_rough[arg_found]

    if plot:
        plot_fvc_assignments(
            xy_wok_rough,
            target_coords,
            xCMM,
            yCMM,
            proc_path_base + "_roughassoc.png",
            xy_fiducial=xy_fiducial_wok_rough,
            xy_fiducial_cmm=xyCMMouter,
            title="Rough fiducial association",
        )

    ft = ZhaoBurgeTransform(
        xy_fiducial_CCD,
        xyCMMouter,
        polids=(polids or config["fvc"]["zb_polids"]),
    )
    command.debug(
        f"Full transform 1. Bisased RMS={ft.rms * 1000}, "
        f"Unbiased RMS={ft.unbiasedRMS * 1000}."
    )
    xy_wok_meas = ft.apply(xyCCD, zb=False)

    if plot:
        plot_fvc_assignments(
            xy_wok_meas,
            target_coords,
            xCMM,
            yCMM,
            proc_path_base + "_full1.png",
            title="Full transform 1",
        )

    # Re-associate fiducials, some could have been wrongly associated in
    # first fit but second fit should be better?
    arg_found, fid_rough_dist = arg_nearest_neighbor(xyCMM, xy_wok_meas)
    command.debug(f"Max fiducial fit 2 distance: {numpy.max(fid_rough_dist)}")

    xy_fiducial_CCD = xyCCD[arg_found]  # Overwriting
    xy_fiducial_wok_refine = xy_wok_meas[arg_found]

    if plot:
        plot_fvc_assignments(
            xy_wok_meas,
            target_coords,
            xCMM,
            yCMM,
            proc_path_base + "_refineassoc.png",
            title="Refined fiducial association",
            xy_fiducial=xy_fiducial_wok_refine,
            xy_fiducial_cmm=xyCMM,
        )

    # Try a new transform
    ft = ZhaoBurgeTransform(
        xy_fiducial_CCD,
        xyCMM,
        polids=(polids or config["fvc"]["zb_polids"]),
    )
    command.debug(
        f"Full transform 1. Bisased RMS={ft.rms * 1000}, "
        f"Unbiased RMS={ft.unbiasedRMS * 1000}."
    )

    xy_wok_meas = ft.apply(xyCCD)  # Overwrite

    if plot:
        plot_fvc_assignments(
            xy_wok_meas,
            target_coords,
            xCMM,
            yCMM,
            proc_path_base + "_full2.png",
            title="Full transform 2",
        )

    # Transform all CCD detections to wok space
    xy_expect_pos = target_coords[["xWokMetExpect", "yWokMetExpect"]].to_numpy()

    arg_found, met_dist = arg_nearest_neighbor(xy_expect_pos, xy_wok_meas)
    command.debug(f"Max metrology distance: {numpy.max(met_dist)}")
    xy_wok_robot_meas = xy_wok_meas[arg_found]

    target_coords["xWokMetMeas"] = xy_wok_robot_meas[:, 0]
    target_coords["yWokMetMeas"] = xy_wok_robot_meas[:, 1]

    dx = target_coords.xWokMetExpect - target_coords.xWokMetMeas
    dy = target_coords.yWokMetExpect - target_coords.yWokMetMeas

    rms = numpy.sqrt(numpy.mean(dx ** 2 + dy ** 2))
    command.debug(f"RMS full fit {rms * 1000} um.")

    return (hdus[1], target_coords)


async def write_proc_image(
    new_filename: str | pathlib.Path,
    raw_hdu: fits.ImageHDU,
    fps: FPS,
    target_coords: pandas.DataFrame,
    robot_grid: RobotGridCalib | None = None,
):

    proc_hdus = fits.HDUList([fits.PrimaryHDU(), raw_hdu])

    dfs = [
        ("positionerTable", positionerTable),
        ("wokCoords", wokCoords),
        ("fiducialCoords", fiducialCoords),
    ]

    for name, df in dfs:
        rec = Table.from_pandas(df).as_array()
        table = fits.BinTableHDU(rec, name=name)
        proc_hdus.append(table)

    # Add IEB information
    ieb_data = {
        "TEMPRTD2": -999.0,
        "TEMPT3": -999.0,
        "TEMPRTD3": -999.0,
        "LED1": -999.0,
        "LED2": -999.0,
    }
    if fps.ieb and isinstance(fps.ieb, IEB):
        ieb_data["TEMPRTD2"] = (await fps.ieb.read_device("rtd2"))[0] or -999.0
        ieb_data["TEMPT3"] = (await fps.ieb.read_device("t3"))[0] or -999.0
        ieb_data["TEMPRTD3"] = (await fps.ieb.read_device("rtd3"))[0] or -999.0
        ieb_data["LED1"] = (await fps.ieb.read_device("led1"))[0]
        ieb_data["LED2"] = (await fps.ieb.read_device("led2"))[0]

    for key, val in ieb_data.items():
        proc_hdus[1].header[key] = val

    # proc_hdus[1].header["KAISEED"] = seed

    await fps.update_position()
    positions = fps.get_positions()
    current_positions = pandas.DataFrame(
        {
            "positionerID": positions[:, 0].astype(int),
            "alphaReport": positions[:, 1],
            "betaReport": positions[:, 2],
        }
    )

    if robot_grid:
        _cmd_alpha = []
        _cmd_beta = []
        _start_alpha = []
        _start_beta = []

        if len(list(robot_grid.robotDict.values())[0].alphaPath) > 0:
            for pid in current_positions.positionerID:
                robot = robot_grid.robotDict[pid]
                _cmd_alpha.append(robot.alphaPath[0][1])
                _cmd_beta.append(robot.betaPath[0][1])
                _start_alpha.append(robot.alphaPath[-1][1])
                _start_beta.append(robot.betaPath[-1][1])

            current_positions["cmdAlpha"] = _cmd_alpha
            current_positions["cmdBeta"] = _cmd_beta
            current_positions["startAlpha"] = _start_alpha
            current_positions["startBeta"] = _start_beta

    rec = Table.from_pandas(current_positions)
    table = fits.BinTableHDU(rec, name="posAngles")

    proc_hdus.append(table)

    proc_hdus.writeto(new_filename, checksum=True)


def extract(
    image_data: numpy.ndarray,
    command: CommandOrFakeT = Mock(),
) -> pandas.DataFrame:
    """Extract image data using SExtractor. Returns the extracted centroids."""

    image_data = numpy.array(image_data, dtype=numpy.float32)

    bkg = sep.Background(image_data)
    bkg_image = bkg.back()

    data_sub = image_data - bkg_image

    objects = sep.extract(data_sub, 3.5, err=bkg.globalrms)
    objects = pandas.DataFrame(objects)

    # Eccentricity
    objects["ecentricity"] = 1 - objects["b"] / objects["a"]

    # Slope of ellipse (optical distortion direction)
    objects["slope"] = numpy.tan(objects["theta"] + numpy.pi / 2)  # rotate by 90

    # Intercept of optical distortion direction
    objects["intercept"] = objects["y"] - objects["slope"] * objects["x"]

    # Ignore everything less than 100 pixels
    objects = objects.loc[objects["npix"] > 100]

    command.debug(f"Found {len(objects)} centroids")
    command.debug(f"Expected {len(positionerTable) + len(fiducialCoords)} centroids")

    return objects


def arg_nearest_neighbor(xyA: numpy.ndarray, xyB: numpy.ndarray):
    """Loop over xy list A, find nearest neighbour in list B

    Returns
    -------
    result
        The indices in list b that match A.

    """

    # TODO: this is probably efficient given the number of points, but maybe
    # replace with a scipy cdist.

    xyA = numpy.array(xyA)
    xyB = numpy.array(xyB)
    out = []
    distance = []
    for x, y in xyA:
        dist = numpy.sqrt((x - xyB[:, 0]) ** 2 + (y - xyB[:, 1]) ** 2)
        amin = numpy.argmin(dist)
        distance.append(dist[amin])
        out.append(amin)

    return numpy.array(out), numpy.array(distance)


def plot_fvc_assignments(
    xy: numpy.ndarray,
    target_coords: pandas.DataFrame,
    xCMM: numpy.ndarray,
    yCMM: numpy.ndarray,
    filename: str,
    xy_fiducial: numpy.ndarray | None = None,
    xy_fiducial_cmm: numpy.ndarray | None = None,
    title: str | None = None,
):
    """Plot the results of the transformation."""

    plt.figure(figsize=(8, 8))

    if title:
        plt.title(title)

    plt.plot(
        xy[:, 0],
        xy[:, 1],
        "o",
        ms=4,
        markerfacecolor="None",
        markeredgecolor="red",
        markeredgewidth=1,
        label="Centroid",
    )

    plt.plot(
        target_coords.xWokMetExpect.to_numpy(),
        target_coords.yWokMetExpect.to_numpy(),
        "xk",
        ms=3,
        label="expected met",
    )

    # Overplot fiducials
    plt.plot(
        xCMM,
        yCMM,
        "D",
        ms=6,
        markerfacecolor="None",
        markeredgecolor="cornflowerblue",
        markeredgewidth=1,
        label="expected fid",
    )

    if xy_fiducial and xy_fiducial_cmm:
        for cmm, measured in zip(xy_fiducial_cmm, xy_fiducial):
            plt.plot([cmm[0], measured[0]], [cmm[1], measured[1]], "-k")

    plt.axis("equal")
    plt.legend()
    plt.xlim([-350, 350])
    plt.ylim([-350, 350])
    plt.savefig(filename, dpi=350)
    plt.close()
