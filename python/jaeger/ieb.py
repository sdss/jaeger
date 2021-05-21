#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-05-12
# @Filename: ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import warnings

from typing import Any, Dict

from drift import Drift, DriftError

from jaeger.exceptions import JaegerUserWarning


__all__ = ["IEB"]


class IEB(Drift):
    """Thing wrapper around a :class:`~drift.drift.Drift` class.

    Allows additional features such as disabling the interface.

    """

    def __init__(self, *args, **kwargs):

        self.disabled = False

        super().__init__(*args, **kwargs)

        self._categories = None

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
            warnings.warn(
                "Failed connecting to the IEB. Disabling it.",
                JaegerUserWarning,
            )

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
