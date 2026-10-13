# Authentication API overview

Owner: Identity Team. Criticality: high.

The Authentication API issues and verifies access tokens for every first-party
surface. The Authentication API reads credentials and profile rows from the User
Database. Active sessions are kept in the Session Cache so token checks stay
under 10ms. Signing and verification are delegated to the Token Service.

## Consumers

The Web App exchanges its session cookie for an access token on every page load.
Mobile, Billing and the Partner Portal are documented separately.

Every token issue and revoke is emitted to the Audit Log Service.

## Token formats

Two formats are live. The current format is a signed JWT with a 15 minute
expiry. The legacy token format is an opaque 40-character string resolved by a
database lookup; it has no expiry and is still accepted on the /token refresh
path.

## Testing

The Web App E2E Suite drives a full login flow on every pull request.
