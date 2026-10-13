# Search Service

Owner: Discovery Team. Criticality: medium.

The Search Service indexes the product catalogue and serves the search box in
the Web App. The Web App calls the Search Service on every keystroke above three
characters.

The Search Service depends on the Authentication API for the service token it
attaches to internal calls, and that token is still in the legacy token format.

A nightly job in the Search Indexer rebuilds the index from the Data Warehouse.
The Search Service depends on the Search Indexer completing before 06:00 UTC.

There are no tests for the Search Indexer.
