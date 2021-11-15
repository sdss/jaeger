#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import Any

import peewee

from coordio.defaults import calibration
from sdssdb.peewee.sdss5db import targetdb

from jaeger.utils.helpers import run_in_executor

from .configuration import Configuration


__all__ = ["Design"]


class Design:
    """Loads and represents a targetdb design."""

    def __init__(self, design_id: int, load_configuration=True):

        if calibration.wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

        self.design_id = design_id

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field: dict[str, Any] = dict(
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
            self.configuration = Configuration(self)

    def get_target_data(self) -> dict[str, dict]:
        """Retrieves target data as a dictionary."""

        # TODO: this is all synchronous which is probably ok because this
        # query should run in < 1s, but at some point maybe we can change
        # this to use async-peewee and aiopg.

        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")

        target_data = (
            targetdb.Design.select(
                targetdb.Assignment.pk.alias("assignment_pk"),
                targetdb.CartonToTarget.pk.alias("carton_to_target_pk"),
                targetdb.CartonToTarget.lambda_eff,
                targetdb.Target,
                targetdb.Magnitude,
                targetdb.Hole.holeid,
                targetdb.Instrument.label.alias("fibre_type"),
                targetdb.Cadence.label.alias("cadence"),
                targetdb.Carton.carton,
                targetdb.Category.label.alias("category"),
                targetdb.Carton.program,
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

        return {data["holeid"]: data for data in target_data}

    @classmethod
    async def create_async(cls, design_id: int):
        """Returns a design while creating the configuration in an executor."""

        self = cls(design_id, load_configuration=False)

        configuration = await run_in_executor(Configuration, self)
        self.configuration = configuration

        return self

    def __repr__(self):
        return f"<Design (design_id={self.design_id})>"
