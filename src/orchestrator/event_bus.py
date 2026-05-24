"""Ordered event bus for orchestration run state."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class EventRecord:
    run_id: str
    sequence: int
    event_type: str
    producer: str
    payload: Dict[str, Any]
    terminal: bool
    timestamp: float


@dataclass(frozen=True)
class EventPublishResult:
    accepted: bool
    reason: str
    record: Optional[EventRecord]


@dataclass
class _RunEventState:
    last_sequence: int = -1
    events: List[EventRecord] = field(default_factory=list)
    seen: Dict[Tuple[int, str, str], EventRecord] = field(default_factory=dict)
    terminal: Optional[EventRecord] = None


class RunEventBus:
    """Serializes run events so retries cannot overwrite newer outcomes."""

    def __init__(self, max_history_per_run: int = 100):
        if max_history_per_run < 1:
            raise ValueError("max_history_per_run must be positive")
        self.max_history_per_run = max_history_per_run
        self._states: Dict[str, _RunEventState] = {}
        self._lock = asyncio.Lock()

    async def publish(
        self,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        producer: str = "runtime",
        terminal: bool = False,
    ) -> EventPublishResult:
        if not run_id:
            raise ValueError("run_id is required")
        if sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not event_type:
            raise ValueError("event_type is required")

        payload_copy = dict(payload or {})
        event_key = (sequence, event_type, producer)

        async with self._lock:
            state = self._states.setdefault(run_id, _RunEventState())

            if event_key in state.seen:
                return EventPublishResult(
                    accepted=False,
                    reason="duplicate_event",
                    record=state.seen[event_key],
                )

            if state.terminal is not None:
                return EventPublishResult(
                    accepted=False,
                    reason="terminal_outcome_recorded",
                    record=state.terminal,
                )

            if sequence <= state.last_sequence:
                previous = state.events[-1] if state.events else None
                return EventPublishResult(
                    accepted=False,
                    reason="out_of_order",
                    record=previous,
                )

            record = EventRecord(
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                producer=producer,
                payload=payload_copy,
                terminal=terminal,
                timestamp=time.time(),
            )
            state.events.append(record)
            state.seen[event_key] = record
            state.last_sequence = sequence

            if terminal:
                state.terminal = record

            if len(state.events) > self.max_history_per_run:
                state.events = state.events[-self.max_history_per_run:]

            return EventPublishResult(
                accepted=True,
                reason="accepted",
                record=record,
            )

    def events(self, run_id: str) -> List[EventRecord]:
        state = self._states.get(run_id)
        if state is None:
            return []
        return list(state.events)

    def terminal_outcome(self, run_id: str) -> Optional[EventRecord]:
        state = self._states.get(run_id)
        if state is None:
            return None
        return state.terminal
