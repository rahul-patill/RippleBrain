# Mobile App notes

Owner: Mobile Team. Criticality: high.

The mobile app calls /token on every launch and still sends the legacy token
format on refresh. Removing the legacy format without a client release will log
every installed app out on next launch.

The Mobile App Test Suite pins a recorded legacy token fixture. The fixture is
checked into the repo and will need regenerating if the format changes.

Minimum supported app version in the field is 4.2, released 14 months ago.
