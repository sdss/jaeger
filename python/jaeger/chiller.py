#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-02-02
# @Filename: chiller.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import logging
from time import time

from typing import TYPE_CHECKING

from jaeger import config
from jaeger.ieb import IEB, Chiller
from jaeger.utils.helpers import BaseBot


if TYPE_CHECKING:
    from jaeger.fps import FPS


__all__ = ["ChillerBot"]


class ChillerBot(BaseBot):
    """Manages the FPS chiller."""

    def __init__(self, fps: FPS):

        self.chiller_config = config.get("chiller", {})
        self.temperature: bool | float = self.chiller_config.get("temperature", False)
        self.flow: bool | float = self.chiller_config.get("flow", False)

        self.temperature_last_changed: float | None = None
        self.temperature_last_setpoint: float | None = None

        super().__init__(fps)

    async def _loop(self):
        """Sets the chiller set point temperature."""

        chiller = Chiller.create()
        assert chiller is not None

        while True:

            await self._set_temperature(chiller)
            await self._set_flow(chiller)

            await asyncio.sleep(60)

    async def _set_temperature(self, chiller: Chiller):
        """Sets the temperature set point."""

        dev_name = "TEMPERATURE_USER_SETPOINT"
        dev = chiller.get_device(dev_name)

        if not isinstance(self.ieb, IEB) or self.ieb.disabled is True:
            return

        if self.temperature is False or self.temperature is None:
            return

        failed: bool = False

        # Try up to 10 times since sometimes setting the temperature fails.
        for _ in range(10):
            failed = False

            try:
                ambient_temp = (await self.ieb.read_device("T3"))[0]
                rh = (await self.ieb.read_device("RH3"))[0]

                # Dewpoint temperature.
                t_d = ambient_temp - (100 - rh) / 5.0

                if self.temperature_last_setpoint is None:
                    self.temperature_last_setpoint = (await dev.read())[0]

                current_setpoint = (await dev.read())[0]

                # If we are maintaining a fixed temperature, check if we
                # need to reset the set point and exit.
                if isinstance(self.temperature, (float, int)):
                    if abs(current_setpoint - self.temperature) > 0.1:
                        await dev.write(int(self.temperature * 10))
                        self.temperature_last_changed = time()
                    break

                # What follows is if we are setting the set point
                # based on the ambient temperature.
                if abs(self.temperature_last_setpoint - current_setpoint) > 1.0:
                    # First we check if the set point has changed. If it has
                    # this usually means a power failure and we want to
                    # reset the set point immediately.

                    self.notify("Chiller set-point has changed.")
                    await dev.write(int(self.temperature_last_setpoint * 10))
                    self.temperature_last_changed = time()
                    break

                else:
                    # Calculate the new set point temperature and write it
                    # to the device if it's different than the current one.

                    # New set point is one below ambient clipped to 0 degC
                    # and above the dew point depression region.
                    new_temp = ambient_temp - 1
                    if new_temp <= 1:
                        new_temp = 1
                    if new_temp < (t_d + 3):
                        new_temp = t_d + 3

                    # Round to closest 0.5
                    new_temp = round(new_temp * 2) / 2.0

                    delta_temp = abs(self.temperature_last_setpoint - new_temp)
                    now = time()

                    if self.temperature_last_changed is None or delta_temp > 0.1:
                        await dev.write(int(new_temp * 10))
                        self.notify(
                            f"Setting chiller to {round(new_temp, 1)} C",
                            level=logging.DEBUG,
                        )
                        self.temperature_last_setpoint = new_temp
                        self.temperature_last_changed = now

                    break

            except Exception:
                failed = True
                await asyncio.sleep(2)
                continue

        if failed is True:
            self.notify("Failed setting chiller temperature.", level=logging.ERROR)

    async def _set_flow(self, chiller: Chiller):
        """Sets the flow set point."""

        dev_name = "FLOW_USER_SETPOINT"
        dev = chiller.get_device(dev_name)

        if not isinstance(self.ieb, IEB) or self.ieb.disabled is True:
            return

        if self.flow is True or self.flow == "auto":
            self.flow = self.chiller_config.get("flow", False)

        if self.flow is False or self.flow is None:
            return

        failed: bool = False

        # Try up to 10 times since sometimes setting the flow fails.
        for _ in range(10):
            failed = False

            try:
                current_setpoint = (await dev.read())[0]

                if isinstance(self.flow, (float, int)):
                    if abs(current_setpoint - self.flow) > 0.1:
                        await dev.write(int(self.flow * 10))

                else:
                    self.notify(
                        f"Invalid chiller set point {self.flow!r}.",
                        level=logging.WARNING,
                    )

                break

            except Exception:
                failed = True
                await asyncio.sleep(2)
                continue

        if failed is True:
            self.notify("Failed setting chiller flow.", level=logging.ERROR)
