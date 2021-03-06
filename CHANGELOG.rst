.. _jaeger-changelog:

=========
Changelog
=========

* :support:`96` Discontinue the use of ``sdsscore``. Improved the handling of user configuration files.
* :support:`95` Support Python 3.9.

* :release:`0.6.0 <2020-10-15>`
* :bug:`-` Do not pass context to actor commands.
* :bug:`-` Fix starting status server.
* :support:`89` Implement daemon.
* :bug:`-` Initialise FPS during ``upgrade-firmware``. If the FPS is not initialised and a layout is not loaded there is no way for the upgrade script to know what positioners may be connected or their statuses.
* :feature:`-` Allow to skip cogging calibration.
* :support:`-` Remove QA database features since they need a full rethink.
* :support:`-` Update ``sdss-drift`` to ``0.1.5`` to fix ``pymodbus`` import.
* :support:`90` Remove the option to initialise datums in `.Positioner`.
* :support`-` Wrap message from ``CANNetBus`` in custom message class with additional ``__slots__``. This fixes a deprecation introduced in ``python-can>=3.3.4``.
* :support:`-` ``motor_steps`` and ``time_step`` are now defined in the configuration file under ``positioner``.
* :feature:`91` Implement additional commands for powering up/down hall sensors, and setting the open/closed loop. In the process, `.Positioner` was cleaned and streamlined. Most functions now raise a ``PositionerError`` if they fail, instead of failing silently with a log message.
* :support:`-` The default logging level for the console is not warning. In the CLI, the logging level can be adjusted using the ``-v`` flag. ``-v`` will set the logging level to ``INFO``; ``-vv`` will set the level to ``DEBUG``; ``-vvv`` will also set the CAN logging level to ``DEBUG``.

* :release:`0.5.2 <2020-07-31>`
* :support:`-` Adapt actor system to use ``CLU>=0.3.0``.

* :release:`0.5.1 <2020-07-29>`
* :feature:`86` Allow to pass a custom configuration file in the CLI using the flag ``-c/--config``.
* :feature:`-` Add actor command ``info`` to report the configuration file and version.

* :release:`0.5.0 <2020-07-20>`
* :feature:`62` Add a new `.Trajectory` class as a low-level method to send trajectories.
* :support:`67` Improve initialisation time by making sure all commands after the initial ``GET_FIRMWARE_VERSION`` know how many positioners are connected and don't time out.
* :support:`68` Use ``sdsstools`` instead of core utilities. Some clean-up of the packaging files.
* :support:`-` Adapt to using CLU>=0.2.0.
* :support:`-` Retrieve configuration from ``$SDSSCORE_DIR/configuration/actors/jaeger.yaml`` or from ``~/.config/sdss/jaeger.yml``.
* :feature:`51` Set up an asyncio exception handler and make the `.Poller` use it if there is a problem with the callback.
* :bug:`64` Fixed WAGO disconnects by increasing the timeout of the hardware.
* :support:`61` Stop the positioners before existing if CLI receives a SIGINT, SIGTERM, or SIGHUP.
* :bug:`72` (also :issue:`73`) Fix UIDs not being returned to the pool in some cases, which emptied it after a while.
* :support:`-` Rename ``cli.py`` to ``__main__.py``.
* :feature:`76` Implement calibration commands and routines.
* :feature:`75` Implement trajectories using SYNC line.
* :support:`21` (and several associated issues) Remove WAGO and use external `drift <https://github.com/sdss/drift>`__ library.
* :support:`70` Better documentation for firmware update.
* :support:`83` Use GitHub workflows.

* :release:`0.4.2 <2019-11-22>`
* :feature:`59` Add an ``engineering_mode`` flag to `.FPS` (can be toogled using the ``jaeger --danger``) flag to override most safety warnings for debugging.
* Unless ``immediate=True`` is passed to `.Poller.set_delay`, waits for the current task to finish.
* Fix call to `.Positioner.goto` from CLI.

* :release:`0.4.1 <2019-11-21>`
* Support versions ``04.00.XX`` and ``04.01.XX`` of Tendo with `.PositionerStatusV4_0` and `.PositionerStatusV4_1` maskbits.
* Significant clean-up of how pollers are used.
* `~jaeger.commands.send_trajectory` now raises exceptions on error.
* :feature:`57` Added `.FPS.moving` and `.Positioner.moving` attributes to determine whether it is save to move the FPS.
* :feature:`56` Move time for go to moves is calculated and reported.
* Very significant rewrite of how messages and replies are matched. Now there is a pool of unique identifiers. Each message gets assigned a UID from the pool corresponding to its ``command_id`` and ``positioner_id``. When a reply is received, it is matched based on ``command_id``, ``positioner_id``, and ``UID``. At that point the UID is returned to the pool. Broadcast messages always receive the reserved ``UID=0``. This means that two broadcast of the same command should not be running at the same time or replies could be misassigned.
* Recognise and deal with CAN\@net devices already in use.

* :release:`0.4.0 <2019-11-19>`
* :feature:`46` Implement a QA database for moves.
* :feature:`13` Abort trajectory and lock the FPS if either a collided status is detected in a positioner or if command 18 is received from the CAN network.
* Add `.SetCurrent` command to actor.
* Fix bug due to use of unsigned integers when passing a negative position.
* :feature:`49` Positioner status and position polling is now done from the FPS instead of from each positioner.
* :feature:`54` Add firmware upgrade command to actor.
* :bug:`53` Fix issues dealing with positioners that in the layout but not connected.
* :feature:`52` Add limits to `~.Positioner.goto`.

* :release:`0.3.0 <2019-11-13>`
* Change file layout to include a positioner ID.
* Add command `.SetCurrent`.
* Modify ``jaeger`` CLI command to use ``async def`` and ``await``.
* Add ``is_bootloader`` to output of ``status`` command.
* :feature:`24` (with :issue:`28`) Initial implementation of WAGO PLCs and associated actor commands.
* :feature:`12` Initial but fully functional implementation of TCP/IP actor.
* :bug:`39` Use ``loop.create_task`` instead of `asyncio.create_task` in `.Poller`, which seems to fix using jaeger in IPython.
* :feature:`40` Allow to instantiate an FPS without a WAGO connection.
* :feature:`37` Support power cycling a PLC.
* :support:`22` Moved some configuration parameters under ``positioner``.
* :feature:`29` Output WAGO status on a timer.

* :release:`0.2.1 <2019-06-29>`
* Fix ``MANIFEST.in`` not including the requirements files.

* :release:`0.2.0 <2019-06-29>`
* Added ``home`` command to ``jaeger`` CLI.
* Fixed bug in which the positions for ``SetActualPosition`` were being sent in degrees instead of in steps.
* Fixed bug that would raise an exception during initialisation if no positioner had replied to ``GET_STATUS``.
* First complete pass at the documentation.
* :feature:`3` Add ``skip-error`` option to ``jaeger demo``.
* :bug:`2` Fix double setting of status when command times out.
* :feature:`1` Added `.FPS.abort` method to cancel all trajectories.
* Expose `.Positioner.set_position` as a public method.
* Load layout when `.FPS` is instantiated.
* Improved logging system.
* Added initial actor features.
* :feature:`9` Initial implementation of the ``CAN@net`` bus.
* Renamed ``interfaces -> profiles`` in configuration.
* :bug:`11` Fix endianess of firmware version.
* :feature:`7` Poll CAN@net device for status.

* :release:`0.1.0 <2018-10-10>`
* Initial documentation.
* Added CLI interface.
* Added convenience function to upgrade firmware.
* Added utilities to convert from bytes to int and vice versa, and to build and parse identifiers.
* Added several helpers (`.AsyncQueue`, `.Poller`, `.StatusMixIn`)
* Added maskbits based on the `~enum.IntFlag` enumeration.
* Implemented `.Command` class and subclasses for all available commands.
* Added `.FPS`, `.JaegerCAN`, and `.Positioner` classes.
* Basic framework.
