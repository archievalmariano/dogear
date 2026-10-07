"""DOGEAR's weekly publisher (docs/PUBLISHING.md §3-§6, §12).

Local and model-free. It stages an issue from the canonical dataset, checks it
independently of the selector, uploads immutable artifacts, and publishes them
with one conditional write of the registry (``publication.json``), recovering
from interrupted runs by reading authoritative storage, never by guessing.

    policy.py       editorial settings the publisher needs but must not decide
    store.py        object storage interface; in-memory (fault injection) and directory stores
    registry.py     the registry schema and its pure state transitions
    pubdata.py      the publication-data repo: staged weeks, pending transaction, history
    eligibility.py  independent check of every rendered record against the dataset
    staging.py      generate + validate a week's artifacts and their provenance
    publisher.py    the transactions: stage, promote, launch, correct, rollback, restore, resume
    routes.py       the reference request router the Worker must match
"""
