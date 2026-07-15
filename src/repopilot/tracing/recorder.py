"""Idempotent P6 trace writer backed by RuntimeStore."""

from __future__ import annotations

from repopilot.persistence.runtime_store import RuntimeStore
from repopilot.tracing.contracts import TraceEvent, TraceRecordingError
from repopilot.tracing.sanitization import sanitize_trace_payload


class TraceRecorder:
    def __init__(self, store: RuntimeStore, *, max_events_per_run: int) -> None:
        if max_events_per_run < 10:
            raise ValueError("max_events_per_run must be at least 10")
        self._store = store
        self._max_events = max_events_per_run

    async def record(self, event: TraceEvent) -> bool:
        """Persist one event, returning False only for an idempotent duplicate."""

        try:
            return await self._store.append_trace(
                run_id=event.run_id,
                event_key=event.event_key,
                event_type=event.event_type.value,
                node_name=event.node_name,
                phase=event.phase,
                status=event.status,
                safe_payload=sanitize_trace_payload(event.payload),
                max_events=self._max_events,
            )
        except Exception as exc:
            raise TraceRecordingError(
                f"failed to record trace event {event.event_type.value}"
            ) from exc
