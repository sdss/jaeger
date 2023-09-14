#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-14
# @Filename: test_fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pathlib

from typing import cast

import numpy
import pandas
import pytest
from jaeger import FPS
from jaeger.fvc import FVC
from jaeger.positioner import Positioner
from jaeger.target import ManualConfiguration

from coordio import calibration


class FakePositioner(Positioner):
    def __ini__(self, pid: int):
        self.positioner_id = pid
        self.disabled = False


@pytest.fixture(scope="module")
def test_data():
    FILE = pathlib.Path(__file__).parent / "data" / "proc-fimg-fvcn-0059.h5"
    hstore = pandas.HDFStore(FILE.as_posix(), mode="r")
    yield hstore.get("posangles"), hstore.get("measured")
    hstore.close()


@pytest.fixture(scope="module")
def configuration(test_data):
    # Ugly hack to add fake positioners to the FPS and prevent get_robot_grid
    # from failing when it checks if all the positioners exist.
    fps = FPS.get_instance()
    fps.clear()
    for pid in calibration.positionerTable.positionerID:
        fps[pid] = FakePositioner(pid)

    posangles, measured = test_data
    pid, alpha, beta = (
        posangles.loc[:, ["positionerID", "cmdAlpha", "cmdBeta"]].to_numpy().T
    )

    pT = calibration.positionerTable.loc["APO"].reset_index().set_index("positionerID")
    hids = pT.loc[pid, "holeID"].tolist()

    data = {
        hids[i]: {"alpha": alpha[i], "beta": beta[i], "fibre_type": "Metrology"}
        for i in range(len(hids))
    }

    mc = ManualConfiguration(data, observatory="APO")

    for offline_pid in measured.loc[measured.offline, "robotID"].tolist():
        mc.assignment_data.fibre_table.loc[offline_pid, "offline"] = 1

    yield mc

    del fps
    FPS._instance = {}


def test_check_data(test_data):
    posangles, measured = test_data

    assert len(posangles) == 500
    assert len(measured) == 500


def test_fvc():
    fvc = FVC("APO")
    assert fvc.command is None


@pytest.mark.xfail()
def test_configuration(configuration: ManualConfiguration):
    ftable = configuration.assignment_data.fibre_table

    assert len(ftable) == 1500
    assert len(ftable[ftable.assigned == 1]) == 500


@pytest.mark.xfail()
def test_process_image(configuration: ManualConfiguration, tmp_path: pathlib.Path):
    fvc = FVC("APO")
    fvc.fps.configuration = configuration

    image = pathlib.Path(__file__).parent / "data/fimg-fvcn-0059.fits"

    proc_hdu, measured, centroids = fvc.process_fvc_image(
        image.as_posix(),
        plot=tmp_path.as_posix(),
    )

    assert isinstance(measured, pandas.DataFrame)

    rms = cast(float, proc_hdu.header["FITRMS"])
    numpy.allclose(rms, 25.03, atol=0.01)

    assert len(list(tmp_path.glob("*.pdf"))) > 0


@pytest.mark.xfail()
def test_calculate_offsets(configuration: ManualConfiguration, test_data):
    posangles, _ = test_data

    fvc = FVC("APO")
    fvc.fps.configuration = configuration

    image = pathlib.Path(__file__).parent / "data/fimg-fvcn-0059.fits"
    _, measured, _ = fvc.process_fvc_image(image.as_posix(), plot=False)

    # Need to remove cases where beta > 180 since the test data used a random
    # configuration with some of those cases.
    measured = measured.loc[measured.beta < 180]

    # Make a mock of the output of FVC.get_positions()
    positions = posangles[["positionerID", "alphaReport", "betaReport"]].to_numpy()
    positions = positions[positions[:, 2] < 180.0]

    new = fvc.calculate_offsets(positions, measured, k=1)

    assert len(new) > 0
