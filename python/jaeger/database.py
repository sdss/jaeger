#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-05-23
# @Filename: database.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import warnings

import peewee
from peewee import (
    AutoField,
    BooleanField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

from jaeger.exceptions import JaegerUserWarning


__all__ = ["get_database_connection", "get_positioner_db_data"]


database_proxy = peewee.DatabaseProxy()  # Create a proxy for our db.


def get_database_connection(dbname: str, **kwargs):
    """Returns a database connection to ``targetdb``."""

    conn = peewee.PostgresqlDatabase(dbname, **kwargs)

    try:
        conn.connect()
        database_proxy.initialize(conn)
    except peewee.OperationalError as err:
        warnings.warn(f"Failed connecting to the database: {err}.", JaegerUserWarning)
        return None

    return conn


def get_positioner_db_data(conn: peewee.PostgresqlDatabase, observatory: str):
    """Returns a dictionary of positioner information."""

    if not conn.is_connection_usable():
        warnings.warn("Database connection is not usable.", JaegerUserWarning)
        return {}

    raw = (
        Positioner.select(
            Positioner.id,
            Positioner.xcen,
            Positioner.ycen,
            PositionerStatus.label.alias("status"),
        )
        .join(Observatory)
        .switch(Positioner)
        .join(PositionerStatus)
        .switch(Positioner)
        .join(PositionerInfo)
        .where(Observatory.label == observatory, PositionerInfo.fiducial >> False)
        .dicts()
    )

    data = {}
    for pos_data in raw:
        pid = pos_data.pop("id")
        data[pid] = pos_data

    return data


class TargetdbBase(Model):
    class Meta:
        schema = "targetdb"
        database = database_proxy


class PositionerStatus(TargetdbBase):
    label = TextField(null=True)
    pk = AutoField()

    class Meta:
        table_name = "positioner_status"


class PositionerInfo(TargetdbBase):
    apogee = BooleanField(null=False)
    boss = BooleanField(null=False)
    fiducial = BooleanField(null=False)
    pk = AutoField()

    class Meta:
        table_name = "positioner_info"


class Observatory(TargetdbBase):
    label = TextField()
    pk = AutoField()

    class Meta:
        table_name = "observatory"


class Positioner(TargetdbBase):
    id = IntegerField(null=True)
    observatory = ForeignKeyField(
        column_name="observatory_pk",
        field="pk",
        model=Observatory,
    )
    pk = AutoField()
    status = ForeignKeyField(
        column_name="positioner_status_pk",
        field="pk",
        model=PositionerStatus,
    )
    info = ForeignKeyField(
        column_name="positioner_info_pk",
        field="pk",
        model=PositionerInfo,
    )
    xcen = FloatField(null=True)
    ycen = FloatField(null=True)

    class Meta:
        table_name = "positioner"
