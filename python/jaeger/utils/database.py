#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-12
# @Filename: database.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from functools import wraps

import peewee

from sdssdb.peewee.sdss5db import targetdb


try:
    import pandas
except ImportError:
    pandas = None


def check_database(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")
        return f(*args, **kwargs)

    return wrapper


@check_database
def load_positioners(start=1, end=1200, observatory=""):
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
        insert = targetdb.Positioner.update(
            {
                "row": row_data.Row,
                "column": row_data.Column,
                "observatory_pk": observatory_pk,
            }
        ).where(targetdb.Positioner.id == pid)

        assert isinstance(insert, peewee.ModelUpdate)
        insert.execute(targetdb.database)
