#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-22
# @Filename: debug.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from jaeger import __version__, config

from . import jaeger_parser


__all__ = ["debug"]


@jaeger_parser.group(invoke_without_command=True)
def debug():
    """Debug and engineering tools."""

    return


@debug.command("info")
def info_(command, fps):
    """Reports information about the system."""

    command.info(
        {
            "version": __version__,
            "config_file": config._CONFIG_FILE or "internal",
        },
        concatenate=False,
    )

    return command.finish()
