#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-11
# @Filename: wago.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

from pymodbus.client.asynchronous.asyncio import AsyncioModbusTcpClient

from .. import config as jaeger_config
from . import utils as wago_utils


#: Mapping between PLC categories and conversion functions.
CONVERT_PLC_VALUE = {
    'rtd': wago_utils.convert_rtd,
    'ee_temp': wago_utils.convert_ee_temp,
    'ee_rh': wago_utils.convert_ee_rh
}


class PLC(object):
    """An object representing a PLC.

    Parameters
    ----------
    module : .Module
        The `.Module` to which this PLC is connected to.
    name : str
        The name of the PLC. It is treated as case-insensitive.
    channel : int
        The channel of the PLC inside the module. The first channel must be 1
        so that the full address of the PLC is
        ``module_address + channel - 40001``.
    category : str
        The type of PLC (e.g., ``temperature``, ``humidity``, ``do``).
    description : str
        A description of the purpose of the PCL.
    units : str
        The units of the values returned.
    coil : bool
        Whether to use coils to talk to this PLC. If `False`, uses registers.
    on_value : bool
        Only for PLCs with ``category='do'``, the value of the coil that
        closes the relay and powers the device.

    """

    def __init__(self, module, name, channel, category='', description='',
                 units='', coil=False, on_value=None):

        self.module = module
        self.name = name
        self.channel = channel
        self.category = category
        self.description = description
        self.units = units
        self.coil = coil

        if self.category == 'do':
            assert on_value is not None, \
                'on_value must be specified for PLCs of type do.'

        self.on_value = on_value

    def __repr__(self):
        return (f'<PLC {self.name} (address={self.address}, '
                f'category={self.category!r}, coil={self.coil})>')

    @property
    def address(self):
        """Returns the full address of this PLC."""

        return self.module.address + self.channel - 40002

    @property
    def client(self):
        """Returns the ``pymodbus`` client."""

        return self.module.wago.client

    async def read(self, convert=False):
        """Reads the value of the coil or register.

        If ``convert=True`` and the ``category`` of the PLC matches one of the
        mappings in `.CONVERT_PLC_VALUE`, the value returned is the one
        obtained after applying the conversion function to the raw register
        value. Otherwise returns the raw value.

        """

        assert self.client.connected, 'client is not connected'

        if self.coil:
            resp = await self.client.protocol.read_coils(self.address, count=1)
        else:
            resp = await self.client.protocol.read_input_registers(self.address, count=1)

        assert resp.function_code < 0x80, f'invalid response for PLC {self.name!r}.'

        value = resp.registers[0] if not self.coil else resp.bits[0]

        if convert:
            if self.category == 'do':
                return 'on' if resp.bits[0] == self.on_value else 'off'
            if self.category in CONVERT_PLC_VALUE:
                return CONVERT_PLC_VALUE[self.category](value)

        return value

    async def write(self, value):
        """Writes values to a coil or register."""

        assert self.client.connected, 'client is not connected'
        assert self.module.mode == 'output', \
            'writing is not allowed to this input module.'

        if self.coil:
            resp = await self.client.protocol.write_coil(self.address, value)
        else:
            resp = await self.client.protocol.write_register(self.address, value)

        assert resp.function_code < 0x80, f'invalid response for PLC {self.name!r}.'

        return True


class Module(object):
    """A Modbus module with some PLCs connected."""

    def __init__(self, wago, address, name='', device='', mode='input',
                 channels=4, comment=''):

        self.address = address
        self.name = name
        self.device = device
        self.mode = mode
        self.channels = channels
        self.comment = comment
        self.plcs = {}

        self.wago = wago

        assert self.mode in ['input', 'output'], f'invalid mode {mode}.'

    def __repr__(self):
        return (f'<Module {self.name} (mode={self.mode!r}, '
                f'channels={self.channels}, plcs={len(self.plcs)})>')

    def add_plc(self, name, channel, **kwargs):
        """Adds a PLC

        Parameters
        ----------
        name : str
            The name of the PLC. It is treated as case-insensitive.
        channel : int
            The channel of the PLC in the module (relative to the
            module address).
        kwargs : dict
            Other parameters to pass to `.PLC`.

        """

        for module in self.wago.modules:
            if name in [plc.lower() for plc in self.wago.modules[module].plcs]:
                raise ValueError(f'PLC {name!r} is already '
                                 f'connected to module {module!r}.')

        self.plcs[name] = PLC(self, name, channel, **kwargs)

    def remove_plc(self, name):
        """Removes a PLC.

        Parameters
        ----------
        name : str
            The name of the PLC to remove.

        """

        for plc in self.plcs:
            if plc.lower() == name.lower():
                return self.plcs.pop(plc)

        raise ValueError(f'{name} is not a valid PLC name.')


class WAGO(object):
    """Controls a WAGO PLC using a Modbus interface.

    Parameters
    ----------
    address : str
        The IP of the WAGO PLC server.
    loop
        The event loop to use.

    """

    def __init__(self, address, loop=None):

        self.address = address
        self.client = AsyncioModbusTcpClient(address, loop=loop)
        self.loop = self.client.loop

        #: Delay to way before considering that a digital output has been written.
        if 'WAGO' in jaeger_config:
            self.DELAY = jaeger_config['WAGO'].get('DO_delay', 0.5)
        else:
            self.DELAY = 0.5

        self.modules = {}

    def __repr__(self):
        return f'<WAGO (address={self.address}, modules={len(self.modules)})>'

    async def connect(self):
        """Initialises the connection to the WAGO server."""

        try:
            await asyncio.wait_for(self.client.connect(), timeout=5)
        except asyncio.TimeoutError:
            raise RuntimeError(f'failed connecting to WAGO on address {self.address}.')

        if not self.client.connected:
            raise RuntimeError(f'failed connecting to WAGO on address {self.address}.')

        return True

    @property
    def connected(self):
        """Returns `True` if the client is connected."""

        return self.client.connected

    def add_module(self, name, **params):
        """Adds a new module.

        Parameters
        ----------
        name : str
            The name of the module.
        params : dict
            Arguments to be passed to `.Module` for initialisation (including
            the ``address`` as a keyword).

        """

        self.modules[name] = Module(self, name=name, **params)

    def get_plc(self, name):
        """Gets the `.PLC` instance that matches ``name``."""

        for module in self.modules.values():
            for plc in module.plcs.values():
                if plc.name.lower() == name.lower():
                    return plc

        raise ValueError(f'PLC {name} is not connected.')

    def list_categories(self):
        """Returns a list of available, non-null PLC categories."""

        categories = []

        for module in self.modules.values():
            categories += [plc.category for plc in module.plcs.values()]

        categories = sorted(list(set(categories)))
        if '' in categories:
            categories.remove('')

        return categories

    async def read_plc(self, name, convert=True):
        """Reads a PLC.

        Parameters
        ----------
        convert : bool
            If possible, convert the value to real units.

        """

        return await self.get_plc(name).read(convert=True)

    async def read_category(self, category, convert=True):
        """Reads all the PLCs of a given category.

        Parameters
        ----------
        category : str
            The category to match.
        convert : bool
            If possible, convert the value to real units.

        Returns
        -------
        dict
            A dictionary of PLC names and read values.

        """

        values = {}

        for module in self.modules:
            for plc in self.modules[module].plcs.values():
                if plc.category.lower() == category:
                    values[plc.name] = await plc.read(convert=convert)

        return values

    async def write_plc(self, name, value):
        """Writes to a PLC."""

        plc = self.get_plc(name)

        initial_value = await plc.read(convert=False)
        if initial_value == value:
            return True

        if not await plc.write(value):
            raise RuntimeError('failed writing value to PLC.')

        # Wait a delay to allow the relay to change.
        if plc.category.lower() == 'do':
            await asyncio.sleep(self.DELAY)

        # Check that the value has changed
        assert await plc.read(convert=False) == value, \
            'failed changing value of PLC coil/register.'

        return True

    async def turn_on(self, name):
        """Turns a relay on (closed)."""

        relay = self.get_plc(name)
        return await self. write_plc(name, relay.on_value)

    async def turn_off(self, name):
        """Turns a relay off (open)."""

        relay = self.get_plc(name)
        return await self. write_plc(name, not relay.on_value)

    @classmethod
    def from_config(cls, config=None):
        """Loads a WAGO from the configuration or from a dictionary.

        Parameters
        ----------
        config : dict
            A dictionary with an ``address`` key for the WAGO and a key
            ``modules`` with a list of modules to be created (the format is
            similar to the signature of `.Module`). If not provided, the
            ``WAGO`` section from the jaeger configuration will be used.

        """

        if not isinstance(config, dict):
            assert 'WAGO' in jaeger_config, \
                'configuration does not include a WAGO section.'
            config = jaeger_config['WAGO']

        # Make sure this is a copy because we'll pop some values.
        config = config.copy()

        # Does a sanity check to make sure there are no duplicate PLC names.
        all_plc_names = [name.lower() for module in config['modules']
                         for name in config['modules'][module]['plcs']]

        if len(all_plc_names) != len(set(all_plc_names)):
            raise ValueError('there are duplicate PLC names in the '
                             'configuration. PLCs must have unique names.')

        new_wago = cls(config['address'])

        for module in config['modules']:

            module_dict = config['modules'][module]
            plcs = module_dict.pop('plcs', {})

            new_wago.add_module(module, **module_dict)

            for plc in plcs:
                new_wago.modules[module].add_plc(plc, **plcs[plc])

        return new_wago
