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
from jaeger.alerts import AlertsBot
from jaeger.chiller import ChillerBot
from jaeger.exceptions import JaegerError, JaegerUserWarning


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

    def __init__(self, fps: FPS, *args, observatory: str | None = None, **kwargs):
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

        # Define alerts and chiller bots.
        self.alerts = AlertsBot(self.fps)

        chiller_config = self.config["chiller"].get("config", None)
        self.chiller = ChillerBot(self.fps) if chiller_config else None

    async def start(self, *args, **kwargs):
        """Starts the actor and the bots."""

        await super().start(*args, **kwargs)

        self.alerts.set_actor(self)
        await self.alerts.start()

        if self.chiller:
            self.chiller.set_actor(self)
            await self.chiller.start()

    async def stop(self):
        """Stops the actor and bots."""

        await self.alerts.stop()
        if self.chiller:
            await self.chiller.stop()

        return await super().stop()

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
