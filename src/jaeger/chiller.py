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

        if self.temperature is False or self.temperature is None:
            return

        if self.temperature == "auto":
            if not isinstance(self.ieb, IEB) or self.ieb.disabled is True:
                return

        failed: bool = False

        # Try up to 10 times since sometimes setting the temperature fails.
        for _ in range(10):
            failed = False

            try:
                current_setpoint = (await dev.read())[0]

                # If we are maintaining a fixed temperature, check if we
                # need to reset the set point and exit.
                if self.temperature is not True and self.temperature != "auto":
                    if abs(current_setpoint - self.temperature) > 0.1:
                        await dev.write(int(self.temperature * 10))
                        self.notify(
                            f"Setting chiller to {self.temperature} C",
                            logging.DEBUG,
                        )
                    break

                assert isinstance(self.ieb, IEB) and self.ieb.disabled is False

                ambient_temp = (await self.ieb.read_device("T3"))[0]
                rh = (await self.ieb.read_device("RH3"))[0]

                # Dewpoint temperature.
                t_d = ambient_temp - (100 - rh) / 5.0

                # What follows is if we are setting the set point
                # based on the ambient temperature.

                # Calculate the new set point temperature and write it
                # to the device if it's different than the current one.

                # New set point is two degC below ambient clipped to 1 degC
                # and above the dew point depression region.
                new_temp = ambient_temp - 2
                if new_temp <= 1:
                    new_temp = 1
                if new_temp < (t_d + 3):
                    new_temp = t_d + 3

                # Round to closest 0.5
                new_temp = round(new_temp * 2) / 2.0
                delta_temp = abs(current_setpoint - new_temp)
                if delta_temp > 0.1:
                    await dev.write(int(new_temp * 10))
                    self.notify(f"Setting chiller to {new_temp} C", logging.DEBUG)

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
                        self.notify(
                            f"Setting chiller flow to {self.flow:.1f} gpm.",
                            level=logging.DEBUG,
                        )

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
