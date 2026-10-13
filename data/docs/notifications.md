# Notification Service

Owner: unassigned.

The Notification Service consumes billing events to send receipts. On a delivery
failure the Notification Service calls back into Billing Service to mark the
receipt unsent.

There is no test suite and no on-call rotation. The service reads an auth token
from a shared config file; nobody has confirmed which token format it uses.
