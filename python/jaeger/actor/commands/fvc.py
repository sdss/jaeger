#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-27
# @Filename: fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from typing import TYPE_CHECKING

from drift import DriftError

from . import jaeger_parser


if TYPE_CHECKING:
    from clu import Command

    from jaeger import FPS

    from ..actor import JaegerActor


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
        async with ieb:
            categories = ieb.get_categories()
            for category in sorted(categories):
                cat_data = await ieb.read_category(category)
                status[category] = [cat_data[cd][0] for cd in cat_data]

        command.info(status)
    except DriftError:
        command.warning(text="FVC IEB is unavailable.")

    command.finish()
