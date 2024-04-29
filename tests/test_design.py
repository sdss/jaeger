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

import numpy
import polars
import pytest

from sdssdb.peewee.sdss5db import opsdb
from sdsstools import yanny

from jaeger.target.design import Design
from jaeger.target.tools import configuration_to_dataframe
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


async def test_configuration_compare_confSummary(tmp_path: pathlib.Path):
    check_database()

    design = Design(21636, epoch=2460427)

    design.configuration.write_to_database()
    confSummary_path = tmp_path / "confSummary.par"
    design.configuration.write_summary(confSummary_path)

    yanny_new = yanny(str(confSummary_path))
    yanny_test = yanny(str(pathlib.Path(__file__).parent / "data/confSummary-test.par"))

    assert yanny_new["epoch"] == yanny_test["epoch"]

    fmap_new = yanny_new["FIBERMAP"]
    fmap_test = yanny_test["FIBERMAP"]

    numpy.testing.assert_allclose(fmap_new["alpha"], fmap_test["alpha"], atol=1e-4)
    numpy.testing.assert_allclose(fmap_new["beta"], fmap_test["beta"], atol=1e-4)

    numpy.testing.assert_allclose(fmap_new["ra"], fmap_test["ra"], atol=1e-4)
    numpy.testing.assert_allclose(fmap_new["dec"], fmap_test["dec"], atol=1e-4)

    numpy.testing.assert_allclose(fmap_new["racat"], fmap_test["racat"], atol=1e-4)
    numpy.testing.assert_allclose(fmap_new["deccat"], fmap_test["deccat"], atol=1e-4)

    numpy.testing.assert_allclose(fmap_new["xFocal"], fmap_test["xFocal"], atol=1e-4)
    numpy.testing.assert_allclose(fmap_new["yFocal"], fmap_test["yFocal"], atol=1e-4)


async def test_configuration_to_dataframe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
):
    check_database()

    design = Design(21637)
    design.configuration.write_to_database()

    configuration_id = design.configuration.configuration_id

    monkeypatch.setenv("SDSSCORE_TEST_DIR", str(tmp_path))

    df = configuration_to_dataframe(design.configuration, write=True)

    assert isinstance(df, polars.DataFrame)
    assert df.height == 1500

    file_path = (
        tmp_path
        / "apo"
        / "summary_files/000XXX/0000XX"
        / f"configuration-{configuration_id}.parquet"
    )
    assert file_path.exists()


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
