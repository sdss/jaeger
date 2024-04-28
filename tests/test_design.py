#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-27
# @Filename: test_design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import pathlib

from typing import TYPE_CHECKING

import polars

from sdssdb.peewee.sdss5db import opsdb

from jaeger.target.design import Design
from jaeger.testing import MockFPS

from . import check_database


if TYPE_CHECKING:
    pass


async def test_create_design():
    check_database()

    design = Design(21637)

    assert design.configuration.fibre_data.height == 1500

    assigned = design.configuration.fibre_data.filter(polars.col.assigned)
    assert assigned.height == 499


async def test_configuration_write(tmp_path: pathlib.Path):
    check_database()

    design = Design(21637)

    assert opsdb.Configuration.select().count() == 0
    assert opsdb.AssignmentToFocal.select().count() == 0

    design.configuration.write_to_database()

    assert (
        opsdb.Configuration.select()
        .where(opsdb.Configuration.design_id == 21637)
        .exists()
    )

    assert opsdb.AssignmentToFocal.select().count() == 1500

    confSummary_path = tmp_path / "confSummary.par"
    design.configuration.write_summary(confSummary_path)

    assert confSummary_path.exists()


async def test_configuration_get_paths(mock_fps: MockFPS):
    check_database()

    design = Design(21637, fps=mock_fps)

    assert design.design_id == 21637

    from_destination = await design.configuration.get_paths()
    assert isinstance(from_destination, dict)

    assigned = design.configuration.fibre_data.filter(polars.col.assigned)
    assert assigned.height == 499

    on_target = design.configuration.fibre_data.filter(polars.col.on_target)
    assert on_target.height < 499

    reassigned = design.configuration.fibre_data.filter(polars.col.reassigned)
    assert reassigned.height > 0
