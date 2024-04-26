#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-05-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import math
import os
import warnings

from typing import Any, Dict

from drift import Drift, DriftError

from jaeger import config
from jaeger.exceptions import JaegerUserWarning


__all__ = ["IEB", "FVC_IEB", "_get_category_data"]


async def _get_category_data(command, category) -> list:
    """Returns data about a device category."""

    ieb = command.actor.fps.ieb
    schema = command.actor.model.schema

    items = schema["properties"][category]["items"]
    measured = []

    async with ieb:
        for item in items:
            name = item["title"]
            type_ = item["type"]
            device = ieb.get_device(name)
            value = (await device.read(connect=False))[0]
            if type_ == "boolean" and device.__type__ == "relay":
                value = True if value == "closed" else False
            elif type_ == "integer":
                value = int(value)
            elif type_ == "number":
                if "multipleOf" in item:
                    precision = int(-math.log10(item["multipleOf"]))
                else:
                    precision = 3
                value = round(value, precision)
            measured.append(value)

    return measured


class IEB(Drift):
    """Thing wrapper around a :class:`~drift.drift.Drift` class.

    Allows additional features such as disabling the interface.

    """

    MAX_RETRIES: int = 5

    def __init__(self, *args, **kwargs):
        self.disabled = False

        super().__init__(*args, **kwargs)

        self._categories = None
        self._n_failures: int = 0

    @classmethod
    def create(cls, path=None):
        """Creates an `.IEB` instance with the default configuration."""

        default_ieb_path = path or config["files"]["ieb_config"]

        default_ieb_path = os.path.expanduser(os.path.expandvars(default_ieb_path))
        if not os.path.isabs(default_ieb_path):
            default_ieb_path = os.path.join(os.path.dirname(__file__), default_ieb_path)

        return cls.from_config(default_ieb_path)

    def get_categories(self):
        """Returns a list of categories."""

        if self._categories is None:
            categories = [
                device.category
                for module in self.modules
                for device in self.modules[module].devices.values()
            ]
            self._categories = set(categories)
            self._categories.discard(None)

        return self._categories

    async def __aenter__(self):
        if self.disabled:
            raise DriftError("IEB is disabled.")

        n_retries = 0
        while True:
            try:
                await Drift.__aenter__(self)
                break
            except DriftError:
                n_retries += 1
                if n_retries >= 5:
                    raise DriftError("Failed connecting to the IEB.")

    async def __aexit__(self, *args):
        await Drift.__aexit__(self, *args)

    def enable(self):
        """Re-enables the IEB instance."""

        self._n_failures = 0
        self.disabled = False

    async def get_status(self) -> Dict[str, Any]:
        """Returns the status of the IEB components."""

        status = {}
        for category in self.get_categories():
            data = await self.read_category(category)
            for device in data:
                if self.get_device(device).__type__ == "relay":
                    value = False if data[device][0] == "open" else True
                else:
                    value = data[device][0]
                status[device] = value

        return status


class FVC_IEB(IEB):
    """Connects to the FVC IEB."""

    @classmethod
    def create(cls, path=None):
        """Creates an `.FVC` instance with the default configuration."""

        default_ieb_path = config["fvc"]["config"]

        return super().create(default_ieb_path)


class Chiller(IEB):
    """Connects to the chiller Modbus PLC."""

    @classmethod
    def create(cls, path=None):
        """Creates a `.Chiller` instance with the default configuration."""

        config_file = config["chiller"].get("config", None)
        if config_file is None:
            warnings.warn("Chiller configuration missing.", JaegerUserWarning)
            return None

        return super().create(config_file)
