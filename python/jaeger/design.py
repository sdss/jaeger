#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import peewee

from sdssdb.peewee.sdss5db import targetdb


class Design:
    """Loads and represents a targetdb design."""

    def __init__(self, design_id: int):

        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field = self.design.field
        self.assignments = list(self.design.assignments)

        self.positioner_assignments = {
            assignment.positioner.id: PositionerAssignment(
                assignment.carton_to_target,
                assignment.positioner,
                assignment.instrument.label,
            )
            for assignment in self.assignments
        }


class PositionerAssignment:
    """Information associated with a robot that has been assigned a target."""

    def __init__(
        self,
        carton_to_target: targetdb.CartonToTarget,
        positioner: targetdb.Positioner,
        fibre_type: str,
    ):

        self.positioner_id = positioner.id

        self.carton_to_target = carton_to_target
        self.positioner = positioner
        self.fibre_type = fibre_type

        self.target = carton_to_target.target

        if getattr(self.positioner, self.fibre_type.lower()) is False:
            raise RuntimeError(
                f"Positioner {self.positioner_id} has no fibre {self.fibre_type}"
            )

        if self.positioner.disabled is True:
            raise RuntimeError(f"Positioner {self.positioner_id} is disabled.")
