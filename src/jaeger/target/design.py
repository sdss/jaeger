#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from dataclasses import dataclass

import numpy
import peewee
import polars

from coordio.defaults import calibration
from coordio.utils import object_offset
from sdssdb.peewee.sdss5db import targetdb

from jaeger import config, log
from jaeger.fps import FPS
from jaeger.utils.database import connect_database
from jaeger.utils.helpers import run_in_executor

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

    """

    def __init__(
        self,
        design_id: int,
        fps: FPS | None = None,
        create_configuration: bool = True,
        epoch: float | None = None,
        scale: float | None = None,
        safety_factor: float = 0.1,
        offset_min_skybrightness: float = 0.5,
    ):
        if calibration.wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

        self.fps = fps or FPS.get_instance()
        self.design_id = design_id

        if connect_database(targetdb.database) is False:
            raise RuntimeError("Cannot connect to database.")

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

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
        self.target_data: dict[str, dict] = self.get_target_data()

        self.configuration: Configuration
        if create_configuration:
            self.configuration = Configuration(self, fps=fps, epoch=epoch, scale=scale)

    def get_target_data(self) -> dict[str, dict]:
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
                targetdb.Target,
                targetdb.Magnitude,
                targetdb.Hole.holeid,
                targetdb.Instrument.label.alias("fibre_type"),
                targetdb.Cadence.label.alias("cadence"),
                targetdb.Carton.carton,
                targetdb.Category.label.alias("category"),
                targetdb.Carton.program,
                targetdb.Design.design_mode,
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

        target_data = self.calculate_offsets(target_data)

        return {data["holeid"]: data for data in list(target_data)}

    def calculate_offsets(self, target_data: list[dict]):
        """Determines the target offsets."""

        def _offset(group: polars.DataFrame):
            design_mode = group[0, "design_mode"]
            fibre_type = group[0, "fibre_type"]

            design_mode_rec = targetdb.DesignMode.get(label=design_mode)

            mag = numpy.array(
                [
                    group["gaia_g"].to_numpy(),
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

                delta_ra, delta_dec, _ = object_offset(
                    mag,
                    mag_lim,
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

            assert isinstance(delta_ra, numpy.ndarray)
            assert isinstance(delta_dec, numpy.ndarray)

            return group.with_columns(
                delta_ra=polars.Series(values=delta_ra, dtype=polars.Float32),
                delta_dec=polars.Series(values=delta_dec, dtype=polars.Float32),
            )

        log.debug(f"offset_min_skybrightness={self.offset_min_skybrightness}")
        log.debug(f"safety_factor={self.safety_factor}")

        # Convert to data frame to group by fibre type (no need to group by design
        # mode since a design can only have one design mode).
        df = polars.DataFrame(list(target_data), infer_schema_length=None)
        df = df.groupby("fibre_type").apply(_offset)

        # Return as a list of dicts again.
        return df.rows(named=True)

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
