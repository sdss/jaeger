#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import json
import logging
import os

import clu
import clu.protocol
from clu.tools import ActorHandler

from jaeger import FPS, __version__, config, log
from jaeger.commands import CommandID
from jaeger.maskbits import LowTemperature


__all__ = ["JaegerActor"]


class JaegerActor(clu.LegacyActor):
    """The jaeger SDSS-style actor."""

    def __init__(self, fps: FPS, *args, ieb_status_delay=60, **kwargs):

        self.fps = fps

        jaeger_base = os.path.join(os.path.dirname(__file__), "..")
        if "schema" not in kwargs:
            kwargs["schema"] = os.path.join(jaeger_base, "etc/schema.json")
        else:
            if not os.path.isabs(kwargs["schema"]):
                kwargs["schema"] = os.path.join(jaeger_base, kwargs["schema"])

        # Pass the FPS instance as the second argument to each parser
        # command (the first argument is always the actor command).
        self.parser_args = [fps]

        self.low_temperature = LowTemperature.NORMAL

        super().__init__(*args, **kwargs)

        self.version = __version__

        # Add ActorHandler to log
        self.actor_handler = ActorHandler(self, code_mapping={logging.INFO: "d"})
        log.addHandler(self.actor_handler)
        self.actor_handler.setLevel(logging.INFO)

        if fps.ieb and not fps.ieb.disabled:
            self.timed_commands.add_command("ieb status", delay=ieb_status_delay)
            asyncio.create_task(self.handle_temperature())

    async def start_status_server(self, port, delay=1):
        """Starts a server that outputs the status as a JSON on a timer."""

        self.status_server = clu.protocol.TCPStreamPeriodicServer(
            self.host,
            port,
            periodic_callback=self._report_status_cb,
            sleep_time=delay,
        )

        await self.status_server.start()

        self.log.info(f"starting status server on {self.host}:{port}")

    async def _report_status_cb(self, transport):
        """Reports the status to the status server."""

        status = await self.fps.report_status()
        status_json = json.dumps(status)

        transport.write(status_json.encode() + "\n".encode())

        return status

    async def handle_temperature(self):
        """Handle positioners in low temperature."""

        async def set_rpm(activate):
            if activate:
                rpm = config["low_temperature"]["rpm_cold"]
                self.write("w", text=f"Low temperature mode. Setting RPM={rpm}.")
            else:
                rpm = config["low_temperature"]["rpm_normal"]
                self.write(
                    "w",
                    text=f"Disabling low temperature mode. Setting RPM={rpm}.",
                )

        async def set_idle_power(activate):
            if activate:
                ht = config["low_temperature"]["holding_torque_very_cold"]
                self.write(
                    "w",
                    text="Very low temperature mode. Setting holding torque.",
                )
            else:
                ht = config["low_temperature"]["holding_torque_normal"]
                self.write(
                    "w",
                    text="Disabling very low temperature mode. Setting holding torque.",
                )
            await self.fps.send_to_all(
                CommandID.SET_HOLDING_CURRENT, alpha=ht[0], beta=ht[1]
            )

        sensor = config["low_temperature"]["sensor"]
        cold = config["low_temperature"]["cold_threshold"]
        very_cold = config["low_temperature"]["very_cold_threshold"]
        interval = config["low_temperature"]["interval"]

        while True:
            try:
                device = self.fps.ieb.get_device(sensor)
                temp = (await device.read())[0]

                if temp <= very_cold:
                    if self.low_temperature == LowTemperature.NORMAL:
                        await set_rpm(True)
                        await set_idle_power(True)
                    elif self.low_temperature == LowTemperature.COLD:
                        await set_idle_power(True)
                    else:
                        pass
                    self.low_temperature = LowTemperature.VERY_COLD

                elif temp <= cold:
                    if self.low_temperature == LowTemperature.NORMAL:
                        await set_rpm(True)
                    elif self.low_temperature == LowTemperature.COLD:
                        pass
                    else:
                        await set_idle_power(False)
                    self.low_temperature = LowTemperature.COLD

                else:
                    if self.low_temperature == LowTemperature.NORMAL:
                        pass
                    elif self.low_temperature == LowTemperature.COLD:
                        await set_rpm(False)
                    else:
                        await set_rpm(False)
                        await set_idle_power(False)
                    self.low_temperature = LowTemperature.NORMAL

                self.write("w", low_temperature=LowTemperature.NORMAL.value)

            except BaseException as err:
                self.write(
                    "w",
                    text=f"Cannot read device {sensor!r}. "
                    f"Low-temperature mode will not be engaged: {err}",
                )
                return

            await asyncio.sleep(interval)
