#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-12
# @Filename: database.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from functools import wraps
from glob import glob

import pandas
import peewee
from astropy import table
from astropy.io import fits

from sdssdb.peewee.sdss5db import opsdb, targetdb


__all__ = [
    "load_holes",
    "load_fields",
    "get_designid_from_queue",
    "match_assignment_hash",
]


def check_database(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")
        return f(*args, **kwargs)

    return wrapper


@check_database
def get_designid_from_queue() -> int | None:
    """Pops a design from the queue."""

    design = opsdb.Queue.pop()
    if design is None:
        return None

    return design.design_id


@check_database
def load_holes(observatory: str):
    """Loads a list holes to ``targetdb.hole``."""

    targetdb.database.become_admin()

    observatory_pk = targetdb.Observatory.get(label=observatory).pk

    row_start = 13
    row_end = -13
    min_cols = 14

    holes = []
    for row in range(row_start, row_end - 1, -1):
        end_col = min_cols + ((row_start - row) if row >= 0 else (row - row_end))
        for col in range(1, end_col + 1, 1):
            if row == 0:
                holeid = f"R0C{col}"
            else:
                holeid = f"R{row:+}C{col}"

            holes.append(
                dict(
                    row=row,
                    column=col,
                    holeid=holeid,
                    observatory_pk=observatory_pk,
                )
            )

    targetdb.Hole.insert(holes).on_conflict(
        conflict_target=[targetdb.Hole.holeid, targetdb.Hole.observatory],
        action="IGNORE",
    ).execute(targetdb.database)


@check_database
def load_fields(
    plan: str,
    files: list[str] = None,
    pattern: str = None,
    sequential_field: bool = False,
):
    """Loads a series of field.

    Parameters
    ----------
    plan
        The robostrategy run string.
    files
        A list of files to load.
    pattern
        Alternative to ``files``, a pattern to be used with ``glob``
        to retrieve a list of files to load.
    sequential_field
        If `False`, uses the filename to determine the field ID. Otherwise
        sequentially increments the ``field_id`` field starting with the current
        maximum value.
    """

    targetdb.database.become_admin()

    if files is None and pattern:
        files = list(glob(pattern))

    if files is None or len(files) == 0:
        raise ValueError("No files provided.")

    version = targetdb.Version.get_or_create(
        plan=plan,
        target_selection=False,
        robostrategy=True,
    )

    hole_ids = pandas.DataFrame(
        list(
            targetdb.Hole.select(
                targetdb.Hole.pk,
                targetdb.Hole.holeid,
                targetdb.Observatory.label,
            )
            .join(targetdb.Observatory)
            .where(targetdb.Hole.holeid.is_null(False))
            .tuples()
        ),
        columns=["pk", "holeid", "observatory"],
    ).set_index("holeid")

    for file_ in files:
        hdul = fits.open(file_)

        # Create field
        if sequential_field is False:
            field_id = int(file_.split("-")[-1][:-5])
        else:
            field_id = targetdb.Field.select(
                peewee.fn.max(targetdb.Field.field_id)
            ).scalar()
            field_id += 1

        observatory = hdul[0].header["OBS"].upper()
        racen = float(hdul[0].header["RACEN"])
        deccen = float(hdul[0].header["DECCEN"])
        PA = float(hdul[0].header["PA"])
        field_cadence = hdul[0].header["FCADENCE"]
        nexp = hdul[0].header["NEXP"]

        insert = targetdb.Field.insert(
            field_id=field_id,
            racen=racen,
            deccen=deccen,
            position_angle=PA,
            version_pk=version[0].pk,
            cadence_pk=targetdb.Cadence.get(label=field_cadence).pk,
            observatory=targetdb.Observatory.get(label=observatory).pk,
        ).on_conflict(
            conflict_target=[targetdb.Field.field_id],
            preserve=[targetdb.Field.field_id],
            update={
                "observatory_pk": peewee.EXCLUDED.observatory_pk,
                "racen": peewee.EXCLUDED.racen,
                "deccen": peewee.EXCLUDED.deccen,
                "position_angle": peewee.EXCLUDED.position_angle,
                "version_pk": peewee.EXCLUDED.version_pk,
                "cadence_pk": peewee.EXCLUDED.cadence_pk,
            },
        )
        field_pk = insert.execute(targetdb.database)

        # Now create a design for each exposure.
        design_modes = hdul[0].header["DESMODE"].split()

        assign_data = table.hstack(
            (
                table.Table(hdul["ASSIGN"].data),
                table.Table(hdul["TARGET"].data),
            )
        )
        assign_data = assign_data[assign_data["assigned"] > 0]
        insert_data = []

        for n in range(nexp):
            design = targetdb.Design(
                exposure=n + 1,
                field_pk=field_pk,
                design_mode_label=design_modes[n],
            )
            design.save()

            if nexp == 1:
                exp_data = assign_data
                holeIDs = exp_data["holeID"].tolist()
            else:
                exp_data = assign_data[assign_data["holeID"][:, n] != " "]
                holeIDs = exp_data["holeID"][:, n].tolist()

            obs_holes = hole_ids.loc[hole_ids.observatory == observatory]
            holeid_pk_exp = obs_holes.loc[holeIDs].pk.tolist()

            insert_data += [
                {
                    "design_id": design.design_id,
                    "hole_pk": holeid_pk_exp[i],
                    "carton_to_target_pk": exp_data["carton_to_target_pk"][i],
                    "instrument_pk": targetdb.Instrument.get(
                        label=exp_data["fiberType_2"][i]
                    ).pk,
                }
                for i in range(len(exp_data))
            ]

        targetdb.Assignment.insert_many(insert_data).execute(targetdb.database)


def match_assignment_hash(design_id1: int, design_id2: int):
    """Checks if the assignment hashes of two designs match."""

    if design_id1 == design_id2:
        return True

    design1 = targetdb.Design.get_by_id(design_id1)
    design2 = targetdb.Design.get_by_id(design_id2)

    return design1.assignment_hash.hex == design2.assignment_hash.hex
