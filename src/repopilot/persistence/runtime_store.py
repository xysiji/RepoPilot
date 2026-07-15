"""Parameterized SQLite operations for run summaries and redacted trace events."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from repopilot.persistence.contracts import (
    RunNotFoundError,
    RunPage,
    RunRecord,
    TraceLimitExceededError,
    TraceRecord,
)

_SAFE_FINAL_REPORT_FIELDS = frozenset(
    {
        "run_id",
        "outcome",
        "summary",
        "modified_files",
        "repair_attempts",
        "max_repair_attempts",
        "model_calls",
        "approval_count",
        "patches_applied",
        "latest_test_outcome",
        "latest_test_exit_code",
        "safe_test_summary",
        "review_status",
        "review_findings",
        "errors",
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeStore:
    """Own serialized access to one runtime-index connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()

    async def create_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        state_schema_version: int,
        goal: str,
        max_repair_attempts: int,
    ) -> RunRecord:
        now = utc_now().isoformat()
        goal_sha256 = hashlib.sha256(goal.encode("utf-8")).hexdigest()
        async with self._lock:
            await self._connection.execute(
                """
                INSERT INTO runs(
                    run_id, thread_id, state_schema_version, status, created_at,
                    updated_at, max_repair_attempts, goal_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    thread_id,
                    state_schema_version,
                    "running",
                    now,
                    now,
                    max_repair_attempts,
                    goal_sha256,
                ),
            )
            await self._connection.commit()
        return await self.get_run(run_id)

    async def update_run_from_state(self, run_id: str, state: dict[str, Any]) -> RunRecord:
        now = utc_now()
        status = str(state.get("status", "running"))
        terminal = status not in {"running", "awaiting_approval"}
        proposal = state.get("pending_approval")
        proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        latest_test = state.get("latest_test_result")
        test_outcome = latest_test.get("outcome") if isinstance(latest_test, dict) else None
        review = state.get("review_result")
        review_status = review.get("status") if isinstance(review, dict) else None
        final_report = state.get("final_report")
        outcome = final_report.get("outcome") if isinstance(final_report, dict) else None
        safe_report = (
            {key: value for key, value in final_report.items() if key in _SAFE_FINAL_REPORT_FIELDS}
            if isinstance(final_report, dict)
            else None
        )
        report_json = (
            json.dumps(safe_report, ensure_ascii=False, separators=(",", ":"))
            if safe_report is not None
            else None
        )
        async with self._lock:
            cursor = await self._connection.execute(
                """
                UPDATE runs SET
                    state_schema_version = ?, status = ?, outcome = ?, updated_at = ?,
                    completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE NULL END,
                    current_proposal_id = ?, repair_attempts = ?, max_repair_attempts = ?,
                    model_calls = ?, latest_test_outcome = ?, review_status = ?,
                    final_report_json = ?
                WHERE run_id = ? AND deleted_at IS NULL
                """,
                (
                    int(state.get("state_schema_version", 0)),
                    status,
                    outcome,
                    now.isoformat(),
                    terminal,
                    now.isoformat(),
                    str(proposal_id) if proposal_id else None,
                    int(state.get("repair_attempts", 0)),
                    int(state.get("max_repair_attempts", 0)),
                    int(state.get("model_calls", 0)),
                    str(test_outcome) if test_outcome else None,
                    str(review_status) if review_status else None,
                    report_json,
                    run_id,
                ),
            )
            await self._connection.commit()
            if cursor.rowcount == 0:
                raise RunNotFoundError("run not found")
        return await self.get_run(run_id)

    async def get_run(self, run_id: str) -> RunRecord:
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ? AND deleted_at IS NULL",
                (run_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise RunNotFoundError("run not found")
        return _run_record(row)

    async def list_runs(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> RunPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        before = _decode_cursor(cursor) if cursor else None
        clauses = ["deleted_at IS NULL"]
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if before is not None:
            clauses.append("(updated_at < ? OR (updated_at = ? AND run_id < ?))")
            parameters.extend([before[0], before[0], before[1]])
        parameters.append(limit + 1)
        sql = (
            "SELECT * FROM runs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, run_id DESC LIMIT ?"
        )
        async with self._lock:
            db_cursor = await self._connection.execute(sql, parameters)
            rows = await db_cursor.fetchall()
            await db_cursor.close()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [_run_record(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            next_cursor = _encode_cursor(
                str(selected[-1]["updated_at"]), str(selected[-1]["run_id"])
            )
        return RunPage(items=items, next_cursor=next_cursor)

    async def append_trace(
        self,
        *,
        run_id: str,
        event_key: str,
        event_type: str,
        node_name: str | None,
        phase: str,
        status: str,
        safe_payload: dict[str, Any],
        max_events: int,
    ) -> bool:
        payload = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            existing_cursor = await self._connection.execute(
                "SELECT 1 FROM trace_events WHERE run_id = ? AND event_key = ?",
                (run_id, event_key),
            )
            existing = await existing_cursor.fetchone()
            await existing_cursor.close()
            if existing is not None:
                return False
            count_cursor = await self._connection.execute(
                "SELECT COUNT(*) FROM trace_events WHERE run_id = ?",
                (run_id,),
            )
            count_row = await count_cursor.fetchone()
            await count_cursor.close()
            if count_row is not None and int(count_row[0]) >= max_events:
                raise TraceLimitExceededError("trace event limit reached")
            cursor = await self._connection.execute(
                """
                INSERT OR IGNORE INTO trace_events(
                    event_key, run_id, event_type, node_name, phase, status,
                    created_at, safe_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    run_id,
                    event_type,
                    node_name,
                    phase,
                    status,
                    utc_now().isoformat(),
                    payload,
                ),
            )
            await self._connection.commit()
            return cursor.rowcount == 1

    async def list_trace_events(
        self,
        run_id: str,
        *,
        limit: int = 100,
        after: int = 0,
        event_type: str | None = None,
    ) -> list[TraceRecord]:
        if not 1 <= limit <= 200 or after < 0:
            raise ValueError("invalid trace pagination")
        await self.get_run(run_id)
        sql = "SELECT * FROM trace_events WHERE run_id = ? AND event_id > ?"
        parameters: list[object] = [run_id, after]
        if event_type is not None:
            sql += " AND event_type = ?"
            parameters.append(event_type)
        sql += " ORDER BY event_id ASC LIMIT ?"
        parameters.append(limit)
        async with self._lock:
            cursor = await self._connection.execute(sql, parameters)
            rows = await cursor.fetchall()
            await cursor.close()
        return [_trace_record(row) for row in rows]

    async def mark_cleanup(self, run_id: str, status: str, error: str | None = None) -> None:
        async with self._lock:
            cursor = await self._connection.execute(
                """
                UPDATE runs SET cleanup_status = ?, cleanup_error = ?, updated_at = ?
                WHERE run_id = ? AND deleted_at IS NULL
                """,
                (status, error, utc_now().isoformat(), run_id),
            )
            await self._connection.commit()
            if cursor.rowcount == 0:
                raise RunNotFoundError("run not found")

    async def delete_runtime_data(self, run_id: str) -> None:
        """Delete trace then soft-delete the registry row in one DB transaction."""

        now = utc_now().isoformat()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                await self._connection.execute(
                    "DELETE FROM trace_events WHERE run_id = ?", (run_id,)
                )
                cursor = await self._connection.execute(
                    """
                    UPDATE runs SET deleted_at = ?, cleanup_status = ?, updated_at = ?
                    WHERE run_id = ? AND deleted_at IS NULL
                    """,
                    (now, "complete", now, run_id),
                )
                if cursor.rowcount == 0:
                    raise RunNotFoundError("run not found")
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def expired_terminal_run_ids(self, retention_days: int) -> list[str]:
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat()
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT run_id FROM runs
                WHERE deleted_at IS NULL AND completed_at IS NOT NULL AND completed_at < ?
                ORDER BY completed_at ASC
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [str(row[0]) for row in rows]

    async def prune_terminal_trace_events(self, retention_days: int) -> int:
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat()
        async with self._lock:
            cursor = await self._connection.execute(
                """
                DELETE FROM trace_events
                WHERE run_id IN (
                    SELECT run_id FROM runs
                    WHERE deleted_at IS NULL AND completed_at IS NOT NULL
                ) AND created_at < ?
                """,
                (cutoff,),
            )
            await self._connection.commit()
            return cursor.rowcount


def _run_record(row: aiosqlite.Row) -> RunRecord:
    report_raw = row["final_report_json"]
    return RunRecord(
        run_id=row["run_id"],
        thread_id=row["thread_id"],
        state_schema_version=row["state_schema_version"],
        status=row["status"],
        outcome=row["outcome"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        current_proposal_id=row["current_proposal_id"],
        repair_attempts=row["repair_attempts"],
        max_repair_attempts=row["max_repair_attempts"],
        model_calls=row["model_calls"],
        latest_test_outcome=row["latest_test_outcome"],
        review_status=row["review_status"],
        goal_sha256=row["goal_sha256"],
        final_report=json.loads(report_raw) if report_raw else None,
        cleanup_status=row["cleanup_status"],
    )


def _trace_record(row: aiosqlite.Row) -> TraceRecord:
    return TraceRecord(
        event_id=row["event_id"],
        event_key=row["event_key"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        node_name=row["node_name"],
        phase=row["phase"],
        status=row["status"],
        created_at=row["created_at"],
        safe_payload=json.loads(row["safe_payload_json"]),
    )


def _encode_cursor(updated_at: str, run_id: str) -> str:
    raw = json.dumps([updated_at, run_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError
        return value[0], value[1]
    except Exception as exc:
        raise ValueError("invalid run cursor") from exc
