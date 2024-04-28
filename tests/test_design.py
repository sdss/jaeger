#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-27
# @Filename: test_design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import polars

from jaeger.target.design import Design
from jaeger.testing import MockFPS

from . import check_database


async def test_create_design():
    check_database()

    design = Design(21636, epoch=2460427)

    assert design.configuration.fibre_data.height == 1500

    assigned = design.configuration.fibre_data.filter(polars.col.assigned)
    assert assigned.height == 499


async def test_configuration_get_paths(mock_fps: MockFPS):
    check_database()

    design = Design(21636, fps=mock_fps, epoch=2460427)

    assert design.design_id == 21636

    from_destination = await design.configuration.get_paths()
    assert isinstance(from_destination, dict)

    assigned = design.configuration.fibre_data.filter(polars.col.assigned)
    assert assigned.height == 499

    on_target = design.configuration.fibre_data.filter(polars.col.on_target)
    assert on_target.height < 499

    reassigned = design.configuration.fibre_data.filter(polars.col.reassigned)
    assert reassigned.height > 0
