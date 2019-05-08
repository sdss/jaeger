#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-05-08 12:07:12

import json

import clu


class JaegerActor(clu.Actor):
    """The jaeger SDSS-style actor."""

    def __init__(self, fps, *args, **kwargs):

        self.fps = fps

        super().__init__(*args, **kwargs)

    @classmethod
    def from_config(cls, fps, config):
        """Creates an actor instance from a configuration file or dict."""

        new_actor = super().from_config(config, fps)

        return new_actor

    async def start_status_server(self, port, delay=1):
        """Starts a server that outputs the status as a JSON on a timer."""

        self.status_server = clu.protocol.TCPStreamPeriodicServer(
            self.host, port, periodic_callback=self._report_status_cb,
            sleep_time=delay)

        await self.status_server.start_server()

        self.log.info(f'starting status server on {self.host}:{port}')

    async def _report_status_cb(self, transport):
        """Reports the status to the status server."""

        status = self.fps.report_status()
        status_json = json.dumps(status)

        transport.write(status_json.encode() + '\n'.encode())

        return status
