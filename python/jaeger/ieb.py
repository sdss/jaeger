#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-05-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import os

from typing import Any, Dict

from drift import Drift, DriftError

from jaeger import config


__all__ = ["IEB", "FVC"]


class IEB(Drift):
    """Thing wrapper around a :class:`~drift.drift.Drift` class.

    Allows additional features such as disabling the interface.

    """

    def __init__(self, *args, **kwargs):

        self.disabled = False

        super().__init__(*args, **kwargs)

        self._categories = None

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

        try:
            await Drift.__aenter__(self)
        except DriftError:
            self.disabled = True
            raise DriftError("Failed connecting to the IEB. Disabling it.")

    async def __aexit__(self, *args):

        await Drift.__aexit__(self, *args)

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


class FVC(IEB):
    """Connects to the FVC IEB."""

    @classmethod
    def create(cls, path=None):
        """Creates an `.FVC` instance with the default configuration."""

        default_ieb_path = config["files"]["fvc_config"]

        return super().create(default_ieb_path)
