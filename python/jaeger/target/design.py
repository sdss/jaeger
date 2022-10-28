#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from dataclasses import dataclass

import pandas
import peewee

from coordio.defaults import calibration
from coordio.utils import object_offset
from sdssdb.peewee.sdss5db import targetdb

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
    load_configuration
        Create a `.Configuration` attached to this design.
    epoch
        The JD epoch for which to calculate the configuration coordinates. If
        `None`, uses the current time.
    scale
        Focal plane scale factor to apply. Defaults to coordio's internal value.

    """

    def __init__(
        self,
        design_id: int,
        load_configuration: bool = True,
        epoch: float | None = None,
        scale: float | None = None,
    ):

        if calibration.wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

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
        self.target_data: dict[str, dict] = self.get_target_data()

        self.configuration: Configuration
        if load_configuration:
            self.configuration = Configuration(self, epoch=epoch, scale=scale)

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

        def _offset(group: pandas.DataFrame):

            design_mode = group.iloc[0].design_mode
            fibre_type = group.iloc[0].fibre_type

            design_mode_rec = targetdb.DesignMode.get(label=design_mode)

            if fibre_type == "APOGEE":
                # Use 2MASS H magnitude for APOGEE
                mag = group.h.values
                mag_lim = design_mode_rec.apogee_bright_limit_targets_min
            else:
                # Gaia G for BOSS.
                mag = group.gaia_g.values
                mag_lim = design_mode_rec.boss_bright_limit_targets_min

            if "bright" in design_mode:
                lunation = "bright"
                skybrightness = 1.0
            else:
                lunation = "dark"
                skybrightness = 0.35

            # Hardcoding this for now.
            offset_min_skybrightness = 0.5

            delta_ra, delta_dec, _ = object_offset(
                mag,
                mag_lim,
                lunation,
                fibre_type.capitalize(),
                can_offset=group.can_offset.values,
                skybrightness=skybrightness,
                offset_min_skybrightness=offset_min_skybrightness,
            )

            group.loc[:, "delta_ra"] = delta_ra
            group.loc[:, "delta_dec"] = delta_dec

            return group

        # Convert to data frame to group by fibre type (no need to group by design
        # mode since a design can only have one design mode).
        df = pandas.DataFrame.from_records(target_data)
        df = df.groupby(["fibre_type"], group_keys=False).apply(_offset)

        # Return as a list of dicts again.
        return [vv for vv in df.transpose().to_dict().values()]

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
        epoch: float | None = None,
        scale: float | None = None,
    ):
        """Returns a design while creating the configuration in an executor."""

        self = cls(design_id, load_configuration=False)

        configuration = await run_in_executor(
            Configuration,
            self,
            epoch=epoch,
            scale=scale,
        )
        self.configuration = configuration

        return self

    def __repr__(self):
        return f"<Design (design_id={self.design_id})>"
