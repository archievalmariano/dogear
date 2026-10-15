## What changes

## Owner review (PUBLISHING §4b)

The unattended `production-scheduled` credentials trust every workflow on `main`.
If this PR touches any of the following, the owner reviews the diff before merging
(and Codex reviews routing changes):

- [ ] `.github/workflows/**`
- [ ] `tools/run_publish.py`
- [ ] the publisher CLI's argument routing (`dogear/publish/cli.py`)
- [ ] `tests/test_route.py` or `tests/test_hosting.py` `RoutingBoundaryTests`

None of the above: leave unchecked.
