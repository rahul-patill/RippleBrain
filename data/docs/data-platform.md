# Data platform

Owner: Data Team. Criticality: medium.

The Analytics Pipeline reads the Billing Service invoice stream hourly. The
pipeline writes normalised fact tables into the Data Warehouse. The Reporting
Tool queries the Data Warehouse directly for revenue dashboards.

Auth metadata is denormalised into the fact tables, including a token_kind
column that distinguishes legacy from current tokens.
