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

from jaeger.ieb import IEB, Chiller
from jaeger.utils.helpers import BaseBot


__all__ = ["ChillerBot"]


class ChillerBot(BaseBot):
    """Manages the FPS chiller."""

    async def _loop(self):
        """Sets the chiller set point temperature."""

        last_changed: float | None = None
        last_setpoint: float | None = None
        failed: bool = False

        chiller = Chiller.create()

        while True:
            # Keep this inside the loop to allow for IEB and chiller reconnects.
            if isinstance(self.ieb, IEB) and self.ieb.disabled is False:

                dev_name = "TEMPERATURE_USER_SETPOINT"
                dev = chiller.get_device(dev_name)

                # Try up to 10 times since sometimes setting the temperature fails.
                for _ in range(10):
                    failed = False

                    try:
                        ambient_temp = (await self.ieb.read_device("T3"))[0]
                        rh = (await self.ieb.read_device("RH3"))[0]

                        # Dewpoint temperature.
                        t_d = ambient_temp - (100 - rh) / 5.0

                        if last_setpoint is None:
                            last_setpoint = (await dev.read())[0]

                        current_setpoint = (await dev.read())[0]

                        if abs(last_setpoint - current_setpoint) > 1.0:
                            # First we check if the set point has changed. If it has
                            # this usually means a power failure and we want to
                            # reset the set point immediately.

                            self.notify(
                                "Chiller set-point has changed. "
                                "Maybe the chiller power cycled."
                            )
                            await dev.write(int(last_setpoint * 10))
                            last_changed = time()
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

                            delta_temp = abs(last_setpoint - new_temp)
                            now = time()

                            if last_changed is None or delta_temp > 0.1:
                                await dev.write(int(new_temp * 10))
                                self.notify(
                                    f"Setting chiller to {round(new_temp, 1)} C",
                                    level=logging.DEBUG,
                                )
                                last_setpoint = new_temp
                                last_changed = now

                            break

                    except Exception:
                        failed = True
                        await asyncio.sleep(2)
                        continue

                if failed is True:
                    self.notify(
                        "Failed setting chiller temperature.",
                        level=logging.ERROR,
                    )

            await asyncio.sleep(60)
