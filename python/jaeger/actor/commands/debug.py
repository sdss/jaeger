#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-22
# @Filename: debug.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import click

from clu.parser import pass_args

from jaeger import __version__, config

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
        return command.finish()

    return


@jaeger_parser.command('info')
def info_(command, fps):
    """Reports information about the system."""

    command.info({'version': __version__,
                  'config_file': config.__CONFIG_FILE__ or 'NA'},
                 concatenate=False)

    return command.finish()
