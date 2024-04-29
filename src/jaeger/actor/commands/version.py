#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-12-06
# @Filename: version.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from coordio import __version__ as coordio_version
from kaiju import __version__ as kaiju_version

from jaeger import __version__ as jaeger_version

from . import jaeger_parser


jaeger_parser.commands.pop("version")


@jaeger_parser.command()
async def version(command, fps):
    """Returns the versions used."""

    command.info(version=jaeger_version)
    command.info(kaiju_version=kaiju_version)
    command.info(coordio_version=coordio_version)

    try:
        from fps_calibrations import get_version

        command.info(fps_calibrations_version=get_version())
    except ImportError:
        command.warning("Cannot output fps_calibrations version.")

    return command.finish()
