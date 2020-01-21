#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: wago.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import click

from clu.parser import pass_args
from . import jaeger_parser


@jaeger_parser.group()
@pass_args()
def wago(command, fps):
    """Manages the WAGO PLCs."""

    wago = fps.wago
    if not wago or not wago.connected:
        return command.fail(text='WAGO not connected.')
        raise click.Abort()

    return


@wago.command()
async def status(command, fps):
    """Outputs the status of the PLCs."""

    wago = fps.wago

    for category in wago.list_categories():
        measured = await wago.read_category(category)
        command.write('i', message=measured)

    return command.finish()


@wago.command()
@click.argument('PLC', type=str)
@click.option('--on/--off', default=None,
              help='the value of the PLC. If not provided, '
                   'switches the current status.')
@click.option('--cycle', is_flag=True, help='power cycles a relay. '
                                            'The final status is on.')
async def switch(command, fps, plc, on, cycle):
    """Switches the status of an on/off PLC."""

    wago = fps.wago

    if cycle:
        on = False

    try:
        plc_obj = wago.get_plc(plc)
    except ValueError:
        return command.fail(text=f'cannot find PLC {plc!r}.')

    if plc_obj.module.mode != 'output':
        return command.fail(text=f'PLC {plc_obj.name!r} is not writeable')

    if on is None:  # The --on/--off was not passed
        current_status = await plc_obj.read(convert=True)
        if current_status == 'on':
            on = False
        elif current_status == 'off':
            on = True
        else:
            return command.fail(text=f'the current status of PLC {plc_obj.name} '
                                     'is not on or off.')

    try:
        if on is True:
            await wago.turn_on(plc_obj.name)
        elif on is False:
            await wago.turn_off(plc_obj.name)
    except Exception:
        return command.fail(text=f'failed to set status of PLC {plc_obj.name}.')

    if cycle:
        command.write('d', text='waiting 1 second before powering up.')
        await asyncio.sleep(1)
        try:
            await wago.turn_on(plc_obj.name)
        except Exception:
            return command.fail(text=f'failed to power PLC {plc_obj.name} back on.')

    status = await plc_obj.read(convert=True)

    return command.finish(text=f'PLC {plc_obj.name!r} is now {status!r}.')
