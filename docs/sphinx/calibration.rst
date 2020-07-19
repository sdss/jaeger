Positioner calibration
======================

Positioners are shipped calibrated but it's possible that the calibration needs to be redone over their lifetime. The calibration procedure runs through the motor, datums, and cogging calibrations, and then saves the configuration to the positioner EPROM.

Performing calibration on a positioner
--------------------------------------

The calibration steps can be commanded independently while in normal mode (no bootloader):

- Motor calibration: command `~.StartMotorCalibration`. Wait until `~.PositionerStatusV4_0.DISPLACEMENT_COMPLETED`, `.MOTOR_ALPHA_CALIBRATED`, and `.MOTOR_BETA_CALIBRATED` are set.

- Datums calibration: command `~.StartDatumCalibration`. Wait until `~.PositionerStatusV4_0.DISPLACEMENT_COMPLETED`, `.DATUM_ALPHA_CALIBRATED`, and `.DATUM_BETA_CALIBRATED` are set.

- Cogging torque calibration: command `~.StartCoggingCalibration`. Wait until `.COGGING_ALPHA_CALIBRATED` and `.COGGING_BETA_CALIBRATED` are set. This step can take 20+ minutes.

- Save calibration: command `~.SaveInternalCalibration`.

Normally the calibration is done using the `.calibrate_positioner` coroutine. For example ::

    >>> from jaeger import FPS
    >>> from jaeger.commands import calibrate_positioner
    >>> fps = await FPS().initialise()
    >>> calibrate_positioner(fps, 31)

This can be conveniently run from the command line as

.. code-block:: console

    $ jaeger calibrate 31

API
---

.. automodule:: jaeger.commands.calibration
    :member-order: bysource
    :noindex:
