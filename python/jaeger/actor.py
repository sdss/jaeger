#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-04-24
# @Filename: actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-26 08:14:28

import asyncioActor


class JaegerActor(asyncioActor.Actor):
    """The jaeger SDSS-style actor."""

    def __init__(self, fps, **kwargs):

        self.fps = fps

        super().__init__(**kwargs)

        self.status_server.periodic_callback = self.report_status

    async def report_status(self, transport):
        """Reports the status to the status server."""

        pass
