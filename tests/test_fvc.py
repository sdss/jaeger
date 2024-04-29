#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-14
# @Filename: test_fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)


from jaeger.fvc import FVC


async def test_fvc():
    fvc = FVC("APO")
    assert fvc.command is None
