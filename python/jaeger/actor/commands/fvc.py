#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-27
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from clu import Command
from drift import DriftError

from jaeger import FPS
from jaeger.ieb import FVC

from ..actor import JaegerActor
from . import jaeger_parser


__all__ = ["fvc"]


@jaeger_parser.group()
def fvc():
    """Commands to command the FVC."""

    pass


@fvc.command()
async def status(command: Command[JaegerActor], fps: FPS):
    """Reports the status of the FVC."""

    fvc_ieb = FVC.create()

    try:
        status = {}
        categories = fvc_ieb.get_categories()
        for category in sorted(categories):
            cat_data = await fvc_ieb.read_category(category)
            status[category] = []
            for cd in cat_data:
                value = cat_data[cd][0]
                if value == "closed":
                    value = True
                elif value == "open":
                    value = False
                else:
                    value = round(value, 1)
                status["fvc_" + category].append(value)

        command.finish(status)

    except DriftError:
        return command.fail(error="FVC IEB is unavailable or failed to connect.")
