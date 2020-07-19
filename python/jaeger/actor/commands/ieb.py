#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import click

from clu.parser import pass_args

from . import jaeger_parser


@jaeger_parser.group()
@pass_args()
def ieb(command, fps):
    """Manages the IEB."""

    ieb = fps.ieb
    if not ieb or ieb.disabled:
        return command.fail(text='ieb not connected.')

    return


@ieb.command()
async def status(command, fps):
    """Outputs the status of the devices."""

    ieb = fps.ieb

    categories = set()
    for module in ieb.modules.values():
        new_categories = set(list(dev.category for dev in module.devices.values()
                                  if dev.category is not None))
        categories = categories.union(new_categories)

    for category in categories:
        data = await ieb.read_category(category)
        measured = []
        for key, value in data.items():
            dev_name = ieb.get_device(key).name
            meas, units = value
            meas = round(meas, 3) if not isinstance(meas, str) else meas
            if meas == 'closed':
                meas = 'on'
            elif meas == 'open':
                meas = 'off'
            value_unit = f'{meas}' if not units else f'{meas} {units}'
            measured.append(f'{dev_name}={value_unit}')
        command.write('i', message='; '.join(measured))

    return command.finish()


@ieb.command()
@click.argument('DEVICE', type=str)
@click.option('--on/--off', default=None,
              help='the value of the device. If not provided, '
                   'switches the current status.')
@click.option('--cycle', is_flag=True, help='power cycles a relay. '
                                            'The final status is on.')
async def switch(command, fps, device, on, cycle):
    """Switches the status of an on/off device."""

    ieb = fps.ieb

    if cycle:
        on = False

    try:
        device_obj = ieb.get_device(device)
        dev_name = device_obj.name
    except ValueError:
        return command.fail(text=f'cannot find device {device!r}.')

    if device_obj.module.mode != 'output':
        return command.fail(text=f'device {dev_name!r} is not output.')

    if on is None:  # The --on/--off was not passed
        current_status = (await device_obj.read())[0]
        if current_status == 'closed':
            on = False
        elif current_status == 'open':
            on = True
        else:
            return command.fail(text=f'invalid status for device {dev_name!r}: '
                                     f'{current_status!r}.')

    try:
        if on is True:
            await device_obj.close()
        elif on is False:
            await device_obj.open()
    except Exception:
        return command.fail(text=f'failed to set status of device {dev_name!r}.')

    if cycle:
        command.write('d', text='waiting 1 second before powering up.')
        await asyncio.sleep(1)
        try:
            await device_obj.close()
        except Exception:
            return command.fail(text=f'failed to power device {dev_name!r} back on.')

    status = 'on' if (await device_obj.read())[0] == 'closed' else 'off'

    return command.finish(text=f'device {dev_name!r} is now {status!r}.')
