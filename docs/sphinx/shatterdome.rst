
The shatterdome
===============

In the `shatterdome <http://pacificrim.wikia.com/wiki/Shatterdome>`__ we'll have a closer look at some of the internal mechanics of the jaeger.

The `CAN bus <.JaegerCAN>`
--------------------------

Loading from a profile
^^^^^^^^^^^^^^^^^^^^^^

The command queue
^^^^^^^^^^^^^^^^^


The `.FPS` class
----------------

Initialisation
^^^^^^^^^^^^^^

Sending commands
^^^^^^^^^^^^^^^^

Shutting down the FPS
^^^^^^^^^^^^^^^^^^^^^

Sending trajectories
^^^^^^^^^^^^^^^^^^^^

Trajectories can be sent either a `YAML <http://yaml.org>`__ file or a dictionary. In both cases the trajectory must include, for each positioner, a list of positions and times for the ``'alpha'`` arm in the format :math:`\rm [(\alpha_1, t_1), (\alpha_2, t_2), ...]`, and a similar dictionary for ``'beta'``. An example of YAML file with a valid trajectory for positioners 1 and 4 is

.. code-block:: yaml

    1:
        alpha: [[20, 5], [100, 10], [50, 15]]
        beta: [[90, 15], [85, 18]]
    4:
        alpha: [[200, 3], [100, 15]]
        beta: [[50, 5]]

And it can be commanded by doing ::

    >>> await fps.send_trajectory('my_trajectory.yaml')

Unless `~.FPS.send_trajectory` is called with ``kaiju_check=False`` (DANGER! Do not do that unless you are sure of what you are doing), jaeger will check with `kaiju <https://github.com/csayres/kaiju>`__ to confirm that the trajectory is safe to execute.

.. warning:: The kaiju check feature is not yet available and all trajectories are currently sent without any anti-collision check.


Aborting all trajectories
^^^^^^^^^^^^^^^^^^^^^^^^^


Positioner, status, and position
--------------------------------

Initialisation
^^^^^^^^^^^^^^

Position and status pollers
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sending a positioner to a position
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Waiting for a status
^^^^^^^^^^^^^^^^^^^^


Commands
--------

Creating a new command class
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Replies
^^^^^^^

When is a command marked done?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Time-outs
^^^^^^^^^


Configuration files
-------------------


Logging
-------
