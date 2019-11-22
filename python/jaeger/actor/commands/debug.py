#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-22
# @Filename: debug.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import click

from clu.parser import pass_args
from . import jaeger_parser


@jaeger_parser.group(invoke_without_command=True)
@click.option('--danger/--no-danger', default=None, help='Use engineering mode (unsafe)?')
@pass_args()
@click.pass_context
def debug(ctx, command, fps, danger):
    """Debug and engineering tools."""

    fps.engineering_mode = danger

    if fps.engineering_mode:
        command.warning('you are now in engineering mode.')
    else:
        command.info('you are not in engineering mode.')

    if ctx.invoked_subcommand is None:
        return command.done()

    return
