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

    actor = command.actor
    assert actor

    ieb = actor.fvc_ieb

    try:
        status = {}
        categories = ieb.get_categories()
        for category in sorted(categories):
            cat_data = await ieb.read_category(category)
            status[category] = []
            for cd in cat_data:
                value = cat_data[cd][0]
                if value == "closed":
                    value = True
                elif value == "open":
                    value = False
                else:
                    value = round(value, 1)
                status[category].append(value)

        command.info(status)

    except DriftError:
        command.warning(text="FVC IEB is unavailable.")

    command.finish()
