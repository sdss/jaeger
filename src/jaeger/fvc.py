#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-01
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import warnings
from functools import partial

from typing import TYPE_CHECKING, Any, Mapping, Optional

import numpy
import polars
from astropy.io import fits
from astropy.table import Table

from clu.command import Command
from clu.legacy.tron import TronConnection
from coordio import transforms
from coordio.defaults import calibration

from jaeger import config, log
from jaeger.exceptions import FVCError, JaegerUserWarning, TrajectoryError
from jaeger.fps import FPS
from jaeger.ieb import IEB
from jaeger.kaiju import get_path_pair_in_executor, get_robot_grid
from jaeger.plotting import plot_fvc_distances
from jaeger.target import Configuration, Design, read_confSummary, wok_to_positioner
from jaeger.target.tools import get_wok_data
from jaeger.utils import run_in_executor


if TYPE_CHECKING:
    import pandas

    from coordio.transforms import FVCTransformAPO, FVCTransformLCO

    from jaeger.actor import JaegerActor
    from jaeger.target.assignment import NewPositionsType
    from jaeger.target.configuration import BaseConfiguration


__all__ = ["FVC"]


FVC_CONFIG = config["fvc"]


# Create a coroutine out of the original plotFVCResults.
async def plotFVCResultsCo(*args, **kwargs):
    transforms.plotFVCResults(*args, **kwargs)


def plotFVCResultsMP(loop: asyncio.AbstractEventLoop, *args, **kwargs):
    loop.create_task(plotFVCResultsCo(*args, **kwargs))  # type: ignore


def get_transform(observatory: str):
    """Returns the correct coordio FVC transform class for the observatory."""

    if observatory.upper() == "APO":
        from coordio.transforms import FVCTransformAPO

        return FVCTransformAPO

    elif observatory.upper() == "LCO":
        from coordio.transforms import FVCTransformLCO

        return FVCTransformLCO

    else:
        raise ValueError(f"Invalid observatory {observatory}.")


class FVC:
    """Focal View Camera class."""

    configuration: Optional[BaseConfiguration]
    fibre_data: Optional[polars.DataFrame]
    measurements: Optional[polars.DataFrame]
    centroids: Optional[polars.DataFrame]
    offsets: Optional[polars.DataFrame]
    fvc_transform: Optional[FVCTransformAPO | FVCTransformLCO]

    image_path: Optional[str]
    proc_image_path: Optional[str]
    raw_hdu: Optional[fits.ImageHDU]
    proc_hdu: Optional[fits.ImageHDU]

    centroid_method: str | None
    fitrms: float
    k: float

    ieb_data: dict[str, Any] = {}

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

        # To be updated manually by the actor command.
        self.iteration: int = 1

        self.reset()

    def reset(self):
        """Resets the instance."""

        self.configuration = None
        self.fvc_transform: FVCTransformAPO | FVCTransformLCO | None = None
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
        self.centroid_method = config["fvc"]["centroid_method"]

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
        broadcast: bool = False,
    ):
        """Logs a message, including to the command if present."""

        level = int(level)
        msg = "[FVC]: " + msg

        if log and to_log:
            log.log(level, msg)

        if self.command and to_command:
            if level == logging.DEBUG:
                self.command.debug(msg, broadcast=broadcast)
            elif level == logging.INFO:
                self.command.info(msg, broadcast=broadcast)
            elif level == logging.WARNING:
                self.command.warning(msg, broadcast=broadcast)
            elif level == logging.ERROR:
                self.command.error(msg, broadcast=broadcast)

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
        positioner_coords: Mapping[int, tuple[float, float]],
        configuration: Optional[BaseConfiguration] = None,
        fibre_data: Optional[polars.DataFrame] = None,
        fibre_type: str = "Metrology",
        centroid_method: str | None = None,
        use_new_invkin: bool = True,
        polids: list[int] | None = None,
        plot: bool | str = False,
        outdir: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> tuple[fits.ImageHDU, polars.DataFrame, polars.DataFrame | None]:
        """Processes a raw FVC image.

        Parameters
        ----------
        path
            The path to the raw FVC image.
        positioner_coords
            A dictionary of positioner ID to ``(alpha, beta)`` with the current
            positions of the robots.
        configuration
            A configuration object to use for processing. If `None`, defaults to the
            current `.FPS` loaded configuration.
        fibre_data
            A Polars data frame with the expected coordinates of the targets. It
            is expected the data frame will have columns ``positioner_id``,
            ``hole_id``, ``fibre_type``, ``xwok``, and ``ywok``. This frame is
            appended to the processed image. Normally this parameters is left
            empty and the fibre table from the configuration loaded into the FPS
            instace is used.
        fibre_type
            The ``fibre_type`` rows in ``fibre_data`` to use. Defaults to
            ``fibre_type='Metrology'``.
        centroid_method
            The centroid method to use, one of ``"nudge"``, ``"sep"``, ``"winpos"``,
            or ``"simple"``. Defaults to ``"nudge"``.
        use_new_invkin
            Use new inverse kinnematic to calculate alpha/beta.
        polids
            The list of ZB polynomial orders to use. If `None` defaults to
            the coordio ``FVCTransform`` orders.
        plot
            Whether to save additional debugging plots along with the processed image.
            If ``plot`` is a string, it will be used as the directory to which to
            save the plots.
        loop
            The running event loop. Used to schedule the plotting of the FVC
            transform fit as a task.

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

        centroid_method = centroid_method or self.centroid_method

        path = str(path)
        if not os.path.exists(path):
            raise FVCError(f"FVC image {path} does not exist.")

        self.configuration = configuration or self.fps.configuration

        if fibre_data is None:
            if configuration is None:
                raise FVCError("No fibre data and no configuration has been loaded.")
            fibre_data = configuration.fibre_data

        fdata = fibre_data.clone().sort("positioner_id")

        # Set fibre_data initially so that if FVCTransform fails we have something.
        self.fibre_data = fdata.sort(["hole_id", "fibre_type"])

        self.log(f"Processing raw image {path}")

        dirname, base = os.path.split(path)
        dirname = outdir or dirname

        proc_path_root = os.path.join(dirname, "proc-" + base[0 : base.find(".fit")])

        if plot is True:
            plot_path_root = proc_path_root
        elif plot is False or plot is None:
            plot_path_root = None
        elif isinstance(plot, str):
            plot_path_root = os.path.join(plot, "proc-" + base[0 : base.find(".fit")])
        else:
            plot_path_root = ""

        hdus = fits.open(path)

        self.image_path = path
        self.raw_hdu = hdus[1].copy()

        image_data = hdus[1].data.astype(numpy.float32)
        header = hdus[1].header

        # If we are using a dark frame, subtract it now.
        dark_image: str | bool = config["fvc"].get("dark_image", False)
        if dark_image:
            if not os.path.exists(dark_image):
                self.log(
                    f"Dark frame {dark_image} not found. Skipping dark correction.",
                    level=logging.WARNING,
                )
            else:
                dark_data = fits.getdata(dark_image).astype(numpy.float32)
                image_data -= dark_data

        # Invert columns at APO.
        if self.fps.observatory == "APO":
            image_data = image_data[:, ::-1]

        self.log(f"Max counts in image: {numpy.max(image_data)}", level=logging.INFO)

        # Get the rotator angle so that we can derotate the centroids to the
        # x/ywok-aligned configuration.
        rotpos = hdus[1].header.get("IPA", 135.4)
        if rotpos is None:
            raise FVCError("IPA keyword not found in the header.")
        rotpos = float(rotpos) % 360.0

        positioner_df = polars.DataFrame(
            [(kk, *vv) for kk, vv in positioner_coords.items()],
            schema={
                "positionerID": polars.Int32,
                "alphaReport": polars.Float64,
                "betaReport": polars.Float64,
            },
        ).sort("positionerID")

        FVCTransform = get_transform(self.fps.observatory)
        self.log(f"Using FVC transform class {FVCTransform.__name__!r}.")

        if loop:
            # Monkeypatch plotFVCResults to use a task and do plotting asynchronously.
            # It's important to override it here, when a loop already exists.
            transforms.plotFVCResults = partial(plotFVCResultsMP, loop)

        # ZB polynomial orders to use.
        polids = config["fvc"].get("polids", None)
        if polids is None:
            self.log("Using coordio default ZB polynomial orders.")
        else:
            self.log(f"Using ZB polynomial orders: {polids}")

        fvc_transform = FVCTransform(
            image_data,
            positioner_df.to_pandas(),
            rotpos,
            polids=polids,
            plotPathPrefix=plot_path_root,
        )

        self.centroids = polars.from_pandas(fvc_transform.extractCentroids())
        fvc_transform.fit(centType=centroid_method, newInvKin=use_new_invkin)
        self.centroid_method = fvc_transform.centType

        self.log(f"Centroid method: {self.centroid_method}.")

        if self.command:
            self.command.info(fvc_centroid_method=self.centroid_method)

        assert fvc_transform.positionerTableMeas is not None
        positionerTableMeas = fvc_transform.positionerTableMeas.copy()

        measured = polars.from_pandas(positionerTableMeas.drop("index", axis=1))
        measured = measured.sort("positionerID")

        n_dubious = measured["wokErrWarn"].sum()
        if n_dubious > 0:
            self.log(
                f"Found {n_dubious} positioners with dubious centroid matches.",
                level=logging.WARNING,
            )

        # Create a column to mark positioners with dubious matches.
        dubious_pid = measured.filter(polars.col.wokErrWarn)["positionerID"]
        fdata = fdata.with_columns(
            dubious=polars.when(polars.col.positioner_id.is_in(dubious_pid))
            .then(True)
            .otherwise(False)
        )

        metrology_data = fdata.clone()  # Sorted by positioner_id, same as "measured"
        metrology_data = metrology_data.filter(polars.col.fibre_type == fibre_type)

        wok_measured = measured[["xWokMeasMetrology", "yWokMeasMetrology"]]

        fvc_fibre_idx = (fdata["fibre_type"] == fibre_type).arg_true()
        fdata[fvc_fibre_idx, "xwok_measured"] = wok_measured["xWokMeasMetrology"]
        fdata[fvc_fibre_idx, "ywok_measured"] = wok_measured["yWokMeasMetrology"]

        # Only use online, assigned robots for final RMS. First get groups of fibres
        # with an assigned robot, that are not offline or dubious.
        if fdata["assigned"].sum() > 0:
            assigned = fdata.filter(
                (
                    polars.col.assigned.any()
                    & polars.col.offline.not_().all()
                    & polars.col.dubious.not_().all()
                ).over("positioner_id")
            )
        else:
            self.log("No assigned fibres found. Using all matched fibres.")
            assigned = fdata.filter(
                polars.col.offline.not_().all(),
                polars.col.dubious.not_().all(),
            )

        # Now get the metrology fibre from those groups.
        assigned_met = assigned.filter(polars.col.fibre_type == fibre_type)

        # Calculate RMS from assigned fibres.
        dx = (assigned_met["xwok"] - assigned_met["xwok_measured"]).to_numpy()
        dy = (assigned_met["ywok"] - assigned_met["ywok_measured"]).to_numpy()
        self.fitrms = float(numpy.round(numpy.sqrt(numpy.mean(dx**2 + dy**2)), 5))
        self.log(f"RMS full fit {self.fitrms * 1000:.3f} um.")

        # Also calculate 90% percentile and percentage of targets below threshold.
        distance = numpy.sqrt(dx**2 + dy**2)

        self.perc_90 = float(numpy.round(numpy.percentile(distance, 90), 4))

        n_reached = numpy.sum(distance <= (config["fvc"]["target_distance"] / 1000))
        self.fvc_percent_reached = float(numpy.round(n_reached / len(dx) * 100, 1))

        # FITSRMS is the RMS of measured - expected for assigned, non-disabled
        # robots. This is different from FVC_RMS reported by
        # FVCTransformAPO.getMetadata() that is measured - reported for all
        # positioners.
        header["FVCITER"] = (self.iteration, "FVC iteration")
        header["FITRMS"] = (self.fitrms * 1000, "RMS full fit [um]")
        header["PERC90"] = (self.perc_90 * 1000, "90% percentile [um]")
        header["FVCREACH"] = (
            self.fvc_percent_reached,
            "Targets that have reached their goal [%]",
        )
        header["DARKFILE"] = (str(dark_image) or "", "Dark frame image")

        fdata = fdata.sort(["hole_id", "fibre_type"])

        self.fibre_data = fdata
        self.fvc_transform = fvc_transform

        self.proc_hdu = fits.CompImageHDU(data=image_data, header=header)

        self.log(f"Finished processing {path}", level=logging.DEBUG)

        return (self.proc_hdu, self.fibre_data, self.centroids)

    async def update_ieb_info(self):
        """Update the IEB data dictionary."""

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

        self.ieb_data = ieb_data

    def calculate_offsets(
        self,
        reported_positions: numpy.ndarray,
        fibre_data: Optional[polars.DataFrame] = None,
        k: Optional[float] = None,
        max_correction: Optional[float] = None,
    ) -> polars.DataFrame:
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
            The new alpha and beta positions as a Polars dataframe indexed by
            positions ID. If `None`, uses the value ``fvc.k`` from the configuration.

        """

        self.log("Calculating offsets from FVC image and model fit.")

        site = config["observatory"]

        self.k = k or config["fvc"]["k"]
        max_offset: float = max_correction or config["fvc"]["max_correction"]

        if fibre_data is None and self.fibre_data is None:
            raise FVCError("No fibre data passed or stored in the instance.")

        if fibre_data is None:
            fibre_data = self.fibre_data
            assert fibre_data is not None

        fibre_data = fibre_data.clone()
        met = fibre_data.filter(polars.col.fibre_type == "Metrology")

        # TODO: deal with missing data
        invalid = (met["xwok_measured"] == -999.0) | (met["ywok_measured"] == -999.0)
        if invalid.any():
            raise FVCError("Some metrology fibres have not been measured.")

        wok_data = get_wok_data(self.site)

        # Calculate alpha/beta from measured wok coordinates.
        _measured = []
        first = True
        for row in met.iter_rows(named=True):
            (alpha_measured, beta_measured), _ = wok_to_positioner(
                row["hole_id"],
                site,
                "Metrology",
                row["xwok_measured"],
                row["ywok_measured"],
                wok_data=wok_data,
            )

            if "alpha" in row:
                alpha_expected = row["alpha"]
                beta_expected = row["beta"]
            else:
                if first:
                    self.log(
                        "Fibre data does not include the expected alpha/beta "
                        "positions. Using reported alpha/beta.",
                        logging.WARNING,
                    )
                reported_pid = reported_positions[:, 0]
                prow = reported_positions[reported_pid == row["positioner_id"]]
                alpha_expected = prow[0][1]
                beta_expected = prow[0][2]

            # If beta >= 180, we would need a left handed configuration. For now we
            # invalidate these values.
            if beta_expected >= 180.0:
                alpha_measured = numpy.nan
                beta_measured = numpy.nan

            xwok_distance = row["xwok_measured"] - row["xwok"]
            ywok_distance = row["ywok_measured"] - row["ywok"]

            _measured.append(
                (
                    row["hole_id"],
                    row["positioner_id"],
                    xwok_distance,
                    ywok_distance,
                    alpha_expected,
                    beta_expected,
                    alpha_measured,
                    beta_measured,
                )
            )

            first = False

        measured = polars.DataFrame(
            _measured,
            schema={
                "hole_id": polars.String,
                "positioner_id": polars.Int32,
                "xwok_distance": polars.Float64,
                "ywok_distance": polars.Float64,
                "alpha_expected": polars.Float64,
                "beta_expected": polars.Float64,
                "alpha_measured": polars.Float64,
                "beta_measured": polars.Float64,
            },
        ).sort("positioner_id")

        # Merge the reported positions.
        reported = polars.DataFrame(
            reported_positions.tolist(),
            schema={
                "positioner_id": polars.Int32,
                "alpha_reported": polars.Float64,
                "beta_reported": polars.Float64,
            },
        ).sort("positioner_id")

        offsets = reported.join(measured, on="positioner_id", how="left")

        # If there are measured alpha/beta that are NaN, replace those with the
        # previous value.
        offsets = offsets.with_columns(transformation_valid=True)
        test_col = "alpha_measured"
        pos_na = offsets[test_col].is_null() | offsets[test_col].is_nan()
        if pos_na.sum() > 0:
            self.log(
                "Failed to calculate corrected positioner coordinates for "
                f"{pos_na.sum()} positioners.",
                level=logging.WARNING,
            )

            # For now set these values to the expected because we'll use them to
            # calculate the offset (which will be zero for invalid conversions).
            idx = pos_na.arg_true()
            expected = offsets[idx, ["alpha_expected", "beta_expected"]]
            offsets[idx, "alpha_measured"] = expected["alpha_expected"]
            offsets[idx, "beta_measured"] = expected["beta_expected"]
            offsets[idx, "transformation_valid"] = False

        # Calculate offset between expected and measured.
        alpha_offset = offsets["alpha_expected"] - offsets["alpha_measured"]
        beta_offset = offsets["beta_expected"] - offsets["beta_measured"]

        # if alpha measured and alpha reported lie on either side of the
        # wrap, adjust the offset accordingly
        wrap1 = (alpha_offset > 360).arg_true()
        alpha_offset[wrap1] = alpha_offset[wrap1] - 360
        wrap2 = (alpha_offset < -360).arg_true()
        alpha_offset[wrap2] = alpha_offset[wrap2] + 360

        # Clip very large offsets and apply a proportional term.
        alpha_offset_c = numpy.clip(self.k * alpha_offset, -max_offset, max_offset)
        beta_offset_c = numpy.clip(self.k * beta_offset, -max_offset, max_offset)

        # Add new columns to DF.
        offsets = offsets.with_columns(
            alpha_offset=alpha_offset,
            beta_offset=beta_offset,
            alpha_offset_corrected=polars.Series(alpha_offset_c, dtype=polars.Float64),
            beta_offset_corrected=polars.Series(beta_offset_c, dtype=polars.Float64),
        )

        # Calculate new alpha and beta by applying the offset to the reported
        # positions (i.e., the ones the positioners believe they are at).
        alpha_new = offsets["alpha_reported"] + offsets["alpha_offset_corrected"]
        beta_new = offsets["beta_reported"] + offsets["beta_offset_corrected"]

        # Get new positions that are out of range and clip them.
        alpha_oor = ((alpha_new < 0.0) | (alpha_new > 360.0)).arg_true()
        beta_oor = ((beta_new < 0.0) | (beta_new > 180.0)).arg_true()

        alpha_new[alpha_oor] = numpy.clip(alpha_new[alpha_oor], 0, 360)
        beta_new[beta_oor] = numpy.clip(beta_new[beta_oor], 0, 180)

        # For the values out of range, recompute the corrected offsets.
        alpha_corr = alpha_new[alpha_oor] - offsets[alpha_oor, "alpha_reported"]
        offsets[alpha_oor, "alpha_offset_corrected"] = alpha_corr
        beta_corr = beta_new[beta_oor] - offsets[beta_oor, "beta_reported"]
        offsets[beta_oor, "beta_offset_corrected"] = beta_corr

        # Final check. If alpha/beta_new are NaNs, replace with reported values.
        alpha_new_nan = alpha_new.is_nan().arg_true()
        alpha_new[alpha_new_nan] = offsets[alpha_new_nan, "alpha_reported"]
        beta_new_nan = beta_new.is_nan().arg_true()
        beta_new[beta_new_nan] = offsets[beta_new_nan, "beta_reported"]

        # Save new positions.
        offsets = offsets.with_columns(
            alpha_new=alpha_new,
            beta_new=beta_new,
        )

        # Set the invalid alpha/beta_measured back to NaN.
        conds = [
            polars.when(polars.col.transformation_valid.not_())
            .then(float("nan"))
            .otherwise(polars.col(column))
            .alias(column)
            for column in [
                "alpha_measured",
                "beta_measured",
                "alpha_offset",
                "beta_offset",
                "alpha_offset_corrected",
                "beta_offset_corrected",
            ]
        ]
        offsets = offsets.with_columns(*conds)

        self.offsets = offsets

        self.log("Finished calculating offsets.", level=logging.DEBUG)

        return offsets

    async def write_proc_image(
        self,
        new_filename: Optional[str | pathlib.Path] = None,
        broadcast: bool = False,
    ) -> fits.HDUList:
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

        if self.fibre_data is None or self.raw_hdu is None or self.proc_hdu is None:
            raise FVCError("Need to run process_fvc_image before writing the image.")

        proc_hdus = fits.HDUList([fits.PrimaryHDU(), self.proc_hdu])

        if self.configuration:
            proc_hdus[1].header["CONFIGID"] = self.configuration.configuration_id
        else:
            proc_hdus[1].header["CONFIGID"] = -999.0

        proc_hdus[1].header["CAPPLIED"] = self.correction_applied

        # These are Pandas dataframes. We leave them be for convenience.
        positionerTable = calibration.positionerTable
        wokCoords = calibration.wokCoords
        fiducialCoords = calibration.fiducialCoords

        dfs: list[tuple[str, pandas.DataFrame]] = [
            ("POSITIONERTABLE", positionerTable.reset_index()),
            ("WOKCOORDS", wokCoords.reset_index()),
            ("FIDUCIALCOORDS", fiducialCoords.reset_index()),
        ]

        # The FVCTransform tables are also Pandas.
        if self.fvc_transform is not None:
            if self.fvc_transform.positionerTableMeas is not None:
                pos_table_meas = self.fvc_transform.positionerTableMeas
                pos_table_meas = pos_table_meas.drop(columns=["index"])
                dfs.append(("POSITIONERTABLEMEAS", pos_table_meas))
            if self.fvc_transform.fiducialCoordsMeas is not None:
                fid_coords_meas = self.fvc_transform.fiducialCoordsMeas
                fid_coords_meas = fid_coords_meas.drop(columns=["index"])
                dfs.append(("FIDUCIALCOORDSMEAS", fid_coords_meas))

        for name, df in dfs:
            rec = Table.from_pandas(df).as_array()
            table = fits.BinTableHDU(rec, name=name)
            proc_hdus.append(table)

        # fibre_data is Polars.
        fibre_data = self.fibre_data.clone().sort("positioner_id")
        fibre_data_rec = Table.from_pandas(fibre_data.to_pandas()).as_array()
        proc_hdus.append(fits.BinTableHDU(fibre_data_rec, name="FIBERDATA"))

        for key, val in self.ieb_data.items():
            proc_hdus[1].header[key] = val

        # Add header keywords from coordio.FVCTransfromAPO.
        if self.fvc_transform:
            try:
                proc_hdus[1].header.extend(self.fvc_transform.getMetadata())
            except Exception as err:
                self.log(
                    f"Cannot get FVCTransform metadata: {err}",
                    logging.WARNING,
                    broadcast=broadcast,
                )

        posangles = None
        if self.fps:
            await self.fps.update_position()
            positions = self.fps.get_positions()
            current_positions = polars.DataFrame(
                {
                    "positionerID": positions[:, 0].astype(int),
                    "alphaReport": positions[:, 1],
                    "betaReport": positions[:, 2],
                }
            )

            current_positions = current_positions.with_columns(
                cmdAlpha=polars.lit(numpy.nan, dtype=polars.Float64),
                cmdBeta=polars.lit(numpy.nan, dtype=polars.Float64),
                startAlpha=polars.lit(numpy.nan, dtype=polars.Float64),
                startBeta=polars.lit(numpy.nan, dtype=polars.Float64),
            )

            if self.configuration:
                robot_grid = self.configuration.robot_grid

                _cmd_alpha = []
                _cmd_beta = []
                _start_alpha = []
                _start_beta = []

                if len(list(robot_grid.robotDict.values())[0].alphaPath) > 0:
                    for pid in current_positions["positionerID"]:
                        robot = robot_grid.robotDict[pid]
                        _cmd_alpha.append(robot.alphaPath[0][1])
                        _cmd_beta.append(robot.betaPath[0][1])
                        _start_alpha.append(robot.alphaPath[-1][1])
                        _start_beta.append(robot.betaPath[-1][1])

                    current_positions = current_positions.with_columns(
                        cmdAlpha=polars.lit(_cmd_alpha, dtype=polars.Float64),
                        cmdBeta=polars.lit(_cmd_beta, dtype=polars.Float64),
                        startAlpha=polars.lit(_start_alpha, dtype=polars.Float64),
                        startBeta=polars.lit(_start_beta, dtype=polars.Float64),
                    )

            posangles = Table.from_pandas(current_positions.to_pandas()).as_array()
        proc_hdus.append(fits.BinTableHDU(posangles, name="POSANGLES"))

        if self.centroids is not None:
            rec = Table.from_pandas(self.centroids.to_pandas()).as_array()
        else:
            rec = None
        proc_hdus.append(fits.BinTableHDU(rec, name="CENTROIDS"))

        offsets = None
        if self.offsets is not None:
            offsets = Table.from_pandas(self.offsets.to_pandas()).as_array()
        proc_hdus.append(fits.BinTableHDU(offsets, name="OFFSETS"))

        await run_in_executor(proc_hdus.writeto, new_filename, checksum=True)

        self.log(f"Processed HDU written to {new_filename}", broadcast=broadcast)
        self.proc_image_path = os.path.abspath(new_filename)

        return proc_hdus

    async def apply_correction(self, offsets: Optional[polars.DataFrame] = None):
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

            offset_data = offsets.filter(polars.col.positioner_id == robot.id)

            if len(offset_data) == 0 or len(offset_data) > 1:
                raise ValueError(f"Invalid offset data for positioner {robot.id}.")

            if offset_data[0, "transformation_valid"]:
                continue

            new = offset_data[["alpha_new", "beta_new"]]
            dist = offset_data[["xwok_distance", "ywok_distance"]] * 1000.0

            meas_distance = numpy.hypot(dist["xwok_distance"], dist["ywok_distance"])[0]
            if meas_distance > target_distance:
                robot.setDestinationAlphaBeta(new[0, "alpha_new"], new[0, "beta_new"])
            else:
                # Mark robot offline to indicate that we won't move it.
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
            path_generation_mode="greedy",
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

    def write_summary_F(
        self,
        path: str | pathlib.Path | None = None,
        plot: bool = True,
        extra_headers: dict = {},
        broadcast: bool = False,
    ):
        """Updates data with the last measured positions and write confSummaryF."""

        if self.configuration is None:
            raise FVCError("write_summary_F can only be called with a configuration.")

        if self.fibre_data is None:
            raise FVCError("No fibre data.")

        self.log("Updating coordinates.", level=logging.DEBUG, broadcast=broadcast)

        fdata = self.fibre_data.clone().sort(["positioner_id", "fibre_type"])

        keep_cols = ["positioner_id", "hole_id", "xwok_measured", "ywok_measured"]
        measured = (
            fdata.filter(polars.col.fibre_type == "Metrology")
            .select(polars.col(keep_cols))
            .fill_nan(None)
            .drop_nulls()
        )

        new_alpha_beta: NewPositionsType = {}

        wok_data = get_wok_data(self.fps.observatory)
        for row in measured.iter_rows(named=True):
            (alpha, beta), _ = wok_to_positioner(
                row["hole_id"],
                self.fps.observatory,
                "Metrology",
                row["xwok_measured"],
                row["ywok_measured"],
                wok_data=wok_data,
            )
            if not numpy.isnan(alpha) and alpha is not None:
                new_alpha_beta[row["positioner_id"]] = {"alpha": alpha, "beta": beta}

        configuration_copy = self.configuration.copy()
        configuration_copy.assignment.fibre_table = fdata.clone()

        # Update alpha/beta and upstream coordinates.
        configuration_copy.assignment.update_positioner_coordinates(new_alpha_beta)

        if self.proc_hdu and "IPA" in self.proc_hdu.header:
            rotator_angle = round(self.proc_hdu.header["IPA"], 2)
        else:
            rotator_angle = -999.0

        headers = {
            "rotator_angle": rotator_angle,
            "fvc_centroid_method": self.centroid_method or "?",
            "fvc_rms": self.fitrms,
            "fvc_90_perc": self.perc_90,
            "fvc_percent_reached": self.fvc_percent_reached,
            "fvc_image_path": self.proc_image_path if self.proc_image_path else "",
            "temperature": self.ieb_data.get("TEMPT3", -999.0),
        }
        headers.update(extra_headers)

        configuration_copy.write_summary(
            path=path,
            flavour="F",
            headers=headers,
            overwrite=True,
        )

        # Plot analysis of FVC loop.
        if plot and self.proc_image_path:
            if self.configuration.assignment.boresight is None:
                self.log(
                    "Configuration does not have boresight set. "
                    "Cannot produce FVC plots.",
                    level=logging.WARNING,
                    broadcast=broadcast,
                )
            else:
                self.log("Creating FVC plots", level=logging.DEBUG, broadcast=broadcast)

                outpath = str(self.proc_image_path).replace(".fits", "_distances.pdf")

                plot_fvc_distances(
                    self.configuration,
                    configuration_copy.fibre_data,
                    path=outpath,
                )


async def reprocess_configuration(
    configuration_id: int,
    path: pathlib.Path | str | None = None,
    centroid_method: str | None = None,
    use_suffix: bool = True,
):  # pragma: no cover
    """Reprocesses the FVC image from a configuration with a different centroid method.

    Outputs a new ``confSummaryF`` file.

    Parameters
    ----------
    configuration_id
        The configuration ID for which to reprocess data. Must have an existing
        ``confSummaryF`` file in ``$SDSSCORE_DIR``.
    path
        The path where to write the new ``confSummaryF`` file. If `None`, defaults
        to ``$SDSSCORE_DIR``.
    centroid_method
        The centroid method to use, one of ``"nudge"``, ``"sep"``, ``"winpos"``,
        or ``"simple"``.
    use_suffix
        If `True`, the new ``confSummaryF`` path file will have a suffix
        including the centroid mode used.

    Returns
    -------
    path
        The path to the new ``confSummaryF`` file.

    """

    site = config["observatory"]
    confSummaryF_path = Configuration._get_summary_file_path(
        configuration_id,
        site,
        "F",
    )

    if not os.path.exists(confSummaryF_path):
        raise FileNotFoundError(
            f"Cannot find a confSummaryF file for {configuration_id}."
        )

    header, _ = read_confSummary(confSummaryF_path)

    design = Design(
        header["design_id"],
        epoch=header["epoch"],
        scale=header["focal_scale"],
    )

    fps = FPS.get_instance()
    assert not fps.can, "This function cannot be called on a running FPS instance."

    configuration = design.configuration
    configuration.configuration_id = configuration_id

    fvc = FVC(site)

    proc_fimg = header["fvc_image_path"]
    fimg = proc_fimg.replace("proc-", "")

    posangles = fits.getdata(proc_fimg, "POSANGLES")
    fiber_data = polars.DataFrame(fits.getdata(proc_fimg, "FIBERDATA"))

    positioner_coords = {}
    for row in posangles:
        positioner_coords[row["positionerID"]] = (row["alphaReport"], row["betaReport"])

    fvc.process_fvc_image(
        fimg,
        positioner_coords,
        configuration=configuration,
        fibre_data=fiber_data,
        centroid_method=centroid_method,
        plot=False,
    )

    if path is None:
        path = confSummaryF_path

    path = str(path)

    if use_suffix:
        path = path.replace(".par", f"_{fvc.centroid_method}.par")

    fvc.write_summary_F(
        path=path,
        plot=False,
        extra_headers={
            "MJD": header["MJD"],
            "obstime": header["obstime"],
            "temperature": header["temperature"],
        },
    )
