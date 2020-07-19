.. _actor:

Actor
=====

The jaeger actor provides a TCP/IP interface to the jaeger functionality. jaeger uses `CLU <https://clu.readthedocs.io/en/latest/>`__ to provide the actor framework. Most actor commands are thin wrappers around the functionality provided by the jaeger API.

The following commands are available in the jaeger actor.

.. click:: jaeger.actor:jaeger_parser
   :prog: jaeger-actor
   :show-nested:
