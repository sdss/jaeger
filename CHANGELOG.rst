.. _jaeger-changelog:

=========
Changelog
=========

* Change file layout to include a positioner ID.
* Add command `.SetCurrent`.
* Modify ``jaeger`` CLI command to use ``async def`` and ``await``.
* Add ``is_bootloader`` to output of ``status`` command.
* :feature:`24` (with :issue:`28`) Initial implementation of WAGO PLCs and associated actor commands.
* :feature:`12` Initial but fully functional implementation of TCP/IP actor.
* :bug:`39` Use ``loop.create_task`` instead of `asyncio.create_task` in `.Poller`, which seems to fix using jaeger in IPython.
* :feature:`40` Allow to instantiate an FPS without a WAGO connection.
* :feature:`37` Support power cycling a PLC.

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
