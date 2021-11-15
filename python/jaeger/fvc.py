#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-01
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import logging
import os
import pathlib
import warnings

from typing import TYPE_CHECKING, Optional

import numpy
import pandas
import sep
from astropy.io import fits
from astropy.table import Table
from matplotlib import pyplot as plt

from clu.command import Command
from coordio.defaults import calibration
from coordio.transforms import RoughTransform, ZhaoBurgeTransform

from jaeger import config, log
from jaeger.exceptions import FVCError, JaegerUserWarning
from jaeger.fps import FPS
from jaeger.ieb import IEB
from jaeger.utils import run_in_executor


if TYPE_CHECKING:
    from jaeger.actor import JaegerActor

__all__ = ["FVC"]


class FVC:
    """Focal View Camera class."""

    def __init__(self, site: str, command: Optional[Command[JaegerActor]] = None):

        if len(calibration.positionerTable) == 0:
            warnings.warn(
                "FPS calibrations not loaded or the array is empty.",
                JaegerUserWarning,
            )

        self.site = site

        self.command = command
        self.fps = FPS.get_instance()

    def set_command(self, command: Command[JaegerActor]):
        """Sets the command."""

        if command.status.is_done:
            raise FVCError("Command is done.")

        self.command = command

    def log(
        self,
        msg: str,
        level: logging._Level = logging.INFO,
        to_log: bool = True,
        to_command: bool = True,
    ):
        """Logs a message, including to the command if present."""

        level = int(level)
        msg = "[FVC]: " + msg

        if log and to_log:
            log.log(level, msg)

        if self.command and to_command:
            if level == logging.DEBUG:
                self.command.debug(msg)
            elif level == logging.INFO:
                self.command.info(msg)
            elif level == logging.WARNING:
                self.command.warning(msg)
            elif level == logging.ERROR:
                self.command.error(msg)

    async def expose(self, exposure_time: float = 5.0) -> pathlib.Path:
        """Takes an exposure with the FVC and blocks until the exposure is complete.

        Returns the path to the new image.

        """

        if self.command is None:
            raise FVCError("Command must be set.")

        if self.command.status.is_done:
            raise FVCError("Command is done.")

        self.log(f"Taking {exposure_time} s FVC exposure.", to_command=False)

        expose_command = self.command.send_command(
            "fliswarm",
            f"talk -c fvc expose {exposure_time}",
        )

        assert isinstance(expose_command, Command)
        await expose_command

        if expose_command.status.did_fail:
            raise FVCError("The FVC exposure failed.")

        for reply in expose_command.replies:
            for keyword in reply.keywords:
                if keyword.name.lower() == "filename":
                    filename = keyword.values[-1]
                    self.log(f"FVC raw image is {filename}.", to_command=False)
                    return pathlib.Path(filename)

        raise FVCError("The exposure succeeded but did not output the filename.")

    def process_fvc_image(
        self,
        path: pathlib.Path | str,
        fibre_data: Optional[pandas.DataFrame] = None,
        plot: bool | str = False,
        polids: numpy.ndarray | list | None = None,
    ) -> tuple[fits.ImageHDU, pandas.DataFrame, pandas.DataFrame]:
        """Processes a raw FVC image.

        Parameters
        ----------
        path
            The path to the raw FVC image.
        fibre_data
            A Pandas data frame with the expected coordinates of the targets. It
            is expected the data frame will have columns ``positioner_id``,
            ``fibre_type``, ``xwok``, and ``ywok``. Only the rows that correspond
            to ``fibre_type='Metrology'`` are used. This frame is appended to the
            processed image. Normally this parameters is left empty and the fibre
            table from the configuration loaded into the FPS instace is used.
        plot
            Whether to save additional debugging plots along with the processed image.
            If ``plot`` is a string, it will be used as the directory to which to
            save the plots.

        Returns
        -------
        result
            A tuple with the read raw image HDU (with columns flipped) as the first
            argument and the expected coordinates, as a data frame, as the second.
            The data frame is the same as the input target coordinates but with the
            columns ``xwok_measured`` and ``ywok_measured`` appended.

        """

        path = str(path)
        if not os.path.exists(path):
            raise FVCError(f"FVC image {path} does not exist.")

        if fibre_data is None:
            if self.fps is None or self.fps.configuration is None:
                raise FVCError("No fibre data and no configuration has been loaded.")
            fibre_data = self.fps.configuration.assignment_data.fibre_table

        fibre_data = fibre_data.copy().reset_index().set_index("fibre_type")

        self.log(f"Processing raw image {path}")

        dirname, base = os.path.split(path)
        proc_path_root = os.path.join(dirname, "proc-" + base[0 : base.find(".fit")])

        if plot is True:
            plot_path_root = proc_path_root
        elif isinstance(plot, str):
            plot_path_root = os.path.join(plot, "proc-" + base[0 : base.find(".fit")])
        else:
            plot_path_root = ""

        hdus = fits.open(path)

        # Invert columns
        hdus[1].data = hdus[1].data[:, ::-1]
        image_data = hdus[1].data

        centroids = self.extract(image_data)

        fiducialCoords = calibration.fiducialCoords.loc[self.site]

        xCMM = fiducialCoords.xWok.to_numpy()
        yCMM = fiducialCoords.yWok.to_numpy()
        xyCMM = numpy.array([xCMM, yCMM]).T

        xyCCD = centroids[["x", "y"]].to_numpy()

        fibre_data_met = fibre_data.loc["Metrology"]

        # Get close enough to associate the correct centroid with the correct fiducial.
        x_wok_expect = numpy.concatenate([xCMM, fibre_data_met.xwok.to_numpy()])
        y_wok_expect = numpy.concatenate([yCMM, fibre_data_met.ywok.to_numpy()])
        xy_wok_expect = numpy.array([x_wok_expect, y_wok_expect]).T

        rt = RoughTransform(xyCCD, xy_wok_expect)
        xy_wok_rough = rt.apply(xyCCD)

        # First associate fiducials and build first round just use outer fiducials
        rCMM = numpy.sqrt(xyCMM[:, 0] ** 2 + xyCMM[:, 1] ** 2)
        keep = rCMM > 310
        xyCMMouter = xyCMM[keep, :]

        arg_found, fid_rough_dist = arg_nearest_neighbor(xyCMMouter, xy_wok_rough)
        self.log(
            f"Max fiducial rough distance: {numpy.max(fid_rough_dist):.3f}",
            level=logging.DEBUG,
        )

        xy_fiducial_CCD = xyCCD[arg_found]
        xy_fiducial_wok_rough = xy_wok_rough[arg_found]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_rough,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_roughassoc.png",
                xy_fiducial=xy_fiducial_wok_rough,
                xy_fiducial_cmm=xyCMMouter,
                title="Rough fiducial association",
            )

        ft = ZhaoBurgeTransform(
            xy_fiducial_CCD,
            xyCMMouter,
            polids=(polids or config["fvc"]["zb_polids"]),
        )
        self.log(
            f"Full transform 2. Bisased RMS={ft.rms * 1000:.3f}, "
            f"Unbiased RMS={ft.unbiasedRMS * 1000:.3f}.",
            level=logging.DEBUG,
        )
        xy_wok_meas = ft.apply(xyCCD, zb=False)

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_full1.png",
                title="Full transform 1",
            )

        # Re-associate fiducials, some could have been wrongly associated in
        # first fit but second fit should be better?
        arg_found, fid_rough_dist = arg_nearest_neighbor(xyCMM, xy_wok_meas)
        self.log(
            f"Max fiducial fit 2 distance: {numpy.max(fid_rough_dist):.3f}",
            level=logging.DEBUG,
        )

        xy_fiducial_CCD = xyCCD[arg_found]  # Overwriting
        xy_fiducial_wok_refine = xy_wok_meas[arg_found]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_refineassoc.png",
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
        self.log(
            f"Full transform 1. Bisased RMS={ft.rms * 1000:.3f}, "
            f"Unbiased RMS={ft.unbiasedRMS * 1000:.3f}.",
            level=logging.DEBUG,
        )

        xy_wok_meas = ft.apply(xyCCD)  # Overwrite

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_full2.png",
                title="Full transform 2",
            )

        # Transform all CCD detections to wok space
        xy_expect_pos = fibre_data_met[["xwok", "ywok"]].to_numpy()

        arg_found, met_dist = arg_nearest_neighbor(xy_expect_pos, xy_wok_meas)
        self.log(
            f"Max metrology distance: {numpy.max(met_dist):.3f}",
            level=logging.DEBUG,
        )
        xy_wok_robot_meas = xy_wok_meas[arg_found]

        fibre_data = fibre_data.reset_index()

        met_index = fibre_data.fibre_type == "Metology"

        fibre_data.loc[met_index, "xwok_measured"] = xy_wok_robot_meas[:, 0]
        fibre_data.loc[met_index, "ywok_measured"] = xy_wok_robot_meas[:, 1]

        # Only use online robots for final RMS.
        online = fibre_data.loc[~fibre_data.offline]
        dx = online.xWokMetExpect - online.xWokMetMeas
        dy = online.yWokMetExpect - online.yWokMetMeas

        rms = numpy.sqrt(numpy.mean(dx ** 2 + dy ** 2))
        self.log(f"RMS full fit {rms * 1000:.3f} um.")

        hdus[1].header["FITRMS"] = (rms * 1000, "RMS full fit [um]")

        return (hdus[1], fibre_data, centroids)

    async def write_proc_image(
        self,
        new_filename: str | pathlib.Path,
        raw_hdu: fits.ImageHDU,
        measured_coords: pandas.DataFrame,
        centroids: pandas.DataFrame,
    ) -> fits.HDUList:
        """Writes the processed image along with additional table data."""

        proc_hdus = fits.HDUList([fits.PrimaryHDU(), raw_hdu])

        positionerTable = calibration.positionerTable
        wokCoords = calibration.wokCoords
        fiducialCoords = calibration.fiducialCoords

        dfs = [
            ("POSITIONERTABLE", positionerTable.reset_index()),
            ("WOKCOORDS", wokCoords.reset_index()),
            ("FIDUCIALCOORDS", fiducialCoords.reset_index()),
        ]

        for name, df in dfs:
            rec = Table.from_pandas(df).as_array()
            table = fits.BinTableHDU(rec, name=name)
            proc_hdus.append(table)

        measured_coords.reset_index(inplace=True)
        measured_coords.sort_values("positioner_id", inplace=True)
        measured_coords_rec = Table.from_pandas(measured_coords).as_array()
        proc_hdus.append(fits.BinTableHDU(measured_coords_rec, name="FIBERDATA"))

        # Add IEB information
        ieb_keys = config["fvc"]["ieb_keys"]
        ieb_data = {key: -999.0 for key in ieb_keys}

        for key in ieb_keys:
            device_name = ieb_keys[key]
            if self.fps and self.fps.ieb and isinstance(self.fps.ieb, IEB):
                try:
                    device = self.fps.ieb.get_device(device_name)
                    ieb_data[key] = (await device.read())[0] or -999.0
                except Exception as err:
                    self.log(f"Failed getting IEB information: {err}", logging.WARNING)
                    break

        for key, val in ieb_data.items():
            proc_hdus[1].header[key] = val

        if self.fps:
            await self.fps.update_position()
            positions = self.fps.get_positions()
            current_positions = pandas.DataFrame(
                {
                    "positionerID": positions[:, 0].astype(int),
                    "alphaReport": positions[:, 1],
                    "betaReport": positions[:, 2],
                }
            )

            if self.fps.configuration:
                robot_grid = self.fps.configuration.robot_grid

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

            rec = Table.from_pandas(current_positions).as_array()
            proc_hdus.append(fits.BinTableHDU(rec, name="POSANGLES"))

        rec = Table.from_pandas(centroids).as_array()
        proc_hdus.append(fits.BinTableHDU(rec, name="CENTROIDS"))

        await run_in_executor(proc_hdus.writeto, new_filename, checksum=True)

        return proc_hdus

    def extract(self, image_data: numpy.ndarray) -> pandas.DataFrame:
        """Extract image data using SExtractor. Returns the extracted centroids."""

        image_data = numpy.array(image_data, dtype=numpy.float32)

        bkg = sep.Background(image_data)
        bkg_image = bkg.back()

        data_sub = image_data - bkg_image

        objects = sep.extract(
            data_sub,
            config["fvc"]["background_sigma"],
            err=bkg.globalrms,
        )
        objects = pandas.DataFrame(objects)

        # Eccentricity
        objects["ecentricity"] = 1 - objects["b"] / objects["a"]

        # Slope of ellipse (optical distortion direction)
        objects["slope"] = numpy.tan(objects["theta"] + numpy.pi / 2)  # rotate by 90

        # Intercept of optical distortion direction
        objects["intercept"] = objects["y"] - objects["slope"] * objects["x"]

        # Ignore everything less than X pixels
        objects = objects.loc[objects["npix"] > config["fvc"]["centroid_min_npix"]]

        self.log(f"Found {len(objects)} centroids", level=logging.DEBUG)

        ncentroids = len(calibration.positionerTable) + len(calibration.fiducialCoords)
        self.log(f"Expected {ncentroids} centroids", level=logging.DEBUG)

        return objects

    def plot_fvc_assignments(
        self,
        xy: numpy.ndarray,
        target_coords: pandas.DataFrame | pandas.Series,
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
            target_coords.xwok.to_numpy(),
            target_coords.xwok.to_numpy(),
            "xk",
            ms=3,
            label="Expected MET",
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
            label="Expected FIF",
        )

        if xy_fiducial is not None and xy_fiducial_cmm is not None:
            for cmm, measured in zip(xy_fiducial_cmm, xy_fiducial):
                plt.plot([cmm[0], measured[0]], [cmm[1], measured[1]], "-k")

        plt.axis("equal")
        plt.legend()
        plt.xlim([-350, 350])
        plt.ylim([-350, 350])
        plt.savefig(filename, dpi=350)
        plt.close()


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
