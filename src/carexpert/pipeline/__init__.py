"""Pipeline: from raw adverts to ranked, explained deals."""

from .dedupe import fingerprint, looks_like_same_car
from .ingest import IngestStats, from_row, ingest, mark_stale
from .run import ScanReport, dispatch_alerts, scan

__all__ = [
    "IngestStats",
    "ScanReport",
    "dispatch_alerts",
    "fingerprint",
    "from_row",
    "ingest",
    "looks_like_same_car",
    "mark_stale",
    "scan",
]
