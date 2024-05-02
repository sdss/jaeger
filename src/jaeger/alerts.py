#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-01-26
# @Filename: alerts.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from typing import TYPE_CHECKING, Any

from clu.legacy.tron import TronKey
from drift import Device, Relay

from jaeger import config
from jaeger.ieb import IEB, Chiller
from jaeger.utils.helpers import BaseBot


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = ["AlertsBot"]


class AlertsBot(BaseBot):
    """Monitors values and raises alerts."""

    def __init__(self, fps: FPS):
        super().__init__(fps)

        self.config: dict[str, Any] = config["alerts"]
        self.interval: float = self.config["interval"]

        self.keywords: dict[str, bool] = {}
        self._gfa_alerts: dict[str, bool] = {}

        self.reset()

    def reset(self):
        """Resets alerts and parameters."""

        self.keywords = {
            "alert_gfa_temp_critical": False,
            "alert_gfa_temp_warning": False,
            "alert_ieb_temp_critical": False,
            "alert_ieb_temp_warning": False,
            "alert_robot_temp_critical": False,
            "alert_robot_temp_warning": False,
            "alert_fps_flow": False,
            "alert_dew_point": False,
            "alert_chiller_dew_point": False,
            "alert_chiller_fault": False,
            "alert_fluid_temperature": False,
        }
        self._gfa_alerts = {}

    async def start(self, delay: float | bool = False):
        """Stars the monitoring loop."""

        await self.stop()

        if delay is not False and delay > 0:
            await asyncio.sleep(delay)

        self._task = asyncio.create_task(self._loop())

        if "gfa" in self.config["enabled"]:
            if self.actor and "fliswarm" in self.actor.models:
                self.actor.models["fliswarm"].register_callback(self._check_gfa)
            else:
                self.notify(
                    "Failed starting GFA alert monitoring.",
                    level=logging.ERROR,
                )

    async def stop(self):
        """Stops the monitoring loop."""

        if self._task:
            with suppress(asyncio.CancelledError):
                self._task.cancel()
                await self._task
        if (
            self.actor
            and "fliswarm" in self.actor.models
            and self._check_gfa in self.actor.models["fliswarm"]._callbacks
        ):
            self.actor.models["fliswarm"].remove_callback(self._check_gfa)

    def set_keyword(self, keyword: str, new_value: bool) -> bool:
        """Sets the value of an alert keyword and outputs it to the actor.

        Returns a boolean indicating whether the value has changed.
        """

        if keyword not in self.keywords:
            raise KeyError(f"Invalid alert keyword {keyword}.")

        changed = self.keywords[keyword] != new_value

        self.keywords[keyword] = new_value

        if new_value is True:
            level = logging.WARNING
        else:
            level = logging.INFO

        # Repeatedly output the keyword if the alert is on.
        # Otherwise only if it changed.
        if new_value is True or changed is True:
            self.notify({keyword: int(self.keywords[keyword])}, level=level)

        return changed

    async def _loop(self):
        """The main monitoring loop."""

        coros = []
        if "robot" in self.config["enabled"]:
            coros.append(self._check_robots)
        if "ieb" in self.config["enabled"]:
            coros.append(self._check_ieb)
        if "flow" in self.config["enabled"]:
            coros.append(self._check_flow)
        if "temperature" in self.config["enabled"]:
            coros.append(self._check_outside_temperature)
        if "chiller" in self.config["enabled"]:
            coros.append(self._check_chiller)

        while True:
            for coro in coros:
                try:
                    await coro()
                except Exception as err:
                    self.notify(
                        f"Failed running alerts coroutine {coro.__name__}: {err}"
                    )

            await asyncio.sleep(self.interval)

    async def get_dew_point_temperarure(self):
        """Returns the ambient and dew point temperatures."""

        assert isinstance(self.ieb, IEB)

        temp_config = config["alerts"]["temperature"]

        temp = (await self.ieb.read_device(temp_config["sensor_temp"], adapt=True))[0]
        rh = (await self.ieb.read_device(temp_config["sensor_rh"], adapt=True))[0]

        # Dewpoint temperature.
        t_d = temp - (100 - rh) / 5.0

        return temp, t_d

    async def shutdown_gfas(self):
        """Shutdowns the GFAs without touching the rest of the FPS."""

        if not isinstance(self.ieb, IEB):
            self.notify(
                "IEB not connected, cannot power off GFAs.",
                level=logging.ERROR,
            )
            return

        self.notify("Shutting down cameras.")

        for gfa in range(1, 7):
            device = self.ieb.get_device(f"GFA{gfa}")

            assert isinstance(device, Relay)
            await device.open()

            await asyncio.sleep(0.5)

    async def _shutdown_device(self, device: Device | str):
        """Shuts down a device."""

        if isinstance(device, str):
            if isinstance(self.ieb, IEB):
                device = self.ieb.get_device(device)
            else:
                self.notify(
                    f"IEB not connected, cannot find device {device}.",
                    level=logging.ERROR,
                )
                return

        assert isinstance(device, Relay)
        await device.open()

    async def shutdown_fps(
        self,
        nucs: bool = False,
        gfas: bool = False,
        cans: bool = False,
    ):
        """Shutdowns the robots and optionally other electronics."""

        if not isinstance(self.ieb, IEB):
            self.notify(
                "IEB not connected, cannot power off FPS.",
                level=logging.ERROR,
            )
            return

        self.notify("Shutting down power supplies.")
        for ps in range(1, 7):
            await self._shutdown_device(f"PS{ps}")

        if gfas is True:
            await self.shutdown_gfas()

        if cans is True:
            self.notify("Shutting down CAN devices.")
            for can in range(1, 7):
                await self._shutdown_device(f"CM{can}")

        if nucs is True:
            self.notify("Shutting down NUCs.")
            for nuc in range(1, 7):
                await self._shutdown_device(f"NUC{nuc}")

    async def _check_robots(self):
        """Checks robot temperature."""

        if not isinstance(self.ieb, IEB):
            self.notify("IEB not connected. Cannot check robot temperatures.")
            return

        robot_config = config["alerts"]["robot"]

        sensor = robot_config["sensor"]
        temperature = (await self.ieb.read_device(sensor, adapt=True))[0]

        if temperature > robot_config["critical"]:
            changed = self.set_keyword("alert_robot_temp_critical", True)
            if not changed:
                return

            self.notify("Critical robot temperature reached.")
            await self.shutdown_fps()

        elif temperature >= robot_config["warning"]:
            self.set_keyword("alert_robot_temp_warning", True)
            self.notify("Robot temperature exceeds safe limits.")

        else:
            self.set_keyword("alert_robot_temp_critical", False)
            self.set_keyword("alert_robot_temp_warning", False)

    async def _check_ieb(self):
        """Checks IEB internal temperature."""

        if not isinstance(self.ieb, IEB):
            self.notify("IEB not connected. Cannot check IEB temperature.")
            return

        ieb_config = config["alerts"]["ieb"]

        sensor = ieb_config["sensor"]
        temperature = (await self.ieb.read_device(sensor, adapt=True))[0]

        if temperature > ieb_config["critical"]:
            changed = self.set_keyword("alert_ieb_temp_critical", True)
            if not changed:
                return

            self.notify("Critical IEB temperature reached.")
            await self.shutdown_fps(nucs=True, gfas=True, cans=True)

        elif temperature >= ieb_config["warning"]:
            self.set_keyword("alert_ieb_temp_warning", True)
            self.notify("IEB temperature exceeds safe limits.")

        else:
            self.set_keyword("alert_ieb_temp_critical", False)
            self.set_keyword("alert_ieb_temp_warning", False)

        # If a GFA has caused a temperature alert and it's then disconnected
        # the alert won't clear because that camera stops reporting status.
        # To prevent that here we loop over the power status of each camera
        # and if it's off we disable the alert. This does not immediately disable
        # the alarm but next time that _check_gfa() is called it will refresh
        # the keywords.
        for gfa_id in range(1, 7):
            relay_status = await self.ieb.read_device(f"GFA{gfa_id}")
            if relay_status == "open":
                self._gfa_alerts.pop(f"gfa{gfa_id}", None)

    async def _check_gfa(self, model: dict, key: TronKey):
        """Check GFA temperatures."""

        if key.name != "status":
            return

        gfa_config = config["alerts"]["gfa"]

        camera_name: str = key.value[0]
        if not camera_name.startswith("gfa"):
            return

        base_temperature: float = float(key.value[17])

        if base_temperature >= gfa_config["critical"]:
            changed = self.set_keyword("alert_gfa_temp_critical", True)
            if not changed:
                return

            self.notify(f"Critical GFA temperature reached on camera {camera_name}.")
            self._gfa_alerts[camera_name] = True

            # This will only run once since once we shut down the GFAs the keyword
            # is not output anymore.
            await self.shutdown_gfas()

        elif base_temperature >= gfa_config["warning"]:
            self.set_keyword("alert_gfa_temp_warning", True)
            self.notify(f"GFA {camera_name} temperature exceeds safe limits.")
            self._gfa_alerts[camera_name] = True

        else:
            self._gfa_alerts[camera_name] = False
            if all([value is False for value in self._gfa_alerts.values()]):
                self.set_keyword("alert_gfa_temp_critical", False)
                self.set_keyword("alert_gfa_temp_warning", False)

    async def _check_flow(self):
        """Check flow rates."""

        if not isinstance(self.ieb, IEB):
            self.notify("IEB not connected. Cannot check flow rates.")
            return

        flow_config = config["alerts"]["flow"]

        sensor = flow_config["sensor"]
        flow = (await self.ieb.read_device(sensor, adapt=True))[0]

        if flow < flow_config["critical"]:
            self.set_keyword("alert_fps_flow", True)
            self.notify("FPS coolant flow is below limits.")

        else:
            self.set_keyword("alert_fps_flow", False)

    async def _check_outside_temperature(self):
        """Checks if the outside temperature is close to the dew point."""

        if not isinstance(self.ieb, IEB):
            self.notify("IEB not connected. Cannot check outside temperature.")
            return

        temp, t_d = await self.get_dew_point_temperarure()

        if temp < t_d + config["alerts"]["temperature"]["dew_threshold"]:
            self.set_keyword("alert_dew_point", True)
            self.notify("Outside temperature is approaching dew point limit.")

        else:
            self.set_keyword("alert_dew_point", False)

    async def _check_chiller(self):
        """Checks the chiller status."""

        if not isinstance(self.ieb, IEB):
            self.notify("IEB not connected. Cannot run chiller checks.")
            return

        chiller = Chiller.create()
        assert chiller is not None

        try:
            setpoint = (await chiller.read_device("TEMPERATURE_USER_SETPOINT"))[0]
            fluid_temp = (await chiller.read_device("DISPLAY_VALUE"))[0]
        except Exception as err:
            self.notify(f"Failed reading chiller values: {err}", level=logging.ERROR)

        _, t_d = await self.get_dew_point_temperarure()

        if fluid_temp < t_d + config["alerts"]["temperature"]["dew_threshold"]:
            self.set_keyword("alert_chiller_dew_point", True)
            self.notify("Fluid temperature is approaching dew point limit.")

        else:
            self.set_keyword("alert_chiller_dew_point", False)

        chiller_config = config["alerts"]["chiller"]

        supply_temp = (await self.ieb.read_device(chiller_config["sensor_supply"]))[0]

        if abs(setpoint - supply_temp) > chiller_config["threshold"]:
            self.set_keyword("alert_fluid_temperature", True)
            self.notify("Chiller set point is different from supply temperature.")

        else:
            self.set_keyword("alert_fluid_temperature", False)

        # Check if there are chiller alerts.
        chiller_alerts: list[str] = []
        chiller_mod = chiller.modules["chiller"]
        for chiller_dev_name in chiller_mod.devices:
            if chiller_dev_name.startswith("alert_"):
                value: Any = await chiller.read_device(chiller_dev_name, adapt=False)
                if value > 0:
                    chiller_alerts.append(chiller_dev_name)

        if len(chiller_alerts) > 0:
            self.set_keyword("alert_chiller_fault", True)
            self.notify(
                "The following chiller alerts are active: " + ", ".join(chiller_alerts)
            )
        else:
            self.set_keyword("alert_chiller_fault", False)
