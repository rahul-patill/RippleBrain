# Legacy Token Runbook

Owner: Identity Team.

This runbook covers rollback steps for the legacy token format issued by the
Authentication API.

## Rollback

1. Re-enable the LEGACY_TOKENS flag in the Authentication API config.
2. Warm the Session Cache from the User Database.
3. Confirm the Partner Portal can still broker a login.

## Known gaps

There is no runbook step for the Notification Service. Nobody is listed as its
owner and it has no test suite, so the blast radius through it is unverified.
