#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-14
# @Filename: test_fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pathlib

import pandas
import pytest

from jaeger.fvc import FVC
from jaeger.positioner import Positioner


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


def test_check_data(test_data):
    posangles, measured = test_data

    assert len(posangles) == 500
    assert len(measured) == 500


async def test_fvc():
    fvc = FVC("APO")
    assert fvc.command is None
