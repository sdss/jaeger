#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: wago.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import click

from clu.parser import pass_args
from . import jaeger_parser


@jaeger_parser.group()
@pass_args()
def wago(command, fps):

    wago = fps.wago
    if wago is None:
        command.failed(text='WAGO not set up.')
        raise click.Abort()

    return


@wago.command()
async def status(command, fps):
    """Outputs the status of the PLCs."""

    wago = fps.wago
    if not wago:
        command.failed(text='WAGO not set up.')
        return

    for category in wago.list_categories():
        measured = await wago.read_category(category)
        command.write('i', message=measured)

    command.done()


@wago.command()
@click.argument('PLC', type=str)
@click.option('--on/--off', default=None,
              help='the value of the PLC. If not provided, '
                   'switches the current status.')
async def switch(command, fps, plc, on):
    """Switches the status of an on/off PLC."""

    desired_status = None

    wago = fps.wago
    if wago is None:
        command.failed(text='WAGO not set up.')
        return

    try:
        plc_obj = wago.get_plc(plc)
    except ValueError:
        command.failed(text=f'cannot find PLC {plc!r}.')
        return

    if plc_obj.module.mode != 'output':
        command.failed(text=f'PLC {plc_obj.name!r} is not writeable')

    if on is None:  # The --on/--off was not passed
        current_status = await plc_obj.read(convert=True)
        if current_status == 'on':
            on = False
        elif current_status == 'off':
            on = True
        else:
            command.failed(text=f'the current status of PLC {plc_obj.name} '
                                'is not on or off.')
            return

    if on is True:
        await wago.turn_on(plc_obj.name)
        desired_status = 'on'
    elif on is False:
        await wago.turn_off(plc_obj.name)
        desired_status = 'off'

    # Do a sanity check
    status = await plc_obj.read(convert=True)

    if not status == desired_status:
        command.failed(text=f'failed to set status of PLC {plc_obj.name}.')
        return

    command.done(text=f'PLC {plc_obj.name!r} is now {desired_status!r}.')

    return
