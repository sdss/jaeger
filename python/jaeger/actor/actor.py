#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import json
import logging
import os
from tempfile import NamedTemporaryFile
from time import time

import clu
import clu.protocol
from clu.tools import ActorHandler

import jaeger
from jaeger import FPS, __version__, log
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.ieb import IEB, Chiller


__all__ = ["JaegerActor"]


def merge_json(base: str, custom: str | None, write_temporary_file=False):
    """Merges two JSON files. Optional writes the result as a temporary file."""

    if custom is None:
        return base

    if os.path.samefile(base, custom):
        return base
    else:
        base_json = json.loads(open(base, "r").read())
        custom_json = json.loads(open(custom, "r").read())
        base_json["properties"].update(custom_json["properties"])

        if write_temporary_file is False:
            return base_json
        else:
            tmpfile = NamedTemporaryFile(delete=False, mode="w+")
            json.dump(base_json, tmpfile)
            tmpfile.flush()
            return tmpfile.name


class JaegerActor(clu.LegacyActor):
    """The jaeger SDSS-style actor."""

    def __init__(
        self,
        fps: FPS,
        *args,
        ieb_status_delay: float = 60.0,
        observatory: str | None = None,
        **kwargs,
    ):

        jaeger.actor_instance = self

        self.fps = fps

        # This is mostly for the miniwoks. If the schema file is not the base
        # one, merge them.
        base = os.path.join(os.path.dirname(__file__), "..")

        base_schema = os.path.realpath(os.path.join(base, "etc/schema.json"))

        schema = kwargs.get("schema", None)
        c_schema = os.path.realpath(os.path.join(base, schema)) if schema else None

        kwargs["schema"] = merge_json(base_schema, c_schema, write_temporary_file=True)

        # Pass the FPS instance as the second argument to each parser
        # command (the first argument is always the actor command).
        self.parser_args = [fps]

        # Set the observatory where the actor is running.
        if observatory is None:
            try:
                self.observatory = os.environ["OBSERVATORY"]
            except KeyError:
                raise JaegerError("Observatory not passed and $OBSERVATORY is not set.")
        else:
            self.observatory = observatory

        super().__init__(*args, **kwargs)

        self.version = __version__

        # Add ActorHandler to log and to the warnings logger.
        self.actor_handler = ActorHandler(
            self,
            level=logging.WARNING,
            filter_warnings=[JaegerUserWarning],
        )
        log.addHandler(self.actor_handler)
        if log.warnings_logger:
            log.warnings_logger.addHandler(self.actor_handler)

        self._alive_task = asyncio.create_task(self._report_alive())
        self._status_watcher_task = asyncio.create_task(self._status_watcher())
        self._chiller_watcher_task: asyncio.Task | None = asyncio.create_task(
            self._chiller_watcher()
        )

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

    async def _report_alive(self):
        """Outputs the ``alive_at`` keyword."""

        while True:
            self.write("d", {"alive_at": time()}, broadcast=True)
            await asyncio.sleep(60)

    async def _report_status_cb(self, transport):
        """Reports the status to the status server."""

        status = await self.fps.report_status()
        status_json = json.dumps(status)

        transport.write(status_json.encode() + "\n".encode())

        return status

    async def _status_watcher(self):
        """Listens to the status async generator."""

        async for status in self.fps.async_status():
            self.write("i", fps_status=f"0x{status.value:x}")

    async def _chiller_watcher(self):
        """Sets the chiller set point temperature."""

        last_changed: float | None = None
        last_setpoint: float | None = None
        failed: bool = False

        chiller = Chiller.create()

        while True:
            # Keep this inside the loop to allow for IEB and chiller reconnects.
            if isinstance(self.fps.ieb, IEB):
                ieb = self.fps.ieb

                dev_name = "TEMPERATURE_USER_SETPOINT"
                dev = chiller.get_device(dev_name)

                # Try up to 10 times since sometimes setting the temperature fails.
                for _ in range(10):
                    failed = False
                    changed: bool = False

                    try:
                        ambient_temp = (await ieb.read_device("T3"))[0]

                        if last_setpoint is None:
                            last_setpoint = (await dev.read())[0]

                        current_setpoint = (await dev.read())[0]

                        if abs(last_setpoint - current_setpoint) > 1.0:
                            # First we check if the set point has changed. If it has
                            # this usually means a power failure and we want to
                            # reset the set point immediately.

                            self.write(
                                "w",
                                {
                                    "text": "Chiller set-point has changed. "
                                    "Maybe the chiller power cycled."
                                },
                            )
                            await dev.write(int(last_setpoint * 10))
                            last_changed = time()
                            break

                        else:
                            # Compare the ambient temperature with the current
                            # chiller temperature. If the delta is > 1 degrees
                            # we reset the set point immediately, as we do if
                            # last_changed is None (we have never set the set
                            # point). Otherwise only set the temperature once
                            # an hour.

                            # New set point is one below ambient clipped to 1 degC.
                            new_temp = ambient_temp - 1
                            if new_temp <= 1:
                                new_temp = 1

                            delta_temp = abs(last_setpoint - new_temp)
                            now = time()

                            msg = {"text": f"Setting chiller to {round(new_temp, 1)} C"}

                            if last_changed is None or delta_temp > 1:
                                await dev.write(int(new_temp * 10))
                                changed = True
                            else:
                                delta_time_hours = (now - last_changed) / 3600.0
                                if delta_time_hours >= 1.0:
                                    await dev.write(int(new_temp * 10))
                                    changed = True

                            if changed is True:
                                self.write("i", msg)
                                last_setpoint = new_temp
                                last_changed = now

                            break

                    except Exception:
                        failed = True
                        await asyncio.sleep(2)
                        continue

                if failed is True:
                    self.write("e", {"text": "Failed setting chiller temperature."})

                await asyncio.sleep(60)
