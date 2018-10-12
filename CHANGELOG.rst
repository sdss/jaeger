.. _jaeger-changelog:

=========
Changelog
=========

* First complete pass at the documentation.
* :feature:`3` Add ``skip-error`` option to ``jaeger demo``.
* :bug:`2` Fix double setting of status when command times out.
* :feature:`1` Added `.FPS.abort` method to cancel all trajectories.
* Expose `.Positioner.set_position` as a public method.
* Load layout when `.FPS` is instantiated.
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
