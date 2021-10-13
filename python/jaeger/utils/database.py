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

import peewee

from sdssdb.peewee.sdss5db import targetdb


try:
    import pandas
except ImportError:
    pandas = None


__all__ = ["load_sequence_positioners", "load_holeids", "load_fields"]


def check_database(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")
        return f(*args, **kwargs)

    return wrapper


@check_database
def load_sequence_positioners(start=1, end=1200):
    """Loads a sequence of positioners to ``targetdb.positioner``

    Does not populate row or column or observatory_pk.
    Use `.populate_holeid` after running this function.
    """

    rows = ({"id": pid} for pid in range(start, end + 1))
    targetdb.Positioner.insert_many(rows).execute(targetdb.database)


def read_layout_data(data_file: str):
    """Reads a layout file with positioner information."""

    if pandas is None:
        raise ImportError("pandas is required to run this function.")

    data = pandas.read_csv(data_file)

    # Select positioners only.
    data = data.loc[data.Device.str.startswith("P")]

    data.Device = pandas.to_numeric(data.Device.str.slice(1))
    data.Row = pandas.to_numeric(data.Row.str.slice(1))
    data.Column = pandas.to_numeric(data.Column.str.slice(1))

    data = data.sort_values(["Row", "Column"], ascending=[False, True])
    data.set_index(data.Device, inplace=True)

    return data


@check_database
def load_holeids(file: str, observatory: str):
    """Loads the hole IDs associated a list of positioners.

    Parameters
    ----------
    file
        The layout file containing positioners and their association with
        hole positioners.
    observatory
        The observatory for the wok file.
    """

    data = read_layout_data(file)
    observatory = observatory.upper()

    observatory_pk = targetdb.Observatory.get(label=observatory).pk

    for pid, row_data in data.iterrows():
        if row_data.Row == 0:
            holeid = f"R0C{row_data.Column}"
        else:
            holeid = f"R{row_data.Row:+}C{row_data.Column}"

        insert = targetdb.Positioner.update(
            {
                "row": row_data.Row,
                "column": row_data.Column,
                "holeid": holeid,
                "observatory_pk": observatory_pk,
            }
        ).where(targetdb.Positioner.id == pid)

        assert isinstance(insert, peewee.ModelUpdate)
        insert.execute(targetdb.database)


@check_database
def load_fields(plan: str, files: list[str] = None, pattern: str = None):
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
    """

    assert pandas

    from astropy import table
    from astropy.io import fits

    if files is None and pattern:
        files = list(glob(pattern))

    if files is None or len(files) == 0:
        raise ValueError("No files provided.")

    version = targetdb.Version.get_or_create(
        plan=plan,
        target_selection=False,
        robostrategy=True,
    )

    positioner_ids = pandas.DataFrame(
        list(
            targetdb.Positioner.select(
                targetdb.Positioner.id,
                targetdb.Positioner.holeid,
            )
            .where(targetdb.Positioner.holeid.is_null(False))
            .tuples()  # type: ignore
        ),
        columns=["id", "holeid"],
    ).set_index("holeid")

    for file_ in files:
        hdul = fits.open(file_)

        # Create field
        field_id = int(file_.split("-")[-1][:-5])
        observatory = hdul[0].header["OBS"].upper()
        racen = hdul[0].header["RACEN"]
        deccen = hdul[0].header["DECCEN"]
        PA = hdul[0].header["PA"]
        field_cadence = hdul[0].header["FCADENCE"]
        nexp = hdul[0].header["NEXP"]

        insert = targetdb.Field.insert(
            field_id=field_id,
            racen=racen,
            deccen=deccen,
            position_angle=PA,
            slots_exposures=nexp,
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
                "slots_exposures": peewee.EXCLUDED.slots_exposures,
                "position_angle": peewee.EXCLUDED.position_angle,
                "version_pk": peewee.EXCLUDED.version_pk,
                "cadence_pk": peewee.EXCLUDED.cadence_pk,
            },
        )
        field_pk = insert.execute(targetdb.database)  # type: ignore

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
                design_mode_pk=design_modes[n],
            )
            design.save()

            if nexp == 1:
                exp_data = assign_data
                holeIDs = exp_data["holeID"].tolist()
            else:
                exp_data = assign_data[assign_data["holeID"][:, n] != " "]
                holeIDs = exp_data["holeID"][:, n].tolist()

            positioner_ids_exp = positioner_ids.loc[holeIDs].id.tolist()

            insert_data += [
                {
                    "design_id": design.design_id,
                    "positioner_id": positioner_ids_exp[i],
                    "carton_to_target_pk": exp_data["carton_to_target_pk"][i],
                    "instrument_pk": targetdb.Instrument.get(
                        label=exp_data["fiberType_2"][i]
                    ).pk,
                }
                for i in range(len(exp_data))
            ]

        targetdb.Assignment.insert_many(insert_data).execute(targetdb.database)
