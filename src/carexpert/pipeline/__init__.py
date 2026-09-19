"""Pipeline: from raw adverts to ranked, explained deals."""

from .dedupe import fingerprint, looks_like_same_car
from .ingest import IngestStats, from_row, ingest, mark_stale
from .run import ScanReport, dispatch_alerts, import_captures, scan

__all__ = [
    "IngestStats",
    "ScanReport",
    "dispatch_alerts",
    "fingerprint",
    "from_row",
    "import_captures",
    "ingest",
    "looks_like_same_car",
    "mark_stale",
    "scan",
]
