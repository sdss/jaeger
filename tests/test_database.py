#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-27
# @Filename: test_database.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

from . import check_database


if TYPE_CHECKING:
    from sdssdb.connection import PeeweeDatabaseConnection


def test_database(database: PeeweeDatabaseConnection):
    check_database()

    assert database.connected
    assert database.dbname == "sdss5db_jaeger_test"
