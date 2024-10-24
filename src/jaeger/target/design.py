#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

import numpy
import peewee
import polars

from coordio.defaults import calibration
from coordio.utils import object_offset
from sdssdb.peewee.sdss5db import targetdb

from jaeger import config, log
from jaeger.fps import FPS
from jaeger.target.schemas import TARGET_DATA_SCHEMA
from jaeger.target.too import add_targets_of_opportunity_to_design
from jaeger.utils.database import connect_database
from jaeger.utils.helpers import run_in_executor
from jaeger.utils.utils import Timer

from .configuration import Configuration


__all__ = ["Design"]


@dataclass
class FieldData:
    """Field data for design."""

    field_id: int
    rs_run: str
    observatory: str
    racen: float
    deccen: float
    position_angle: float


class Design:
    """Loads and represents a targetdb design.

    Parameters
    ----------
    design_id
        The ID of the design to load.
    fps
        An instance of the `FPS` class. If not povided, uses `~.BaseFPS.get_instance`.
        In general a custom FPS instance should not be passed, and this argument is
        meant mostly for testing.
    create_configuration
        Create a `.Configuration` attached to this design.
    epoch
        The JD epoch for which to calculate the configuration coordinates. If
        `None`, uses the current time.
    scale
        Focal plane scale factor to apply. Defaults to coordio's internal value.
    safety_factor
        For offset calculation. Factor to add to ``mag_limit``. See ``object_offset``.
    offset_min_skybrightness
        Minimum sky brightness for the offset. See ``object_offset``.
    use_targets_of_opportunity
        Whether to replace targets with targets of opportunity accoding to the
        parameters in ``configuration.targets_of_opportunity``.

    """

    def __init__(
        self,
        design_id: int,
        fps: FPS | None = None,
        create_configuration: bool = True,
        epoch: float | None = None,
        scale: float | None = None,
        safety_factor: float | None = None,
        offset_min_skybrightness: float = 0.0,
        use_targets_of_opportunity: bool = True,
    ):
        if calibration.wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

        log.info(f"Creating Design instance for design_id={design_id}.")

        self.fps = fps or FPS.get_instance()
        self.design_id = design_id

        if connect_database(targetdb.database) is False:
            raise RuntimeError("Cannot connect to database.")

        with Timer() as timer:
            try:
                self.design = targetdb.Design.get(design_id=design_id)
            except peewee.DoesNotExist:
                raise ValueError(f"design_id {design_id} does not exist in DB.")

        log.debug(f"Design data retrieved from DB in {timer.elapsed:.2f} s.")

        self.field = FieldData(
            field_id=self.design.field.field_id,
            rs_run=self.design.field.version.plan if self.design else "NA",
            observatory=self.design.field.observatory.label,
            racen=self.design.field.racen,
            deccen=self.design.field.deccen,
            position_angle=self.design.field.position_angle,
        )

        self.safety_factor = safety_factor
        self.offset_min_skybrightness = offset_min_skybrightness

        log.debug("Loading target data and calculating offsets.")
        with Timer() as timer:
            self.target_data: polars.DataFrame = self.get_target_data()
        log.debug(f"Loaded target data in {timer.elapsed:.2f} s.")

        self.replaced_target_data: polars.DataFrame | None = None

        if use_targets_of_opportunity:
            with Timer() as timer:
                add_targets_of_opportunity_to_design(self)
            log.debug(f"Added targets of opportunity in {timer.elapsed:.2f} s.")

        self.configuration: Configuration
        if create_configuration:
            log.info(f"Creating configuration for design_id={design_id}.")
            self.configuration = Configuration(self, fps=fps, epoch=epoch, scale=scale)

    def get_target_data(self) -> polars.DataFrame:
        """Retrieves target data as a dictionary."""

        # TODO: this is all synchronous which is probably ok because this
        # query should run in < 1s, but at some point maybe we can change
        # this to use async-peewee and aiopg.

        if connect_database(targetdb.database) is False:
            raise RuntimeError("Database is not connected.")

        target_data = (
            targetdb.Design.select(
                targetdb.Assignment.pk.alias("assignment_pk"),
                targetdb.CartonToTarget.pk.alias("carton_to_target_pk"),
                targetdb.CartonToTarget.lambda_eff,
                targetdb.CartonToTarget.delta_ra,
                targetdb.CartonToTarget.delta_dec,
                targetdb.CartonToTarget.can_offset,
                targetdb.CartonToTarget.priority,
                targetdb.Target.catalogid,
                targetdb.Target.ra,
                targetdb.Target.dec,
                targetdb.Target.epoch,
                targetdb.Target.pmra,
                targetdb.Target.pmdec,
                targetdb.Target.parallax,
                targetdb.Magnitude.bp,
                targetdb.Magnitude.g,
                targetdb.Magnitude.h,
                targetdb.Magnitude.i,
                targetdb.Magnitude.z,
                targetdb.Magnitude.r,
                targetdb.Magnitude.rp,
                targetdb.Magnitude.gaia_g,
                targetdb.Magnitude.j,
                targetdb.Magnitude.k,
                targetdb.Magnitude.optical_prov,
                targetdb.Hole.holeid.alias("hole_id"),
                targetdb.Instrument.label.alias("fibre_type"),
                targetdb.Cadence.label.alias("cadence"),
                targetdb.Carton.carton,
                targetdb.Category.label.alias("category"),
                targetdb.Carton.program,
                targetdb.Design.design_mode,
                peewee.SQL("false").alias("is_too"),
            )
            .join(targetdb.Assignment)
            .join(targetdb.CartonToTarget)
            .join(targetdb.Target)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Carton)
            .join(targetdb.Category, peewee.JOIN.LEFT_OUTER)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Cadence, peewee.JOIN.LEFT_OUTER)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Magnitude, peewee.JOIN.LEFT_OUTER)
            .switch(targetdb.Assignment)
            .join(targetdb.Hole)
            .switch(targetdb.Assignment)
            .join(targetdb.Instrument)
            .where(targetdb.Design.design_id == self.design_id)
            .dicts()
        )

        target_data = polars.DataFrame(list(target_data), schema=TARGET_DATA_SCHEMA)

        target_data = self.calculate_offsets(target_data)

        return target_data

    def calculate_offsets(self, target_data: polars.DataFrame):
        """Determines the target offsets."""

        def _offset(group: polars.DataFrame):
            design_mode = group[0, "design_mode"]
            fibre_type = group[0, "fibre_type"]

            design_mode_rec = targetdb.DesignMode.get(label=design_mode)

            mag = numpy.array(
                [
                    group["g"].to_numpy(),
                    group["r"].to_numpy(),
                    group["i"].to_numpy(),
                    group["z"].to_numpy(),
                    group["bp"].to_numpy(),
                    group["gaia_g"].to_numpy(),
                    group["rp"].to_numpy(),
                    group["j"].to_numpy(),
                    group["h"].to_numpy(),
                    group["k"].to_numpy(),
                ]
            )
            mag = mag.astype("f8").T

            if fibre_type == "APOGEE":
                mag_lim = design_mode_rec.apogee_bright_limit_targets_min
            else:
                mag_lim = design_mode_rec.boss_bright_limit_targets_min

            if "bright" in design_mode:
                lunation = "bright"
                skybrightness = 1.0
            else:
                lunation = "dark"
                skybrightness = 0.35

            can_offset = group["can_offset"].to_numpy()

            if can_offset.any():
                # TODO: this should not be necessary but right now there's a bug in
                # object_offset that will return delta_ra=-1 when the design_mode
                # doesn't have any magnitude limits defined.

                delta_ra, delta_dec, offset_flags = object_offset(
                    mag,
                    numpy.array(mag_lim),
                    lunation,
                    fibre_type.capitalize(),
                    config["observatory"].upper(),
                    can_offset=can_offset,
                    skybrightness=skybrightness,
                    safety_factor=self.safety_factor,
                    offset_min_skybrightness=self.offset_min_skybrightness,
                )
            else:
                delta_ra = numpy.zeros(len(group))
                delta_dec = numpy.zeros(len(group))
                offset_flags = numpy.zeros(len(group), dtype=numpy.int32)

            # make bad mag cases nan
            cases = [-999, -9999, 999, 0.0, numpy.nan, 99.9, None]
            mag[numpy.isin(mag, cases)] = numpy.nan

            # check stars that are too bright for design mode
            mag_lim = numpy.array(mag_lim)
            valid_ind = numpy.where(numpy.array(mag_lim) != -999.0)[0]
            mag_bright = numpy.any(mag[:, valid_ind] < mag_lim[valid_ind], axis=1)

            # grab program as below check not valid for skies or standards
            program = group["program"].to_numpy()

            # check offset flags to see if should be used or not
            offset_valid = numpy.zeros(len(group), dtype=bool)
            for i, fl in enumerate(offset_flags):
                # manually check bad flags
                if program[i] == "SKY" or "ops" in program[i]:
                    offset_valid[i] = True
                elif 8 & int(fl) and mag_bright[i]:
                    # if below sky brightness and brighter than mag limit
                    offset_valid[i] = False
                elif 16 & int(fl) and mag_bright[i]:
                    # if can_offset False and brighter than mag limit
                    offset_valid[i] = False
                elif 32 & int(fl):
                    # if brighter than safety limit
                    offset_valid[i] = False
                else:
                    offset_valid[i] = True

            assert isinstance(delta_ra, numpy.ndarray)
            assert isinstance(delta_dec, numpy.ndarray)
            assert isinstance(offset_flags, numpy.ndarray)

            return group.with_columns(
                delta_ra=polars.Series(values=delta_ra, dtype=polars.Float32),
                delta_dec=polars.Series(values=delta_dec, dtype=polars.Float32),
                offset_flags=polars.Series(values=offset_flags, dtype=polars.Int32),
                offset_valid=polars.Series(values=offset_valid, dtype=polars.Boolean),
            )

        log.debug(f"offset_min_skybrightness={self.offset_min_skybrightness}")
        log.debug(f"safety_factor={self.safety_factor}")

        invalid = target_data.filter(~polars.col.offset_valid)
        if len(invalid) > 0:
            log.warning(f"Found {len(invalid)} targets with invalid offsets.")

        # Group by fibre type and apply the offset calculation. delta_ra and delta_dec
        # are modified and target_data is updated.
        target_data = target_data.group_by("fibre_type").map_groups(_offset)

        return target_data

    @classmethod
    def check_design(cls, design_id: int, site: str):
        """Checks if a design exists and is for the current observatory."""

        if connect_database(targetdb.database) is False:
            raise RuntimeError("Database is not connected.")

        exists = (
            targetdb.Design.select()
            .where(targetdb.Design.design_id == design_id)
            .exists()
        )
        if exists is False:
            return False

        observatory = (
            targetdb.Design.select(targetdb.Observatory.label)
            .join(targetdb.DesignToField)
            .join(targetdb.Field)
            .join(targetdb.Observatory)
            .where(targetdb.Design.design_id == design_id)
            .limit(1)
            .scalar()
        )

        return site == observatory

    @classmethod
    async def create_async(
        cls,
        design_id: int,
        fps: FPS | None = None,
        epoch: float | None = None,
        scale: float | None = None,
        boss_wavelength: float | None = None,
        apogee_wavelength: float | None = None,
        **kwargs,
    ):
        """Returns a design while creating the configuration in an executor."""

        self = cls(design_id, fps=fps, create_configuration=False, **kwargs)

        log.info(f"Creating configuration for design_id={self.design_id}.")
        configuration = await run_in_executor(
            Configuration,
            self,
            fps=fps,
            epoch=epoch,
            scale=scale,
            boss_wavelength=boss_wavelength,
            apogee_wavelength=apogee_wavelength,
        )
        self.configuration = configuration

        return self

    def __repr__(self):
        return f"<Design (design_id={self.design_id})>"

    def get_target_data_dict(self) -> dict[str, dict[str, Any]]:
        """Returns a dictionary of the target data keyed by ``hole_id``."""

        td_dicts = self.target_data.to_dicts()

        return {td["hole_id"]: td for td in td_dicts}
