# Billing Service

Owner: Payments Team. Criticality: high.

Billing Service validates every incoming request against the Authentication API
before touching an invoice. It accepts both token formats today.

Billing Contract Tests assert the shape of the auth claims the service expects.
The claims differ between the two token formats, so the contract fixtures cover
both.

Billing emits an invoice stream that the data platform consumes, and calls the
Notification Service for receipts.
