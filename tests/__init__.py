#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-26
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest

from fps_calibrations import __version__ as fps_calibrations_version
from sdssdb.peewee.sdss5db import database


FPS_CALIBRATIONS_VERSION = "2024.04.01"


def check_database():
    """Checks the database connection and skips a test if not connected."""

    if not database.connected:
        pytest.skip("Database not available.")

    if database.dbname != "sdss5db_jaeger_test":
        pytest.skip("Not connected to the test database.")


def check_fps_calibrations_version():
    if fps_calibrations_version != FPS_CALIBRATIONS_VERSION:
        raise ValueError(
            "fps_calibrations version does not match the expected "
            "version for testing. The required version "
            f"is {FPS_CALIBRATIONS_VERSION!r}."
        )
