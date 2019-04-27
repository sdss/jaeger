#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-27 13:20:39

import json

import asyncioActor


class JaegerActor(asyncioActor.Actor):
    """The jaeger SDSS-style actor."""

    def __init__(self, fps, **kwargs):

        self.fps = fps

        super().__init__(status_callback=self.report_status, **kwargs)

    async def report_status(self, transport):
        """Reports the status to the status server."""

        status = self.fps.get_status()
        status_json = json.dumps(status)

        transport.write(status_json.encode() + '\n'.encode())

        return status
