"""Slot splitting logic for peer-review duty slots and self-bookings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from bot.core.utils import parse_iso_utc, utc_iso
from bot.database.models import (
    EVENT_TYPE_PEER_REVIEW,
    EVENT_TYPE_SLOT,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    STATUS_BOOKED,
    STATUS_OPEN,
    CalendarSnapshotItem,
)

logger = logging.getLogger(__name__)


@dataclass
class SlotInterval:
    """Represents a time interval for splitting calculations."""

    start: datetime
    end: datetime
    s21_event_id: str
    event_slot_id: Optional[str] = None
    role: str = ROLE_EVALUATOR
    status: str = STATUS_OPEN
    data: dict[str, Any] = field(default_factory=dict)
    item_type: str = EVENT_TYPE_SLOT


@dataclass
class SplitPlan:
    """Instructions on how to synchronize split slots with DB and API."""

    # Original open slot modified in-place: (event_slot_id, s21_event_id, new_start_utc, new_end_utc)
    slots_to_update: list[dict[str, Any]] = field(default_factory=list)
    # Additional open slots that must be created: (new_start_utc, new_end_utc)
    slots_to_create: list[dict[str, Any]] = field(default_factory=list)
    # Original slots that were completely eclipsed and should be deleted: (event_slot_id, s21_event_id)
    slots_to_delete: list[dict[str, Any]] = field(default_factory=list)
    # Complete list of normalized CalendarSnapshotItems after splitting
    normalized_items: list[CalendarSnapshotItem] = field(default_factory=list)


def calculate_slot_splits(
    items: list[CalendarSnapshotItem],
) -> SplitPlan:
    """
    Split open EVALUATOR slots when they collide with EVALUATED bookings.

    Example:
      Open slot: 19:00 - 22:00 (ROLE_EVALUATOR, STATUS_OPEN)
      Booking:   19:30 - 20:00 (ROLE_EVALUATED, STATUS_BOOKED)
    Result:
      a) Evaluator slot: 19:00 - 19:30 (ROLE_EVALUATOR, STATUS_OPEN)
      b) Evaluated slot: 19:30 - 20:00 (ROLE_EVALUATED, STATUS_BOOKED)
      c) Evaluator slot: 20:00 - 22:00 (ROLE_EVALUATOR, STATUS_OPEN)
    """
    open_slots: list[SlotInterval] = []
    bookings: list[SlotInterval] = []
    other_items: list[CalendarSnapshotItem] = []

    for it in items:
        try:
            start_dt = parse_iso_utc(it.start_time)
            end_dt = parse_iso_utc(it.end_time) if it.end_time else None
        except Exception:
            other_items.append(it)
            continue

        if not end_dt or end_dt <= start_dt:
            other_items.append(it)
            continue

        role = it.role or (it.data or {}).get("role") or ROLE_EVALUATOR
        slot_id = (it.data or {}).get("event_slot_id")

        if it.type == EVENT_TYPE_SLOT and it.status == STATUS_OPEN and role == ROLE_EVALUATOR:
            open_slots.append(
                SlotInterval(
                    start=start_dt,
                    end=end_dt,
                    s21_event_id=it.s21_event_id,
                    event_slot_id=slot_id,
                    role=ROLE_EVALUATOR,
                    status=STATUS_OPEN,
                    data=dict(it.data or {}),
                    item_type=EVENT_TYPE_SLOT,
                )
            )
        elif it.type == EVENT_TYPE_PEER_REVIEW and it.status == STATUS_BOOKED and role == ROLE_EVALUATED:
            bookings.append(
                SlotInterval(
                    start=start_dt,
                    end=end_dt,
                    s21_event_id=it.s21_event_id,
                    event_slot_id=slot_id,
                    role=ROLE_EVALUATED,
                    status=STATUS_BOOKED,
                    data=dict(it.data or {}),
                    item_type=EVENT_TYPE_PEER_REVIEW,
                )
            )
        else:
            other_items.append(it)

    if not open_slots or not bookings:
        # No splitting needed
        return SplitPlan(normalized_items=list(items))

    plan = SplitPlan()
    final_open_slots: list[SlotInterval] = []

    for slot in open_slots:
        # Find all evaluated bookings that overlap with this open slot
        overlapping_bookings = [
            b for b in bookings if max(slot.start, b.start) < min(slot.end, b.end)
        ]
        if not overlapping_bookings:
            final_open_slots.append(slot)
            continue

        # Sort overlapping bookings chronologically
        overlapping_bookings.sort(key=lambda b: b.start)

        current_start = slot.start
        pieces: list[tuple[datetime, datetime]] = []

        for b in overlapping_bookings:
            if b.start > current_start:
                pieces.append((current_start, min(b.start, slot.end)))
            current_start = max(current_start, b.end)

        if current_start < slot.end:
            pieces.append((current_start, slot.end))

        if not pieces:
            # Slot is completely consumed by bookings
            plan.slots_to_delete.append(
                {
                    "event_slot_id": slot.event_slot_id or slot.s21_event_id,
                    "s21_event_id": slot.s21_event_id,
                }
            )
            continue

        # First piece modifies the original slot in-place
        first_piece = pieces[0]
        plan.slots_to_update.append(
            {
                "event_slot_id": slot.event_slot_id or slot.s21_event_id,
                "s21_event_id": slot.s21_event_id,
                "new_start_utc": utc_iso(first_piece[0]),
                "new_end_utc": utc_iso(first_piece[1]),
                "original_slot": slot,
            }
        )
        first_interval = SlotInterval(
            start=first_piece[0],
            end=first_piece[1],
            s21_event_id=slot.s21_event_id,
            event_slot_id=slot.event_slot_id,
            role=ROLE_EVALUATOR,
            status=STATUS_OPEN,
            data=dict(slot.data),
            item_type=EVENT_TYPE_SLOT,
        )
        final_open_slots.append(first_interval)

        # Subsequent pieces require creating new slots
        for i, extra_piece in enumerate(pieces[1:], start=2):
            extra_event_id = f"{slot.s21_event_id}_split_{i}"
            plan.slots_to_create.append(
                {
                    "new_start_utc": utc_iso(extra_piece[0]),
                    "new_end_utc": utc_iso(extra_piece[1]),
                    "s21_event_id": extra_event_id,
                    "parent_slot": slot,
                }
            )
            extra_data = dict(slot.data)
            extra_data["event_slot_id"] = None
            final_open_slots.append(
                SlotInterval(
                    start=extra_piece[0],
                    end=extra_piece[1],
                    s21_event_id=extra_event_id,
                    event_slot_id=None,
                    role=ROLE_EVALUATOR,
                    status=STATUS_OPEN,
                    data=extra_data,
                    item_type=EVENT_TYPE_SLOT,
                )
            )

    # Reconstruct normalized items
    result_items: list[CalendarSnapshotItem] = list(other_items)

    for b in bookings:
        result_items.append(
            CalendarSnapshotItem(
                s21_event_id=b.s21_event_id,
                type=b.item_type,
                status=b.status,
                start_time=utc_iso(b.start),
                end_time=utc_iso(b.end),
                role=b.role,
                data=b.data,
            )
        )

    for s in final_open_slots:
        result_items.append(
            CalendarSnapshotItem(
                s21_event_id=s.s21_event_id,
                type=s.item_type,
                status=s.status,
                start_time=utc_iso(s.start),
                end_time=utc_iso(s.end),
                role=s.role,
                data=s.data,
            )
        )

    # Sort all items chronologically
    result_items.sort(key=lambda it: it.start_time)
    plan.normalized_items = result_items
    return plan
