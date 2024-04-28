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

from typing import TYPE_CHECKING

import peewee
import polars
from astropy import table
from astropy.io import fits

from sdssdb.peewee.sdss5db import opsdb, targetdb

from jaeger import config


if TYPE_CHECKING:
    from sdssdb.connection import PeeweeDatabaseConnection

__all__ = [
    "connect_database",
    "load_holes",
    "load_fields",
    "get_designid_from_queue",
    "match_assignment_hash",
]


def connect_database(database: PeeweeDatabaseConnection, force: bool = False):
    """Connects the database if it is not."""

    if database.connected and force is False:
        return True

    database.connect(**config["database"])

    return database.connected


def check_database(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if targetdb.database.connected is False:
            if connect_database(targetdb.database) is False:
                raise RuntimeError("Database is not connected.")
        return f(*args, **kwargs)

    return wrapper


@check_database
def get_designid_from_queue(
    pop: bool = True,
    epoch_delay: bool = False,
) -> tuple[int | None, float | None]:
    """Pops a design from the queue.

    Parameters
    ----------
    pop
        If `True`, pops the next design from the queue. Otherwise just returns the
        design ID.
    epoch_delay
        If `True`, calculates the delta epoch that optimises observing all the
        queued designs with the same hash.

    Returns
    -------
    result
        A tuple with the design ID as the first element and the delta epoch, in
        seconds as the second one. If ``epoch_delay=False``, the delta epoch is
        `None`.


    """

    if pop:
        design = opsdb.Queue.pop()
    else:
        design = (
            opsdb.Queue.select()
            .where(opsdb.Queue.position > 0)
            .order_by(opsdb.Queue.position)
            .first()
        )

    if design is None:
        return (None, None)

    if epoch_delay is False:
        return (design.design_id, None)

    # Get a list of all the entries in the queue that have the
    # same hash as the first one.
    hash = targetdb.Design.get_by_id(design.design_id).assignment_hash.hex

    design_hashes = (
        opsdb.Queue.select(opsdb.Queue.position)
        .join(targetdb.Design, on=(targetdb.Design.design_id == opsdb.Queue.design_id))
        .where(targetdb.Design.assignment_hash == hash)
        .where(opsdb.Queue.position >= (1 if pop is False else -1))
        .order_by(opsdb.Queue.position)
        .tuples()
    )

    # This should not happen, but if the first entry does not have position 1,
    # just leave because something weird happened. Note that the position can be -1
    # if we have pop the design.
    positions = list(zip(*design_hashes))[0]
    if positions[0] > 1:
        return (design.design_id, 0.0)

    # Count how many consecutive positions there are.
    n_designs: int = 1
    if len(positions) > 1:
        positions = [p if p >= 0 else p + 1 for p in positions]
        for idx in range(1, len(positions)):
            if positions[idx] == positions[idx - 1] + 1:
                n_designs += 1
            else:
                break

    # Cap to the number of designs after which we'll force a reconfiguration.
    max_designs_epoch: int = config["configuration"]["max_designs_epoch"]
    if n_designs > max_designs_epoch:
        n_designs = max_designs_epoch

    return (design.design_id, n_designs / 2 * 900.0)


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
    files: list[str] | None = None,
    pattern: str | None = None,
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

    hole_ids = polars.DataFrame(
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
        schema=["pk", "holeid", "observatory"],
    ).sort("holeid")

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

            ft2 = exp_data["fiberType_2"]

            obs_holes = hole_ids.filter(polars.col.observation == observatory)
            holes_filter = obs_holes.filter(polars.col.holeid.is_in(holeIDs))
            holeid_pk_exp = holes_filter["pk"].to_list()

            insert_data += [
                {
                    "design_id": design.design_id,
                    "hole_pk": holeid_pk_exp[i],
                    "carton_to_target_pk": exp_data["carton_to_target_pk"][i],
                    "instrument_pk": targetdb.Instrument.get(label=ft2[i]).pk,
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
