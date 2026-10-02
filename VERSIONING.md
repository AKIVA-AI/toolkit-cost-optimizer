# Versioning

The project follows Semantic Versioning from 1.0.0.

- **Report output** is a report envelope (`docs/report-envelope.md`, `schemas/report-envelope.v1.json`). The `predicate.summary` of each command is documented in the README. Removing or renaming a summary field is a major change; adding one is a minor change. Changes are listed in `CHANGELOG.md`.
- **`--legacy-json`** keeps the pre-1.0 output (`schemas/cli-output.schema.json`) for one minor release and is removed in 1.1.0.
- **Row schema**: versions 1 and 2 are accepted and accept additional fields. A new required field would mean a new schema version.
- **Price table**: updates to the built-in table change `simulate` and `route` results. They are released as minor versions and listed in `CHANGELOG.md` with the new `as_of` date.
