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
from astropy.io import fits
from astropy.table import Table

from clu.command import Command
from clu.legacy.tron import TronConnection
from coordio.defaults import calibration
from coordio.transforms import FVCTransformAPO

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


FVC_CONFIG = config["fvc"]


class FVC:
    """Focal View Camera class."""

    fibre_data: Optional[pandas.DataFrame]
    measurements: Optional[pandas.DataFrame]
    centroids: Optional[pandas.DataFrame]
    offsets: Optional[pandas.DataFrame]

    image_path: Optional[str]
    proc_image_path: Optional[str]
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
        self.correction_applied: bool = False

        self.command = command
        self.fps = FPS.get_instance()

        self.reset()

    def reset(self):
        """Resets the instance."""

        self.fvc_transform: FVCTransformAPO | None = None
        self.fibre_data = None
        self.centroids = None
        self.offsets = None

        self.image_path = None
        self.proc_image_path = None
        self.raw_hdu = None
        self.proc_hdu = None

        self.k = 1
        self.fitrms = -9.99
        self.perc_90 = -9.99
        self.fvc_percent_reached = -9.99

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

        self.log(
            f"Taking {stack} x {exposure_time} seconds FVC exposure.",
            to_command=False,
        )

        tron = None

        if stack <= 1:
            cmd_str = f"talk -c fvc expose {exposure_time}"
        else:
            cmd_str = f"talk -c fvc expose --stack {stack} {exposure_time}"

        if self.command:
            expose_command = self.command.send_command("fliswarm", cmd_str)
        else:
            tron = TronConnection(
                "jaeger.jaeger",
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

        try:
            filename = expose_command.replies.get("filename")[-1]
            self.log(f"FVC raw image is {filename}.", to_command=False)
            return pathlib.Path(filename)
        except KeyError:
            raise FVCError("The exposure succeeded but did not output the filename.")

    def process_fvc_image(
        self,
        path: pathlib.Path | str,
        positioner_coords: dict,
        fibre_data: Optional[pandas.DataFrame] = None,
        fibre_type: str = "Metrology",
        use_winpos: bool = True,
        use_new_invkin: bool = True,
        plot: bool | str = False,
        outdir: str | None = None,
    ) -> tuple[fits.ImageHDU, pandas.DataFrame, pandas.DataFrame]:
        """Processes a raw FVC image.

        Parameters
        ----------
        path
            The path to the raw FVC image.
        positioner_coords
            A dictionary of positioner ID to ``(alpha, beta)`` with the current
            positions of the robots.
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
        use_winpos
            Whether to use windowed position for centroid extraction.
        use_new_invkin
            Use new inverse kinnematic to calculate alpha/beta.
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

        fdata = fibre_data.copy().reset_index().set_index("positioner_id")

        self.log(f"Processing raw image {path}")

        dirname, base = os.path.split(path)
        dirname = outdir or dirname

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

        # Get the rotator angle so that we can derotate the centroids to the
        # x/ywok-aligned configuration.
        rotpos = hdus[1].header.get("IPA", None)
        if rotpos is None:
            raise FVCError("IPA keyword not found in the header.")
        rotpos = float(rotpos) % 360.0

        positioner_df = pandas.DataFrame.from_dict(
            positioner_coords,
            columns=["alphaReport", "betaReport"],
            orient="index",
        )
        positioner_df.index.set_names(["positionerID"], inplace=True)

        fvc_transform = FVCTransformAPO(
            image_data,
            positioner_df,
            rotpos,
            plotPathPrefix=plot_path_root,
        )

        self.centroids = fvc_transform.extractCentroids()
        fvc_transform.fit(useWinpos=use_winpos, newInvKin=use_new_invkin)

        assert fvc_transform.positionerTableMeas is not None

        measured = fvc_transform.positionerTableMeas.copy().set_index("positionerID")

        n_dubious = measured.wokErrWarn.sum()
        if n_dubious > 0:
            self.log(
                f"Found {n_dubious} positioners with dubious centroid matches.",
                level=logging.WARNING,
            )

        metrology_data = fdata.copy().reset_index()
        metrology_data = metrology_data.loc[metrology_data.fibre_type == fibre_type]

        # Create a column to mark positioners with dubious matches.
        fdata.loc[:, "dubious"] = 0
        dubious_pid = measured.loc[measured.wokErrWarn].index.values
        fdata.loc[dubious_pid, "dubious"] = 1

        wok_measured = measured.loc[
            metrology_data.positioner_id, ["xWokMeasMetrology", "yWokMeasMetrology"]
        ]
        fdata.loc[
            fdata.fibre_type == fibre_type, ["xwok_measured", "ywok_measured"]
        ] = wok_measured.values

        fdata = fdata.reset_index().set_index("fibre_type")

        # Only use online, assigned robots for final RMS. First get groups of fibres
        # with an assigned robot, that are not offline or dubious.
        if fdata.assigned.sum() > 0:
            assigned = fdata.groupby("positioner_id").filter(
                lambda g: g.assigned.any()
                & (g.offline == 0).all()
                & (g.dubious == 0).all()
                # & (g.on_target == 1).any()
            )
        else:
            self.log("No assigned fibres found. Using all matched fibres.")
            assigned = fdata.groupby("positioner_id").filter(
                lambda g: (g.offline == 0).all()
                & (g.dubious == 0).all()
                # & (g.on_target == 1).any()
            )

        # Now get the metrology fibre from those groups.
        assigned = assigned.loc[fibre_type]

        # Calculate RMS from assigned fibres.
        dx = assigned.xwok - assigned.xwok_measured
        dy = assigned.ywok - assigned.ywok_measured
        self.fitrms = numpy.round(numpy.sqrt(numpy.mean(dx**2 + dy**2)), 5)
        self.log(f"RMS full fit {self.fitrms * 1000:.3f} um.")

        # Also calculate 90% percentile and percentage of targets below threshold.
        distance = numpy.sqrt(dx**2 + dy**2)

        self.perc_90 = numpy.round(numpy.percentile(distance, 90), 4)

        n_reached = numpy.sum(distance <= (config["fvc"]["target_distance"] / 1000))
        self.fvc_percent_reached = numpy.round(n_reached / len(dx) * 100, 1)

        # FITSRMS is the RMS of measured - expected for assigned, non-disabled
        # robots. This is different from FVC_RMS reported by
        # FVCTransformAPO.getMetadata() that is measured - reported for all
        # positioners.
        hdus[1].header["FITRMS"] = (self.fitrms * 1000, "RMS full fit [um]")
        hdus[1].header["PERC90"] = (self.perc_90 * 1000, "90% percentile [um]")
        hdus[1].header["FVCREACH"] = (
            self.fvc_percent_reached,
            "Targets that have reached their goal [%]",
        )

        fdata.reset_index(inplace=True)
        fdata.set_index(["hole_id", "fibre_type"], inplace=True)

        self.fibre_data = fdata
        self.proc_hdu = hdus[1]
        self.fvc_transform = fvc_transform

        self.log(f"Finished processing {path}", level=logging.DEBUG)

        return (self.proc_hdu, self.fibre_data, self.centroids)

    def calculate_offsets(
        self,
        reported_positions: numpy.ndarray,
        fibre_data: Optional[pandas.DataFrame] = None,
        k: Optional[float] = None,
        max_correction: Optional[float] = None,
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
        max_correction
            The maximum offset allowed per robot and arm, in degrees. Corrections
            larger than ``max_offset`` are clipped.

        Returns
        -------
        new_positions
            The new alpha and beta positions as a Pandas dataframe indexed by
            positions ID. If `None`, uses the value ``fvc.k`` from the configuration.

        """

        self.log("Calculating offset from FVC image and model fit.")

        site = config["observatory"]

        self.k = k or config["fvc"]["k"]
        max_offset: float = max_correction or config["fvc"]["max_correction"]

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
                "Failed to calculate corrected positioner coordinates for "
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

        if self.fps and self.fps.configuration:
            proc_hdus[1].header["CONFIGID"] = self.fps.configuration.configuration_id
        else:
            proc_hdus[1].header["CONFIGID"] = -999.0

        proc_hdus[1].header["CAPPLIED"] = self.correction_applied

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

        fibre_data = self.fibre_data.copy()
        fibre_data.reset_index(inplace=True)
        fibre_data.sort_values("positioner_id", inplace=True)
        fibre_data_rec = Table.from_pandas(fibre_data).as_array()
        proc_hdus.append(fits.BinTableHDU(fibre_data_rec, name="FIBERDATA"))

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

        # Add header keywords from coordio.FVCTransfromAPO.
        if self.fvc_transform:
            proc_hdus[1].header.extend(self.fvc_transform.getMetadata())

        posangles = None
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

            posangles = Table.from_pandas(current_positions).as_array()
        proc_hdus.append(fits.BinTableHDU(posangles, name="POSANGLES"))

        rec = Table.from_pandas(self.centroids).as_array()
        proc_hdus.append(fits.BinTableHDU(rec, name="CENTROIDS"))

        offsets = None
        if self.offsets is not None:
            offsets = Table.from_pandas(self.offsets.reset_index()).as_array()
        proc_hdus.append(fits.BinTableHDU(offsets, name="OFFSETS"))

        await run_in_executor(proc_hdus.writeto, new_filename, checksum=True)

        self.log(f"Processed HDU written to {new_filename}")
        self.proc_image_path = os.path.abspath(new_filename)

        return proc_hdus

    async def apply_correction(
        self,
        offsets: Optional[pandas.DataFrame] = None,
    ):  # pragma: no cover
        """Applies the offsets. Fails if the trajectory is collided or deadlock."""

        if self.fps.locked:
            raise FVCError("The FPS is locked. Cannot apply corrections.")

        self.log("Preparing correction trajectory.")

        if self.offsets is None and offsets is None:
            raise FVCError("Offsets not set or passed. Cannot apply correction.")

        if offsets is None:
            offsets = self.offsets

        assert offsets is not None
        await self.fps.update_position()

        target_distance = config["fvc"]["target_distance"]

        # Setup robot grid.
        grid = get_robot_grid(self.fps)
        for robot in grid.robotDict.values():
            if robot.isOffline:
                continue

            positioner = self.fps[robot.id]
            robot.setAlphaBeta(positioner.alpha, positioner.beta)
            robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)

            if offsets.loc[robot.id].transformation_valid == 0:
                continue

            new = offsets.loc[robot.id, ["alpha_new", "beta_new"]]
            dist = offsets.loc[robot.id, ["xwok_distance", "ywok_distance"]] * 1000.0
            if numpy.hypot(dist.xwok_distance, dist.ywok_distance) > target_distance:
                robot.setDestinationAlphaBeta(new.alpha_new, new.beta_new)
            else:
                robot.isOffline = True

        # Check for collisions. If robots are collided just leave them there.
        collided = grid.getCollidedRobotList()
        n_coll = len(collided)
        if n_coll > 0:
            for pid in collided:
                positioner = self.fps[pid]
                alpha = positioner.alpha
                beta = positioner.beta
                grid.robotDict[pid].setAlphaBeta(alpha, beta)
                grid.robotDict[pid].setDestinationAlphaBeta(alpha, beta)

        # Generate trajectories.
        (to_destination, _, did_fail, deadlocks) = await get_path_pair_in_executor(
            grid,
            ignore_did_fail=True,
            stop_if_deadlock=True,
            ignore_initial_collisions=True,
        )
        if did_fail:
            log.warning(
                f"Found {len(deadlocks)} deadlocks but applying correction anyway."
            )

        self.log("Sending correction trajectory.")
        try:
            await self.fps.send_trajectory(to_destination, command=self.command)
        except TrajectoryError as err:
            raise FVCError(f"Failed executing the correction trajectory: {err}")

        self.correction_applied = True
        self.log("Correction applied.")

    async def write_summary_F(self):
        """Updates data with the last measured positions and write confSummaryF."""

        if self.fps is None or self.fps.configuration is None:
            raise FVCError("write_summary_F can only be called with a configuration.")

        if self.fibre_data is None:
            raise FVCError("No fibre data.")

        self.log("Updating coordinates.", level=logging.DEBUG)

        fdata = (
            self.fibre_data.reset_index()
            .set_index(["positioner_id", "fibre_type"])
            .copy()
        )

        idx = pandas.IndexSlice
        cols = ["hole_id", "xwok_measured", "ywok_measured"]
        measured = fdata.loc[idx[:, "Metrology"], cols].dropna()

        for (pid, ftype), row in measured.iterrows():
            (alpha, beta), _ = wok_to_positioner(
                row.hole_id,
                self.fps.observatory,
                "Metrology",
                row.xwok_measured,
                row.ywok_measured,
            )
            if not numpy.isnan(alpha):
                fdata.loc[idx[pid, :], ["alpha", "beta"]] = (alpha, beta)

        configuration_copy = self.fps.configuration.copy()
        configuration_copy.assignment_data.fibre_table = fdata.copy()

        for pid in measured.index.get_level_values(0).tolist():
            alpha, beta = fdata.loc[(pid, "Metrology"), ["alpha", "beta"]]
            for ftype in ["APOGEE", "BOSS", "Metrology"]:
                configuration_copy.assignment_data.positioner_to_icrs(
                    pid,
                    ftype,
                    alpha,
                    beta,
                    update=True,
                )

        await configuration_copy.write_summary(
            flavour="F",
            headers={
                "fvc_rms": self.fitrms,
                "fvc_90_perc": self.perc_90,
                "fvc_percent_reached": self.fvc_percent_reached,
                "fvc_image_path": self.proc_image_path if self.proc_image_path else "",
            },
            overwrite=True,
        )
