Configuration file
==================

jaeger uses an internal YAML file to store configuration values. The default configuration as of
version |jaeger_version| is:

.. literalinclude:: ../../python/jaeger/etc/jaeger.yml
   :language: yaml

When jaeger is imported the default configuration is loaded. It then looks for user configuration files in ``$SDSSCORE_DIR/configuration/$OBSERVATORY/actors/jaeger.yaml`` and ``~/.config/sdss/jaeger.yaml`` (in that order). If found, the user file is used to update the default configuration. For example, a user file such as

.. code-block:: yaml

   # file: ~/.config/sdss/jaeger.yaml

   profiles:
     cannet:
       interface: cannet
       channels: [10.1.10.110]
       port: 19228
       buses: [1, 2, 3, 4]
       bitrate: 1000000


will update the ``cannet`` profile while modifying the remaining parameters.

When running jaeger from the :ref:`CLI <cli>`, it is possible to pass a user configuration file by calling ``jaeger --config <CONFIG-FILE> ...``. If not provided, the configuration file discovery default to the above logic.

To prevent the beta arms to take a value below 160 degrees (the point at which collisions are possible if the folded mode is :math:`\beta=180`) on can specify

.. code-block:: yaml

   safe_mode: true

And to define the minimum value of the beta arm

.. code-block:: yaml

   safe_mode:
      min_beta: 170
