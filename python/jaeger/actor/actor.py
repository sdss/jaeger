#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import json
import logging

import clu
from clu import command_parser as jaeger_parser
from clu.misc.logger import ActorHandler

from jaeger import __version__, log


__all__ = ['JaegerActor']


class JaegerActor(clu.LegacyActor):
    """The jaeger SDSS-style actor."""

    def __init__(self, fps, *args, **kwargs):

        self.fps = fps

        # Pass the FPS instance as the second argument to each parser
        # command (the first argument is always the actor command).
        self.parser_args = [fps]

        ieb_status_delay = kwargs.pop('ieb_status_delay', 60)

        super().__init__(*args, parser=jaeger_parser, **kwargs)

        self.version = __version__

        # Add ActorHandler to log
        self.actor_handler = ActorHandler(self, code_mapping={logging.INFO: 'd'})
        log.addHandler(self.actor_handler)
        self.actor_handler.setLevel(logging.INFO)

        if fps.ieb and not fps.ieb.disabled:
            self.timer_commands.add_command('ieb status', delay=ieb_status_delay)

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
