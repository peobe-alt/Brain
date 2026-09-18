"""Scans started from the browser, run in the background.

A scan takes minutes: it cannot happen inside the request that asks for it,
or the page hangs and the browser gives up. So it runs on its own thread and
the page polls for progress.

One scan at a time, deliberately. Two scans running together would hit the
same site twice as fast, which is exactly what the politeness budget exists
to prevent, and the second would fight the first for the database anyway.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..db import session_scope
from ..schemas import SearchQuery

log = logging.getLogger(__name__)

#: How many finished jobs stay visible. Enough to look back at this morning's
#: scans, not enough to grow without bound in a process that never restarts.
HISTORY = 30


@dataclass
class ScanJob:
    """One scan, and everything a screen needs to show about it."""

    id: str
    label: str
    url: str | None = None
    sources: list[str] = field(default_factory=list)
    deep: int = 0
    status: str = "en attente"          # en attente | en cours | termine | echec
    percent: int = 0
    message: str = "Demarrage"
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    found: int = 0
    great: int = 0

    @property
    def done(self) -> bool:
        return self.status in ("termine", "echec")

    @property
    def duration_s(self) -> float:
        end = self.finished_at or datetime.utcnow()
        return max(0.0, (end - self.started_at).total_seconds())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "percent": self.percent,
            "message": self.message,
            "done": self.done,
            "summary": self.summary,
            "errors": self.errors,
            "found": self.found,
            "great": self.great,
            "duration_s": round(self.duration_s),
        }


class ScanRunner:
    """The one place that knows whether a scan is running."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ScanJob] = {}
        self._order: list[str] = []
        self._current: str | None = None

    # -- reading -----------------------------------------------------------

    def get(self, job_id: str) -> ScanJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 10) -> list[ScanJob]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order[-limit:])]

    def running(self) -> ScanJob | None:
        """The scan under way, or None.

        `_current` stays set after a scan ends, because the history needs it.
        Reading it as "running" left the banner up and the launch buttons
        greyed out for good: after one scan, the tool could no longer be used
        from the browser at all.
        """
        with self._lock:
            job = self._jobs.get(self._current) if self._current else None
            return None if job is None or job.done else job

    # -- starting ----------------------------------------------------------

    def start(
        self,
        *,
        label: str,
        sources: list[str],
        query: SearchQuery,
        url: str | None = None,
        deep: int = 0,
        notify: bool = True,
    ) -> tuple[ScanJob | None, str]:
        """Queue a scan, or say why it cannot start."""
        with self._lock:
            if self._current and not self._jobs[self._current].done:
                return None, "Un scan est deja en cours. Attendez qu'il se termine."
            job = ScanJob(id=uuid.uuid4().hex[:12], label=label, url=url,
                          sources=list(sources), deep=deep)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._current = job.id
            self._forget_old()

        thread = threading.Thread(
            target=self._run,
            args=(job, query, notify),
            name=f"scan-{job.id}",
            daemon=True,
        )
        thread.start()
        return job, ""

    def _forget_old(self) -> None:
        while len(self._order) > HISTORY:
            self._jobs.pop(self._order.pop(0), None)

    # -- running -----------------------------------------------------------

    def _run(self, job: ScanJob, query: SearchQuery, notify: bool) -> None:
        from ..pipeline import scan as run_scan

        def progress(message: str, percent: int) -> None:
            job.message = message
            job.percent = max(job.percent, min(99, percent))

        job.status = "en cours"
        try:
            with session_scope() as session:
                report = run_scan(
                    session,
                    sources=job.sources,
                    query=query,
                    deep=job.deep,
                    notify=notify,
                    search_url=job.url,
                    on_progress=progress,
                )
                job.summary = report.summary()
                job.errors = list(report.errors)
                job.found = sum(s.seen for s in report.collected.values())
                job.great = sum(1 for d in report.top if (d.get("score") or 0) >= 75)
            job.status = "echec" if report.errors and not job.found else "termine"
            job.message = "Termine"
            job.percent = 100
        except Exception as exc:  # une erreur ne doit pas emporter le serveur
            log.exception("scan %s en echec", job.id)
            job.status = "echec"
            job.message = str(exc)[:200] or "Erreur inattendue"
            job.errors.append(str(exc))
        finally:
            job.finished_at = datetime.utcnow()


#: One runner for the process. The web app imports this.
runner = ScanRunner()
