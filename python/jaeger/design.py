#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import warnings

from typing import Optional, Union, cast

import numpy
import pandas
import peewee
from coordio import ICRS, Field, FocalPlane, Observed, Site, Tangent, Wok, positioner
from coordio.defaults import INST_TO_WAVE, positionerTable, wokCoords

from sdssdb.peewee.sdss5db import targetdb

from jaeger import log
from jaeger.exceptions import JaegerError, JaegerUserWarning


PositionerType = Union[positioner.PositionerApogee, positioner.PositionerBoss]


class Design:
    """Loads and represents a targetdb design."""

    def __init__(self, design_id: int):

        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")

        log.debug(f"[Design]: loading design {design_id}.")

        self.design_id = design_id

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field = self.design.field
        self.assignments = list(self.design.assignments)

        log.debug(f"[Design]: creating positioner assignments for {design_id}.")

        self.positioner_grid = PositionerGrid(self, self.assignments)

        log.debug("[Design]: finished creating assignments.")


class PositionerGrid:
    """Information about the targets associated with a grid of positioners."""

    observed_boresight: Observed

    icrs: ICRS
    observed: Observed
    focal: FocalPlane
    tangent: Tangent
    positioner_objs: list[PositionerType]
    valid: numpy.ndarray
    positioner: numpy.ndarray

    def __init__(self, design: Design, assignments: list[targetdb.Assignment]):

        self.design = design
        self.design_id = self.design.design_id
        self.observatory: str = self.design.field.observatory.label.upper()
        self.site = Site(self.observatory)

        positioner_table = positionerTable[positionerTable.wokID == self.observatory]
        positioner_table.set_index("holeID", inplace=True)
        wok_table = wokCoords[wokCoords.wokType == self.observatory]
        wok_table.set_index("holeID", inplace=True)

        self.assignments = [
            assg
            for assg in assignments
            if assg.instrument.label.lower()  # TODO: remove this when RS matches wok.
            in wok_table.loc[assg.hole.holeid].holeType.lower()
        ]

        self.holeids: list[str] = [assg.hole.holeid for assg in self.assignments]

        self.positioner_ids = positioner_table.loc[self.holeids].positionerID.tolist()
        self.positioner_ids = cast(list[int], self.positioner_ids)

        self.targets: list[targetdb.Target] = [
            assignment.carton_to_target.target for assignment in self.assignments
        ]

        self.wok_data = wok_table.loc[self.holeids]
        assert isinstance(self.wok_data, pandas.DataFrame)

        assert len(self.wok_data) == len(self.holeids), "invalid number of hole_ids"

        self.fibre_types: list[str] = [
            assg.instrument.label for assg in self.assignments
        ]
        self.wavelengths: list[float] = [
            INST_TO_WAVE[ft.capitalize()] for ft in self.fibre_types
        ]

        if (
            not self.wok_data.reset_index()
            .apply(
                lambda row: self.fibre_types[row.name].lower() in row.holeType.lower(),
                axis=1,
            )
            .all()
        ):
            raise RuntimeError("Mismatch of fibre types to positioners.")

        self.compute_coordinates()

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems."""

        target_data = (
            targetdb.Target.select(
                targetdb.Target.ra,
                targetdb.Target.dec,
                targetdb.Target.pmra,
                targetdb.Target.pmdec,
                targetdb.Target.parallax,
            )
            .join(targetdb.CartonToTarget)
            .join(targetdb.Assignment)
            .join(targetdb.Design)
            .switch(targetdb.Assignment)
            .join(targetdb.Hole)
            .where(
                targetdb.Design.design_id == self.design_id,
                targetdb.Hole.holeid.in_(self.holeids),
            )
            .tuples()
        )
        target_data = numpy.array(target_data, dtype=numpy.float64)

        assert numpy.all(~numpy.isnan(target_data[:, 0:2]))

        self.icrs = ICRS(
            target_data[:, 0:2],
            pmra=numpy.nan_to_num(target_data[:, 2], nan=0),
            pmdec=numpy.nan_to_num(target_data[:, 3], nan=0),
            parallax=numpy.nan_to_num(target_data[:, 4]),
        )

        self.site.set_time(jd)

        self.observed = Observed(
            [[1, 2, 3]],
            wavelength=self.wavelengths,
            site=self.site,
        )

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.observed_boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        field = Field(
            self.observed,
            field_center=self.observed_boresight,
        )

        self.focal = FocalPlane(
            field,
            wavelength=self.wavelengths,
            site=self.site,
        )
        wok = Wok(
            self.focal,
            site=self.site,
            obsAngle=self.design.field.position_angle,
        )
        self.tangent = Tangent(
            wok,
            holeID=self.holeids,
            site=self.site,
            wavelength=self.wavelengths,  # This just to prevent a warning.
        )

        # coordio doesn't allow a single instance of PositionerBase to contain
        # an array of holeIDs and fibre types. For now we create a list of positioner
        # instances for each hole and fibre.
        self.positioner_objs = []
        for ipos in range(len(self.targets)):
            tan_coords = self.tangent[[ipos]]

            ftype = self.fibre_types[ipos]
            holeid = self.holeids[ipos]

            if ftype.upper() == "BOSS":
                positioner_class = positioner.PositionerBoss
            elif ftype.upper() == "APOGEE":
                positioner_class = positioner.PositionerApogee
            else:
                raise ValueError(f"Invalid fibre type {ftype}.")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.positioner_objs.append(
                    positioner_class(
                        tan_coords,
                        site=self.site,
                        holeID=holeid,
                    )
                )

        if any([pos.positioner_warn[0] for pos in self.positioner_objs]):
            warnings.warn(
                "Some coordinates failed while converting to "
                "positioner coordinates. Skipping.",
                JaegerUserWarning,
            )

        self.valid = numpy.where([~p.positioner_warn[0] for p in self.positioner_objs])

        self.positioner = numpy.vstack(
            [p.astype(numpy.float32) for p in self.positioner_objs]
        )

        if not (self.positioner[self.valid][:, 1] < 180).all():
            raise JaegerError("Some beta coordinates are < 180.")

        self.tangent = Tangent(wok, holeID=self.holeids, site=self.site)
