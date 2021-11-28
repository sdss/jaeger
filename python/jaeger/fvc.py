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
from clu.legacy.tron import TronConnection
from coordio.defaults import calibration
from coordio.transforms import RoughTransform, ZhaoBurgeTransform

from jaeger import config, log
from jaeger.exceptions import FVCError, JaegerUserWarning, TrajectoryError
from jaeger.fps import FPS
from jaeger.ieb import IEB
from jaeger.kaiju import get_path_pair_in_executor, get_robot_grid
from jaeger.target.tools import wok_to_positioner
from jaeger.utils import run_in_executor


if TYPE_CHECKING:
    from jaeger.actor import JaegerActor

__all__ = ["FVC"]


class FVC:
    """Focal View Camera class."""

    fibre_data: Optional[pandas.DataFrame]
    centroids: Optional[pandas.DataFrame]
    offsets: Optional[pandas.DataFrame]

    image_path: Optional[str]
    raw_hdu: Optional[fits.ImageHDU]
    proc_hdu: Optional[fits.ImageHDU]

    fitrms: float
    k: float

    def __init__(self, site: str, command: Optional[Command[JaegerActor]] = None):

        if len(calibration.positionerTable) == 0:
            warnings.warn(
                "FPS calibrations not loaded or the array is empty.",
                JaegerUserWarning,
            )

        self.site = site

        self.command = command
        self.fps = FPS.get_instance()

        self.reset()

    def reset(self):
        """Resets the instance."""

        self.fibre_data = None
        self.centroids = None
        self.offsets = None

        self.image_path = None
        self.raw_hdu = None
        self.proc_hdu = None

        self.k = 1
        self.fitrms = -999.0

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

    async def expose(
        self,
        exposure_time: float = 5.0,
        stack: int = 1,
        use_tron_fallback=True,
    ) -> pathlib.Path:  # pragma: no cover
        """Takes an exposure with the FVC and blocks until the exposure is complete.

        Returns the path to the new image. If ``use_tron_fallback=True`` and the
        command has not been set, creates a Tron client to command the FVC.

        """

        if self.command is None:
            if use_tron_fallback is False:
                raise FVCError("Command must be set.")

        else:
            if self.command.status.is_done:
                raise FVCError("Command is done.")

        self.log(f"Taking {exposure_time} seconds FVC exposure.", to_command=False)

        tron = None

        if stack <= 1:
            cmd_str = f"talk -c fvc expose {exposure_time}"
        else:
            cmd_str = f"talk -c fvc expose --stack {stack} {exposure_time}"

        if self.command:
            expose_command = self.command.send_command("fliswarm", cmd_str)
        else:
            tron = TronConnection(
                config["actor"]["tron_host"],
                config["actor"]["tron_port"],
            )
            await tron.start()
            expose_command = tron.send_command("fliswarm", cmd_str)

        assert isinstance(expose_command, Command)
        await expose_command

        if tron:
            tron.stop()

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
        fibre_type: str = "Metrology",
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
            ``hole_id``, ``fibre_type``, ``xwok``, and ``ywok``. This frame is
            appended to the processed image. Normally this parameters is left
            empty and the fibre table from the configuration loaded into the FPS
            instace is used.
        fibre_type
            The ``fibre_type`` rows in ``fibre_data`` to use. Defaults to
            ``fibre_type='Metrology'``.
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

        # Reset the instance
        self.reset()

        path = str(path)
        if not os.path.exists(path):
            raise FVCError(f"FVC image {path} does not exist.")

        if fibre_data is None:
            if self.fps is None or self.fps.configuration is None:
                raise FVCError("No fibre data and no configuration has been loaded.")
            fibre_data = self.fps.configuration.assignment_data.fibre_table

        self.fibre_data = fibre_data.copy().reset_index().set_index("fibre_type")

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

        self.image_path = path
        self.raw_hdu = hdus[1].copy()

        # Invert columns
        hdus[1].data = hdus[1].data[:, ::-1]
        image_data = hdus[1].data

        self.log(f"Max counts in image: {numpy.max(image_data)}", level=logging.INFO)

        self.centroids = self.extract(image_data)

        fiducialCoords = calibration.fiducialCoords.loc[self.site]

        xCMM = fiducialCoords.xWok.to_numpy()
        yCMM = fiducialCoords.yWok.to_numpy()
        xyCMM = numpy.array([xCMM, yCMM]).T

        xyCCD = self.centroids[["x", "y"]].to_numpy()

        fibre_data_met = self.fibre_data.loc[fibre_type]

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
            level=logging.INFO,
        )

        xy_fiducial_CCD = xyCCD[arg_found]
        xy_fiducial_wok_rough = xy_wok_rough[arg_found]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_rough,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_roughassoc.pdf",
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
            f"Full transform 1. Bisased RMS={ft.rms * 1000:.3f}, "
            f"Unbiased RMS={ft.unbiasedRMS * 1000:.3f}.",
            level=logging.INFO,
        )
        xy_wok_meas = ft.apply(xyCCD, zb=False)

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_full1.pdf",
                title="Full transform 1",
            )

        # Re-associate fiducials, some could have been wrongly associated in
        # first fit but second fit should be better?
        arg_found, fid_rough_dist = arg_nearest_neighbor(xyCMM, xy_wok_meas)
        self.log(
            f"Max fiducial fit 2 distance: {numpy.max(fid_rough_dist):.3f}",
            level=logging.INFO,
        )

        xy_fiducial_CCD = xyCCD[arg_found]  # Overwriting
        xy_fiducial_wok_refine = xy_wok_meas[arg_found]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_refineassoc.pdf",
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
            f"Full transform 2. Bisased RMS={ft.rms * 1000:.3f}, "
            f"Unbiased RMS={ft.unbiasedRMS * 1000:.3f}.",
            level=logging.INFO,
        )

        xy_wok_meas = ft.apply(xyCCD)  # Overwrite

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_full2.pdf",
                title="Full transform 2",
            )

        # Transform all CCD detections to wok space
        xy_expect_pos = fibre_data_met[["xwok", "ywok"]].to_numpy()

        arg_found, met_dist = arg_nearest_neighbor(xy_expect_pos, xy_wok_meas)
        self.log(
            f"Max metrology distance: {numpy.max(met_dist):.3f}",
            level=logging.INFO,
        )
        xy_wok_robot_meas = xy_wok_meas[arg_found]

        self.fibre_data.loc[fibre_type, "xwok_measured"] = xy_wok_robot_meas[:, 0]
        self.fibre_data.loc[fibre_type, "ywok_measured"] = xy_wok_robot_meas[:, 1]

        # Only use online robots for final RMS.
        online = self.fibre_data.loc[
            (self.fibre_data.index == fibre_type) & (self.fibre_data.offline == 0)
        ]
        dx = online.xwok - online.xwok_measured
        dy = online.ywok - online.ywok_measured

        self.fitrms = numpy.sqrt(numpy.mean(dx ** 2 + dy ** 2))
        self.log(f"RMS full fit {self.fitrms * 1000:.3f} um.")

        hdus[1].header["FITRMS"] = (self.fitrms * 1000, "RMS full fit [um]")

        self.fibre_data.reset_index(inplace=True)
        self.fibre_data.set_index(["hole_id", "fibre_type"], inplace=True)
        self.proc_hdu = hdus[1]

        self.log(f"Finished processing {path}", level=logging.DEBUG)

        return (self.proc_hdu, self.fibre_data, self.centroids)

    def calculate_offsets(
        self,
        reported_positions: numpy.ndarray,
        fibre_data: Optional[pandas.DataFrame] = None,
        k: Optional[float] = None,
    ) -> pandas.DataFrame:
        """Determines the offset to apply to the currently reported positions.

        Measured wok positions from the fibre data are converted to positioner
        coordinates. An alpha/beta offset is calculated with respect to the
        expected positions. The offsets is then applied to the current positions
        as self-reported by the positioners. Optionally, the offset can be
        adjusted using a PID loop.

        Parameters
        ----------
        reported_positions
            Reported positions for the positioners as a numpy array. Usually
            the output of `.FPS.get_positions`.
        fibre_data
            The fibre data table. Only the metrology entries are used. Must
            have the ``xwok_measured`` and ``ywok_measured`` column populated.
            If `None`, uses the data frame calculated when `.process_fvc_image`
            last run.
        k
            The fraction of the correction to apply.

        Returns
        -------
        new_positions
            The new alpha and beta positions as a Pandas dataframe indexed by
            positions ID. If `None`, uses the value ``fvc.k`` from the configuration.

        """

        self.log("Calculating offset from FVC image and fit.")

        site = config["observatory"]
        self.k = k or config["fvc"]["k"]

        if fibre_data is None and self.fibre_data is None:
            raise FVCError("No fibre data passed or stored in the instance.")

        if fibre_data is None:
            fibre_data = self.fibre_data
            assert fibre_data is not None

        fibre_data = fibre_data.copy().reset_index()
        met: pandas.DataFrame = fibre_data.loc[fibre_data.fibre_type == "Metrology"]

        # TODO: deal with missing data
        if (met.loc[:, ["xwok_measured", "ywok_measured"]] == -999.0).any().any():
            raise FVCError("Some metrology fibres have not been measured.")

        # Calculate alpha/beta from measured wok coordinates.
        _measured = []
        first = True
        for _, row in met.iterrows():
            (alpha_measured, beta_measured), _ = wok_to_positioner(
                row.hole_id,
                site,
                "Metrology",
                row.xwok_measured,
                row.ywok_measured,
            )

            if "alpha" in row:
                alpha_expected = row.alpha
                beta_expected = row.beta
            else:
                if first:
                    self.log(
                        "Fibre data does not include the expected alpha/beta "
                        "positions. Using reported alpha/beta.",
                        logging.WARNING,
                    )
                prow = reported_positions[reported_positions[:, 0] == row.positioner_id]
                alpha_expected = prow[0][1]
                beta_expected = prow[0][2]

            # If beta >= 180, we would need a left handed configuration. For now we
            # invalidate these values.
            if beta_expected >= 180.0:
                alpha_measured = numpy.nan
                beta_measured = numpy.nan

            xwok_distance = row.xwok_measured - row.xwok
            ywok_distance = row.ywok_measured - row.ywok

            _measured.append(
                (
                    row.hole_id,
                    row.positioner_id,
                    xwok_distance,
                    ywok_distance,
                    alpha_expected,
                    beta_expected,
                    alpha_measured,
                    beta_measured,
                )
            )

            first = False

        measured = pandas.DataFrame(
            _measured,
            columns=[
                "hole_id",
                "positioner_id",
                "xwok_distance",
                "ywok_distance",
                "alpha_expected",
                "beta_expected",
                "alpha_measured",
                "beta_measured",
            ],
        )
        measured.set_index("positioner_id", inplace=True)

        # Merge the reported positions.
        reported = pandas.DataFrame(
            reported_positions,
            columns=["positioner_id", "alpha_reported", "beta_reported"],
        )
        reported.positioner_id = reported.positioner_id.astype("int32")
        reported.set_index("positioner_id", inplace=True)

        offsets = pandas.concat([measured, reported], axis=1)

        # If there are measured alpha/beta that are NaN, replace those with the
        # previous value.
        offsets["transformation_valid"] = 1
        pos_na = offsets["alpha_measured"].isna()
        if pos_na.sum() > 0:
            self.log(
                "Failed to calculated corrected positioner coordinates for "
                f"{pos_na.sum()} positioners.",
                level=logging.WARNING,
            )

            # For now set these values to the expected because we'll use them to
            # calculate the offset (which will be zero for invalid conversions).
            expected = offsets.loc[pos_na, ["alpha_expected", "beta_expected"]]
            offsets.loc[pos_na, ["alpha_measured", "beta_measured"]] = expected
            offsets.loc[pos_na, "transformation_valid"] = 0

        # Calculate offset between expected and measured.
        alpha_offset = offsets["alpha_expected"] - offsets["alpha_measured"]
        beta_offset = offsets["beta_expected"] - offsets["beta_measured"]

        offsets["alpha_offset"] = alpha_offset
        offsets["beta_offset"] = beta_offset

        # Clip very large offsets and apply a proportional term.
        max_offset = config["fvc"]["max_offset"]
        alpha_offset_c = numpy.clip(self.k * alpha_offset, -max_offset, max_offset)
        beta_offset_c = numpy.clip(self.k * beta_offset, -max_offset, max_offset)

        offsets["alpha_offset_corrected"] = alpha_offset_c
        offsets["beta_offset_corrected"] = beta_offset_c

        # Calculate new alpha and beta by applying the offset to the reported
        # positions (i.e., the ones the positioners believe they are at).
        alpha_new = offsets["alpha_reported"] + offsets["alpha_offset_corrected"]
        beta_new = offsets["beta_reported"] + offsets["beta_offset_corrected"]

        # Get new positions that are out of range and clip them.
        alpha_oor = (alpha_new < 0.0) | (alpha_new > 360.0)
        beta_oor = (beta_new < 0.0) | (beta_new > 180.0)

        alpha_new.loc[alpha_oor] = numpy.clip(alpha_new.loc[alpha_oor], 0, 360)
        beta_new.loc[beta_oor] = numpy.clip(beta_new.loc[beta_oor], 0, 180)

        # For the values out of range, recompute the corrected offsets.
        alpha_corr = alpha_new.loc[alpha_oor] - offsets.loc[alpha_oor, "alpha_reported"]
        offsets.loc[alpha_oor, "alpha_offset_corrected"] = alpha_corr
        beta_corr = beta_new.loc[beta_oor] - offsets.loc[beta_oor, "beta_reported"]
        offsets.loc[beta_oor, "beta_offset_corrected"] = beta_corr

        # Final check. If alpha/beta_new are NaNs, replace with reported values.
        alpha_new[numpy.isnan(alpha_new)] = offsets.loc[
            numpy.isnan(alpha_new), "alpha_reported"
        ]
        beta_new[numpy.isnan(beta_new)] = offsets.loc[
            numpy.isnan(beta_new), "beta_reported"
        ]

        # Save new positions.
        offsets["alpha_new"] = alpha_new
        offsets["beta_new"] = beta_new

        # Set the invalid alpha/beta_measured back to NaN.
        invalid = offsets["transformation_valid"] == 0
        offsets.loc[
            invalid,
            [
                "alpha_measured",
                "beta_measured",
                "alpha_offset",
                "beta_offset",
                "alpha_offset_corrected",
                "beta_offset_corrected",
            ],
        ] = numpy.nan

        self.offsets = offsets

        self.log("Finished calculating offsets.", level=logging.DEBUG)

        return offsets

    async def write_proc_image(
        self,
        new_filename: Optional[str | pathlib.Path] = None,
    ) -> fits.HDUList:  # pragma: no cover
        """Writes the processed image along with additional table data.

        If ``new_filename`` is not passed, defaults to adding the prefix ``proc-``
        to the last processed image file path.

        """

        if self.image_path is None or self.proc_hdu is None:
            raise FVCError(
                "No current image. Take and process "
                "an image before callin write_proc_image()."
            )

        if new_filename is None:
            image_path = pathlib.Path(self.image_path)
            new_filename = image_path.with_name("proc-" + image_path.name)

        if (
            self.fibre_data is None
            or self.centroids is None
            or self.raw_hdu is None
            or self.proc_hdu is None
        ):
            raise FVCError("Need to run process_fvc_image before writing the image.")

        proc_hdus = fits.HDUList([fits.PrimaryHDU(), self.proc_hdu])

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

        measured_coords = self.fibre_data.copy()
        measured_coords.reset_index(inplace=True)
        measured_coords.sort_values("positioner_id", inplace=True)
        measured_coords_rec = Table.from_pandas(measured_coords).as_array()
        proc_hdus.append(fits.BinTableHDU(measured_coords_rec, name="FIBERDATA"))

        # Add IEB information
        ieb_keys = config["fvc"]["ieb_keys"]
        ieb_data = {key: -999.0 for key in ieb_keys}

        for key in ieb_keys:
            device_name = ieb_keys[key]
            if self.fps and isinstance(self.fps.ieb, IEB):
                try:
                    device = self.fps.ieb.get_device(device_name)
                    ieb_data[key] = (await device.read())[0]
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

            current_positions["cmdAlpha"] = numpy.nan
            current_positions["cmdBeta"] = numpy.nan
            current_positions["startAlpha"] = numpy.nan
            current_positions["startBeta"] = numpy.nan

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

            rec = Table.from_pandas(current_positions.reset_index()).as_array()
            proc_hdus.append(fits.BinTableHDU(rec, name="POSANGLES"))

        rec = Table.from_pandas(self.centroids.reset_index()).as_array()
        proc_hdus.append(fits.BinTableHDU(rec, name="CENTROIDS"))

        if self.offsets is not None:
            rec = Table.from_pandas(self.offsets.reset_index()).as_array()
            proc_hdus.append(fits.BinTableHDU(rec, name="OFFSETS"))

        await run_in_executor(proc_hdus.writeto, new_filename, checksum=True)

        self.log(f"Processed HDU written to {new_filename}")

        return proc_hdus

    async def apply_correction(
        self,
        offsets: Optional[pandas.DataFrame] = None,
    ):  # pragma: no cover
        """Applies the offsets. Fails if the trajectory is collided or deadlock."""

        self.log("Preparing correction trajectory.")

        if self.offsets is None and offsets is None:
            raise FVCError("Offsets not set or passed. Cannot apply correction.")

        if offsets is None:
            offsets = self.offsets

        assert offsets is not None
        await self.fps.update_position()

        # Setup robot grid.
        grid = get_robot_grid()
        for robot in grid.robotDict.values():
            positioner = self.fps[robot.id]
            robot.setAlphaBeta(positioner.alpha, positioner.beta)

            row = offsets.loc[robot.id, ["alpha_new", "beta_new"]]
            if row.isna().any():
                log.warning(f"Positioner {robot.id}: new position is NaN. Skipping.")
                robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)
            else:
                robot.setDestinationAlphaBeta(row.alpha_new, row.beta_new)

        # Check for collisions.
        collided = [rid for rid in grid.robotDict if grid.isCollided(rid)]
        n_coll = len(collided)
        if n_coll > 0:
            raise FVCError(f"Cannot apply corrections. {n_coll} robots are collided.")

        # Generate trajectories.
        (to_destination, _, did_fail, deadlocks) = await get_path_pair_in_executor(
            grid,
            ignore_did_fail=True,
        )
        if did_fail:
            log.warning(
                f"Found {len(deadlocks)} deadlocks but applying correction anyway."
            )

        self.log("Sending correction trajectory.")
        try:
            await self.fps.send_trajectory(to_destination)
        except TrajectoryError as err:
            raise FVCError(f"Failed executing the correction trajectory: {err}")

        self.log("Correction applied.")

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

        self.log(f"Found {len(objects)} centroids", level=logging.INFO)

        ncentroids = len(calibration.positionerTable) + len(calibration.fiducialCoords)
        self.log(f"Expected {ncentroids} centroids", level=logging.INFO)

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
            target_coords.ywok.to_numpy(),
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
