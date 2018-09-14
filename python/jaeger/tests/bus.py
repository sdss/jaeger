#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-04
# @Filename: bus.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-13 21:47:07

import queue

import can
import jaeger
from can.interfaces.virtual import VirtualBus
from jaeger import log


__ALL__ = ['VirtualBusTester']


class VirtualBusTester(VirtualBus):

    def __init__(self, fps=None, **kwargs):

        self.fps = fps

        super().__init__(channel='test_bus')

    def send(self, message, timeout=None):
        """Receives and processes a message."""

        self._check_if_open()

        log.debug(f'VirtualBusTester received message {message.uuid}')

        assert message.is_extended_id, 'this bus only accepts messages with extended id.'

        positioner_id, command_id, __ = jaeger.utils.parse_identifier(message.arbitration_id)
        payload = message.data

        if command_id == jaeger.commands.CommandID.GET_ID:

            assert payload == b'', 'GET_ID command does not accept extra data.'

            for available_positioner in [5]:

                if positioner_id == 0 or positioner_id == available_positioner:

                    arbitration_id = jaeger.utils.get_identifier(
                        available_positioner, command_id, response_code=0)

                    reply_message = can.Message(data=[], arbitration_id=arbitration_id,
                                                extended_id=True)

                    log.debug(f'sending reply with '
                              f'arbitration_id={reply_message.arbitration_id} '
                              f'and data={reply_message.data!r}')

                    all_sent = True
                    for bus_queue in self.channel:
                        if bus_queue is not self.queue:
                            try:
                                bus_queue.put(reply_message, block=True, timeout=timeout)
                            except queue.Full:
                                all_sent = False

                    if not all_sent:
                        raise can.CanError('Could not send message to one or more recipients')
