"""Candidate timetable plus student assignments, independent of Excel size."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Protocol, TYPE_CHECKING

from .domain import Dataset

if TYPE_CHECKING:
    from .evaluation_context import EvaluationContext

SlotKey = tuple[str, str]
RequestKey = tuple[str, str]


class GeneMappings(Protocol):
    """Read-only view accepted by validation before an atomic commit."""
    @property
    def sections(self) -> Mapping[str, SectionGene]: ...

    @property
    def enrollments(self) -> Mapping[RequestKey, str | None]: ...


@dataclass(frozen=True)
class SectionGene:
    section_id: str
    course_id: str
    active: bool = False
    professor_id: str | None = None
    room_id: str | None = None
    meeting_slots: tuple[SlotKey, ...] = ()

    def __post_init__(self):
        # Accept the user's list-of-pairs representation, store immutable pairs.
        slots = tuple(self.meeting_slots)
        if any(not isinstance(pair, (tuple, list)) or len(pair) != 2
               or any(not isinstance(part, str) for part in pair) for pair in slots):
            raise ValueError('meeting_slots must contain (day, time_slot) string pairs')
        object.__setattr__(self, 'meeting_slots', tuple(tuple(pair) for pair in slots))
        if not isinstance(self.section_id, str) or not self.section_id:
            raise ValueError('section_id must be non-empty text')
        if not isinstance(self.course_id, str) or not self.course_id:
            raise ValueError('course_id must be non-empty text')
        if type(self.active) is not bool:
            raise ValueError('active must be a Boolean')
        for name in ('professor_id', 'room_id'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f'{name} must be non-empty text or None')


@dataclass(frozen=True)
class StructureIssue:
    location: str
    message: str


class Chromosome:
    """Controlled mutations invalidate scores; public mappings are read-only.

    Construction may represent a structurally invalid candidate for diagnosis.
    Call validate_structure before evaluation, not a feasibility check.
    """

    def __init__(self, sections: Mapping[str, SectionGene],
                 enrollments: Mapping[RequestKey, str | None]):
        sections, enrollments = dict(sections), dict(enrollments)
        if any(not isinstance(sid, str) or not isinstance(section, SectionGene)
               for sid, section in sections.items()):
            raise TypeError('sections must map string keys to SectionGene records')
        if any(not isinstance(key, tuple) or len(key) != 2
               or any(not isinstance(part, str) or not part for part in key)
               or (sid is not None and (not isinstance(sid, str) or not sid))
               for key, sid in enrollments.items()):
            raise TypeError('enrollments must map (StudentID, CourseID) to SectionID or None')
        # Own immutable snapshots, even for callers retaining the input dicts.
        self._sections = MappingProxyType(sections)
        self._enrollments = MappingProxyType(enrollments)
        self._hard_score: float | None = None
        self._soft_score: float | None = None

    @property
    def sections(self) -> Mapping[str, SectionGene]:
        return MappingProxyType(self._sections)

    @property
    def enrollments(self) -> Mapping[RequestKey, str | None]:
        return MappingProxyType(self._enrollments)

    @property
    def hard_score(self) -> float | None:
        return self._hard_score

    @property
    def soft_score(self) -> float | None:
        return self._soft_score

    def invalidate_evaluation(self) -> None:
        self._hard_score = self._soft_score = None

    def record_evaluation(self, hard_score: float, soft_score: float) -> None:
        """Future evaluator entry point; storing scores does not prove validity."""
        if any(type(v) not in (int, float) or not math.isfinite(v)
               for v in (hard_score, soft_score)) or hard_score < 0 or not 0 <= soft_score <= 1:
            raise ValueError('expected finite hard_score >= 0 and soft_score in [0, 1]')
        self._hard_score, self._soft_score = float(hard_score), float(soft_score)

    def clone(self) -> Chromosome:
        # A child needs its own evaluation; never inherit stale parent scores.
        return Chromosome(deepcopy(dict(self._sections)), deepcopy(dict(self._enrollments)))

    def replace_section(self, section: SectionGene) -> None:
        """Replace one existing stable slot, without silently changing its course."""
        if not isinstance(section, SectionGene):
            raise TypeError('expected a SectionGene')
        previous = self._sections[section.section_id]
        if section.course_id != previous.course_id:
            raise ValueError('an existing section slot cannot change course')
        self.invalidate_evaluation()
        self._sections = MappingProxyType({**self._sections, section.section_id: section})

    def assign(self, request: RequestKey, section_id: str | None) -> None:
        if request not in self._enrollments:
            raise KeyError(f'unknown request: {request}')
        if section_id is not None:
            section = self._sections[section_id]
            if not section.active or section.course_id != request[1]:
                raise ValueError('assignment needs an active section of the requested course')
        self.invalidate_evaluation()
        self._enrollments = MappingProxyType({**self._enrollments, request: section_id})

    def section_sizes(self) -> dict[str, int]:
        counts = Counter(sid for sid in self._enrollments.values() if sid is not None)
        return {sid: counts[sid] for sid in self._sections}


def _request_keys(dataset: Dataset) -> tuple[RequestKey, ...]:
    """Defend against manually constructed Datasets bypassing the loader."""
    keys = tuple((r.student_id, r.course_id) for r in dataset.tables['StudentRequests'])
    seen = set()
    for key in keys:
        if key in seen:
            raise ValueError(f'duplicate student request in dataset: {key}')
        seen.add(key)
    return keys


def _expected_section_slots(dataset: Dataset) -> dict[str, str]:
    courses = dataset.courses
    demand = Counter(cid for _, cid in _request_keys(dataset))
    if set(demand) - set(courses):
        raise ValueError('dataset contains a request for an unknown course')
    slots = {}
    for cid in sorted(courses):
        minimum = courses[cid].min_section_size
        if type(minimum) is not int or minimum <= 0:
            raise ValueError('minimum section size must be a positive integer')
        for ordinal in range(1, demand[cid] // minimum + 1):
            sid = f'{cid}-{ordinal:02d}'
            if sid in slots:
                raise ValueError(f'duplicate section slot: {sid}')
            slots[sid] = cid
    return slots


def build_empty_chromosome(dataset: Dataset, *,
                           section_limits: Mapping[str, int] | None = None) -> Chromosome:
    """Build inactive stable slots, keeping every input request unassigned.

    Slots are floor(demand/minimum size), derived once by the same rule used
    in structural validation. Legacy section_limits may only repeat that rule.
    This creates a structure, not a random population or feasible schedule.
    """
    from .evaluation_context import get_evaluation_context
    context = get_evaluation_context(dataset)
    courses = context.courses
    limits = {} if section_limits is None else dict(section_limits)
    if set(limits) - set(courses):
        raise ValueError('section_limits contains an unknown course')
    if any(type(n) is not int or n < 0 for n in limits.values()):
        raise ValueError('section limits must be non-negative integers')
    slots = context.expected_sections
    if limits:
        counts = Counter(slots.values())
        if any(count != counts[cid] for cid, count in limits.items()):
            raise ValueError('section_limits cannot override dataset-defined stable slots')
    sections = {sid: SectionGene(sid, cid) for sid, cid in slots.items()}
    return Chromosome(sections, {key: None for key in sorted(context.requests)})


def validate_structure(chromosome: GeneMappings, dataset: Dataset | EvaluationContext, *,
                       context: EvaluationContext | None = None) -> tuple[StructureIssue, ...]:
    """Validate references and meeting structure, not capacity or timetable fitness."""
    issues = []

    def report(location, message):
        issues.append(StructureIssue(location, message))

    from .evaluation_context import resolve_context
    context = resolve_context(dataset, context)
    courses, professors = context.courses, context.professors
    rooms, slots = context.rooms, context.slots
    requests, expected_slots = context.requests, context.expected_sections
    for sid in sorted(expected_slots.keys() - chromosome.sections.keys()):
        report(f'sections[{sid}]', 'dataset-defined section slot is missing')
    for sid in sorted(chromosome.sections.keys() - expected_slots.keys()):
        report(f'sections[{sid}]', 'extra section slot not defined by dataset')
    seen_ids = set()
    for sid, section in chromosome.sections.items():
        location = f'sections[{sid}]'
        if not isinstance(section, SectionGene):
            report(location, 'expected a SectionGene')
            continue
        if sid != section.section_id:
            report(location, 'dictionary key differs from section_id')
        if section.section_id in seen_ids:
            report(location, 'duplicate section_id')
        seen_ids.add(section.section_id)
        if sid in expected_slots and section.course_id != expected_slots[sid]:
            report(location, 'section slot belongs to a different course')
        if section.course_id not in courses:
            report(location, 'unknown course')
            continue
        if not section.active:
            if section.professor_id is not None or section.room_id is not None or section.meeting_slots:
                report(location, 'inactive section must have no professor, room or meetings')
            continue
        if section.professor_id not in professors:
            report(location, 'active section needs an existing professor')
        if section.room_id not in rooms:
            report(location, 'active section needs an existing room')
        if len(section.meeting_slots) != courses[section.course_id].sessions_per_week:
            report(location, 'incorrect number of weekly meetings')
        if len(set(section.meeting_slots)) != len(section.meeting_slots):
            report(location, 'duplicate meeting slot')
        if any(slot not in slots for slot in section.meeting_slots):
            report(location, 'unknown meeting slot')
    actual_requests = set(chromosome.enrollments)
    for request in sorted(requests - actual_requests):
        report(f'enrollments[{request}]', 'input request is missing; keep it with None when unassigned')
    for request in actual_requests - requests:
        report(f'enrollments[{request}]', 'request does not exist in input')
    for request, sid in chromosome.enrollments.items():
        if sid is None:
            continue
        section = chromosome.sections.get(sid)
        location = f'enrollments[{request}]'
        if not isinstance(section, SectionGene):
            report(location, 'assignment references an unknown section')
        elif request in requests and section.course_id != request[1]:
            report(location, 'assignment references a section of another course')
        elif not section.active:
            report(location, 'assignment references an inactive section')
    return tuple(issues)
