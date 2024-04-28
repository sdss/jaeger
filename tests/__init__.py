#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-26
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest

from sdssdb.peewee.sdss5db import database


def check_database():
    """Checks the database connection and skips a test if not connected."""

    if not database.connected:
        pytest.skip("Database not available.")

    if database.dbname != "sdss5db_jaeger_test":
        pytest.skip("Not connected to the test database.")
