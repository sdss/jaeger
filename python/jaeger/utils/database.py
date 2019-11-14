#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-14
# @Filename: database.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import os

from peewee import (AutoField, BooleanField, DateTimeField, FloatField,
                    ForeignKeyField, IntegerField, Model, SqliteDatabase, TextField)


# The foreign_keys pragma enforces that foreign key values exist.
db = SqliteDatabase(None, pragmas={'foreign_keys': 1})


class BaseModel(Model):
    class Meta:
        database = db


class Positioner(BaseModel):

    id = IntegerField(primary_key=True)
    x_center = FloatField(null=True)
    y_center = FloatField(null=True)


class Goto(BaseModel):

    pk = AutoField(primary_key=True)
    positioner = ForeignKeyField(Positioner, field='id', backref='moves')
    x_center = FloatField(null=True)
    y_center = FloatField(null=True)
    start_time = DateTimeField(null=True)
    end_time = DateTimeField(null=True)
    alpha_start = FloatField(null=True)
    beta_start = FloatField(null=True)
    alpha_move = FloatField(null=True)
    beta_move = FloatField(null=True)
    alpha_speed = FloatField(null=False)
    beta_speed = FloatField(null=False)
    alpha_end = FloatField(null=True)
    beta_end = FloatField(null=True)
    relative = BooleanField(null=True)
    status_start = IntegerField(null=True)
    status_end = IntegerField(null=True)
    success = BooleanField(null=True)
    fail_reason = TextField(null=True)


def get_qa_database(path, create=True):
    """Returns the QA database object. Models are attached for convenience.

    Parameters
    ----------
    create : bool
        If `True` and the database file does not exist, creates a new
        database.

    Returns
    -------
    `~peewee.SqliteDatabase`
        The `~peewee.SqliteDatabase` object.

    """

    exists = os.path.exists(path)

    if not exists and not create:
        raise RuntimeError('database does not exist and create=False.')

    if not exists:
        dirname = os.path.dirname(path)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)

    db.init(path)
    db.connect()
    db.create_tables([Positioner, Goto])

    db.models = {'Positioner': Positioner,
                 'Goto': Goto}

    return db
