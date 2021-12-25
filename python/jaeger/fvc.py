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
import scipy.spatial.distance
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


FVC_CONFIG = config["fvc"]


class FVC:
    """Focal View Camera class."""

    fibre_data: Optional[pandas.DataFrame]
    measurements: Optional[pandas.DataFrame]
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
        self.correction_applied: bool = False

        self.command = command
        self.fps = FPS.get_instance()

        self.reset()

    def reset(self):
        """Resets the instance."""

        self.fibre_data = None
        self.measurements = None
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
        outdir: str | None = None,
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
        fdata = self.fibre_data  # For shots

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

        self.centroids = self.extract(image_data)

        fiducialCoords = calibration.fiducialCoords.loc[self.site]

        xCMM = fiducialCoords.xWok.to_numpy()
        yCMM = fiducialCoords.yWok.to_numpy()
        xyCMM = numpy.array([xCMM, yCMM]).T

        xyCCD = self.centroids[["x", "y"]].to_numpy()

        # Get the rotator angle so that we can derotate the centroids to the
        # x/ywok-aligned configuration.
        rotpos = hdus[1].header.get("IPA", None)
        if rotpos is None:
            self.log(
                "IPA keyword not found in the header. Assuming that "
                "the FVC image is already derotated.",
                level=logging.WARNING,
                to_command=False,
            )

        # The angle to derotate is rotpos minus the reference rotator position, CCW.
        rotpos = float(rotpos) % 360.0
        theta = rotpos - FVC_CONFIG["reference_rotator_position"]

        theta_rad = numpy.deg2rad(theta)
        sin_theta = numpy.sin(theta_rad)
        cos_theta = numpy.cos(theta_rad)

        # Rotation of theta degrees CCW around x0, y0. First we translate to a
        # rotation around the origin, then rotate and translate back.
        x0, y0 = FVC_CONFIG["centre_rotation"]
        trans_matrix = numpy.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]])
        trans_neg_matrix = numpy.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]])
        rot_matrix = numpy.array(
            [[cos_theta, -sin_theta, 0], [sin_theta, cos_theta, 0], [0, 0, 1]]
        )

        xyzCCD = numpy.hstack((xyCCD, numpy.ones((xyCCD.shape[0], 1))))
        xyzCCD = numpy.dot(
            trans_matrix,
            numpy.dot(
                rot_matrix,
                numpy.dot(trans_neg_matrix, xyzCCD.T),
            ),
        ).T
        xyCCD = xyzCCD[:, 0:2]

        fibre_data_met = fdata.loc[fibre_type].set_index("positioner_id")

        # Get close enough to associate the correct centroid with the correct fiducial.
        x_wok_expect = numpy.concatenate([xCMM, fibre_data_met.xwok.to_numpy()])
        y_wok_expect = numpy.concatenate([yCMM, fibre_data_met.ywok.to_numpy()])
        xy_wok_expect = numpy.array([x_wok_expect, y_wok_expect]).T

        rt = RoughTransform(xyCCD, xy_wok_expect)
        xy_wok_rough = rt.apply(xyCCD)

        # 1. First associate fiducials and build first round just use outer fiducials
        rCMM = numpy.sqrt(xyCMM[:, 0] ** 2 + xyCMM[:, 1] ** 2)
        keep = rCMM > 310
        xyCMMouter = xyCMM[keep, :]

        xyCMMouter_matched_idx, xy_wok_rough_idx, distances = arg_nearest_neighbor(
            xyCMMouter,
            xy_wok_rough,
            FVC_CONFIG["max_rough_fit_distance"],
        )

        n_rejected = len(xyCMMouter) - len(xyCMMouter_matched_idx)
        if n_rejected:
            self.log(
                f"Rejected {n_rejected} rough fiducial matches.",
                level=logging.WARNING,
                to_command=False,
            )
        max_fid_rough_dist = numpy.max(distances[xyCMMouter_matched_idx])
        self.log(
            f"Max. valid fiducial rough distance: {numpy.max(max_fid_rough_dist):.3f}",
            level=logging.INFO,
        )

        xy_fiducial_CCD = xyCCD[xy_wok_rough_idx]
        xy_fiducial_wok_rough = xy_wok_rough[xy_wok_rough_idx]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_rough,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_roughassoc.pdf",
                xy_fiducial=xy_fiducial_wok_rough,
                xy_fiducial_cmm=xyCMMouter[xyCMMouter_matched_idx],
                title="Rough fiducial association",
            )

        ft = ZhaoBurgeTransform(
            xy_fiducial_CCD,
            xyCMMouter[xyCMMouter_matched_idx],
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

        # 2. Re-associate fiducials, some could have been wrongly associated in
        # first fit but second fit should be better?
        xyCMM_matched_idx, xy_wok_meas_idx, distances = arg_nearest_neighbor(
            xyCMM,
            xy_wok_meas,
            FVC_CONFIG["max_fiducial_fit_distance"],
        )

        n_rejected = len(xyCMM) - len(xyCMM_matched_idx)
        if n_rejected:
            self.log(
                f"Rejected {n_rejected} fiducial matches.",
                level=logging.WARNING,
                to_command=False,
            )
        max_fid_dist = numpy.max(distances[xyCMM_matched_idx])
        self.log(
            f"Max. valid fiducial distance: {numpy.max(max_fid_dist):.3f}",
            level=logging.INFO,
        )

        xy_fiducial_CCD = xyCCD[xy_wok_meas_idx]  # Overwriting
        xy_fiducial_wok_refine = xy_wok_meas[xy_wok_meas_idx]

        if plot:
            self.plot_fvc_assignments(
                xy_wok_meas,
                fibre_data_met,
                xCMM,
                yCMM,
                plot_path_root + "_refineassoc.pdf",
                title="Refined fiducial association",
                xy_fiducial=xy_fiducial_wok_refine,
                xy_fiducial_cmm=xyCMM[xyCMM_matched_idx],
            )

        # Try a new transform
        ft = ZhaoBurgeTransform(
            xy_fiducial_CCD,
            xyCMM[xyCMM_matched_idx],
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

        xy_expect_matched_idx, xy_wok_meas_idx, distances = arg_nearest_neighbor(
            xy_expect_pos,
            xy_wok_meas,
            FVC_CONFIG["max_final_fit_distance"],
        )

        n_rejected = len(xy_expect_pos) - len(xy_expect_matched_idx)
        if n_rejected:
            self.log(
                f"Rejected {n_rejected} metrology matches.",
                level=logging.WARNING,
                to_command=False,
            )

        # Set all in mismatched column to 1, then set to zero all the one we matched.
        fdata.loc[:, "mismatched"] = 1
        matched_pids = fibre_data_met.iloc[xy_expect_matched_idx].index.tolist()
        matched_idx = fdata.positioner_id.isin(matched_pids)
        fdata.loc[matched_idx, "mismatched"] = 0

        max_met_dist = numpy.max(distances[xy_expect_matched_idx])
        self.log(
            f"Max. valid metrology distance: {numpy.max(max_met_dist):.3f}",
            level=logging.INFO,
        )

        # Assign measured xywok to fibres with valid matches.
        xy_wok_robot_meas = xy_wok_meas[xy_wok_meas_idx]

        fdata.loc[
            (fdata.index == fibre_type) & matched_idx,
            ["xwok_measured", "ywok_measured"],
        ] = xy_wok_robot_meas

        # Only use online, assigned robots for final RMS. First get groups of fibres
        # with an assigned robot, that are not offline or mismatched.
        if fdata.assigned.sum() > 0:
            assigned = fdata.groupby("positioner_id").filter(
                lambda g: g.assigned.any()
                & (g.offline == 0).all()
                & (g.mismatched == 0).all()
            )
        else:
            self.log("No assigned fibres found. Using all matched fibres.")
            assigned = fdata.groupby("positioner_id").filter(
                lambda g: (g.offline == 0).all() & (g.mismatched == 0).all()
            )

        # Now get the metrology fibre from those groups.
        assigned = assigned.loc[fibre_type]

        # Calculate RMS from assigned fibres.
        dx = assigned.xwok - assigned.xwok_measured
        dy = assigned.ywok - assigned.ywok_measured
        self.fitrms = numpy.round(numpy.sqrt(numpy.mean(dx ** 2 + dy ** 2)), 5)
        self.log(f"RMS full fit {self.fitrms * 1000:.3f} um.")

        hdus[1].header["FITRMS"] = (self.fitrms * 1000, "RMS full fit [um]")

        fdata.reset_index(inplace=True)
        fdata.set_index(["hole_id", "fibre_type"], inplace=True)
        self.proc_hdu = hdus[1]

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

        if self.measurements is not None:
            pos_meas_columns = ["alpha_measured", "beta_measured"]
            alpha_beta_measured = self.offsets.loc[:, pos_meas_columns]
            self.measurements.loc[:, pos_meas_columns] = alpha_beta_measured

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

        if self.measurements:
            measurements_rec = Table.from_pandas(self.measurements).as_array()
        else:
            measurements_rec = None
        proc_hdus.append(fits.BinTableHDU(measurements_rec, name="MEASUREMENTS"))

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

        # Setup robot grid.
        grid = get_robot_grid(self.fps)
        for robot in grid.robotDict.values():
            if robot.isOffline:
                continue

            positioner = self.fps[robot.id]
            robot.setAlphaBeta(positioner.alpha, positioner.beta)

            row = offsets.loc[robot.id, ["alpha_new", "beta_new"]]
            if row.isna().any():
                log.warning(f"Positioner {robot.id}: new position is NaN. Skipping.")
                robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)
            else:
                robot.setDestinationAlphaBeta(row.alpha_new, row.beta_new)

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

    def write_summary_F(self):
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

        self.fps.configuration.assignment_data.fibre_table = fdata.copy()

        for pid in measured.index.get_level_values(0).tolist():
            alpha, beta = fdata.loc[(pid, "Metrology"), ["alpha", "beta"]]
            for ftype in ["APOGEE", "BOSS", "Metrology"]:
                self.fps.configuration.assignment_data.positioner_to_icrs(
                    pid,
                    ftype,
                    alpha,
                    beta,
                    update=True,
                )

        self.fps.configuration.write_summary(
            flavour="F",
            headers={"fvc_rms": self.fitrms},
            overwrite=True,
        )

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


def arg_nearest_neighbor(
    xyA: numpy.ndarray,
    xyB: numpy.ndarray,
    atol: float | None = None,
):
    """Finds the nearest neighbour in list B for each target in list A.

    If the distance between the item in A and the closest element in B is greater
    than ``atol``, a match is not returned.

    Parameters
    ----------
    xyA
        The list we want to match.
    xyB
        The reference table.
    atol
        The maximum allowed distance. `None` to not do any distance checking.

    Returns
    -------
    result
        A tuple with the indices in ``A`` that have been matched, the matching index
        in ``B`` for each matched element in ``A``, and the distance from each
        element in ``A`` to the nearest neighbour in ``B`` (regardless of whether
        that distance is greater than ``atol``).

    """

    xyA = numpy.array(xyA)
    xyB = numpy.array(xyB)

    distances = scipy.spatial.distance.cdist(xyA, xyB)

    min_distances = numpy.array([numpy.min(d) for d in distances])
    indexB = numpy.array([numpy.argmin(d) for d in distances])

    if atol is not None:
        good_matches = numpy.where(min_distances < atol)[0]
    else:
        good_matches = numpy.arange(len(indexB))

    return good_matches, indexB[good_matches], min_distances
