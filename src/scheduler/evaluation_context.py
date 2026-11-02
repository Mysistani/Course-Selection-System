"""Immutable run data and one-time preparation shared by hard and soft evaluation."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Mapping
import weakref

from .chromosome import RequestKey, SlotKey, _expected_section_slots, _request_keys
from .constraints import Diagnostic
from .domain import Course, Dataset, Preference, Professor, Room, Slot, Student
from .policies import DEFAULT_POLICY, DesignPolicy


@dataclass(frozen=True, eq=False)
class EvaluationContext:
    dataset: Dataset
    policy: DesignPolicy
    dataset_fingerprint: str
    policy_fingerprint: str
    courses: Mapping[str, Course]
    students: Mapping[str, Student]
    professors: Mapping[str, Professor]
    rooms: Mapping[str, Room]
    slots: Mapping[SlotKey, Slot]
    requests: frozenset[RequestKey]
    priorities: Mapping[RequestKey, int]
    total_priority: int
    demand: Mapping[str, int]
    expected_sections: Mapping[str, str]
    passed: frozenset[RequestKey]
    qualified: frozenset[tuple[str, str]]
    compatible_rooms: Mapping[str, frozenset[str]]
    effective_capacities: Mapping[tuple[str, str], int]
    professor_min_minutes: Mapping[str, int]
    professor_max_minutes: Mapping[str, int]
    professor_unavailable: Mapping[tuple[str, str], tuple[tuple[int, int], ...]]
    student_unavailable: Mapping[tuple[str, str], tuple[tuple[int, int], ...]]
    student_preferences: Mapping[str, tuple[Preference, ...]]
    professor_preferences: Mapping[str, tuple[Preference, ...]]
    student_preferred_times: Mapping[tuple[str, SlotKey], float]
    professor_preferred_times: Mapping[tuple[str, SlotKey], float]
    willingness: Mapping[tuple[str, str], float]
    difficult_courses: frozenset[str]
    difficult_normalizers: Mapping[str, int]
    calendar_days: tuple[str, ...]
    gap_normalizer_minutes: int
    diagnostics: tuple[Diagnostic, ...]
    _source: weakref.ReferenceType[Dataset] = field(repr=False)

    @classmethod
    def from_dataset(cls, dataset: Dataset, policy: DesignPolicy = DEFAULT_POLICY) -> EvaluationContext:
        return get_evaluation_context(dataset, policy)

    @property
    def context_id(self) -> tuple[str, str]:
        return self.dataset_fingerprint, self.policy_fingerprint

    def accepts(self, dataset: Dataset) -> bool:
        return dataset is self.dataset or dataset is self._source()


# Identity-based weak cache: no per-candidate table scans or fingerprints.
# Context owns a snapshot rather than a strong reference back to the cache key.
_CACHE: dict[int, tuple[weakref.ReferenceType[Dataset], dict[DesignPolicy, EvaluationContext]]] = {}


def get_evaluation_context(dataset: Dataset, policy: DesignPolicy = DEFAULT_POLICY) -> EvaluationContext:
    if not isinstance(dataset, Dataset) or not isinstance(policy, DesignPolicy):
        raise TypeError('expected Dataset and DesignPolicy')
    identity = id(dataset)
    cached = _CACHE.get(identity)
    if cached is not None and cached[0]() is dataset and policy in cached[1]:
        return cached[1][policy]
    context = _build_context(dataset, policy)
    if cached is None or cached[0]() is not dataset:
        def discard(reference, key=identity):
            current = _CACHE.get(key)
            if current is not None and current[0] is reference:
                del _CACHE[key]
        cached = weakref.ref(dataset, discard), {}
        _CACHE[identity] = cached
    cached[1][policy] = context
    return context


def resolve_context(dataset: Dataset | EvaluationContext,
                    context: EvaluationContext | None = None) -> EvaluationContext:
    if isinstance(dataset, EvaluationContext):
        if context is not None and context is not dataset:
            raise ValueError('cannot replace EvaluationContext during an evaluation run')
        return dataset
    if context is None:
        return get_evaluation_context(dataset)
    if not context.accepts(dataset):
        raise ValueError('Dataset differs from the frozen EvaluationContext; start a new run')
    return context


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _build_context(source: Dataset, policy: DesignPolicy) -> EvaluationContext:
    # Domain rows and policy are frozen; container copies detach caller aliases.
    dataset = Dataset(source.tables, source.sha256, source.warnings)
    courses = MappingProxyType(dict(sorted(dataset.courses.items())))
    students = MappingProxyType(dict(sorted(dataset.students.items())))
    professors = MappingProxyType(dict(sorted(dataset.professors.items())))
    rooms = {r.room_id: r for r in dataset.tables['Classrooms']}
    slots = {(s.day, s.time_slot): s for s in dataset.tables['TimeSlots']}
    request_keys = frozenset(_request_keys(dataset))
    expected = _expected_section_slots(dataset)
    priorities = {(r.student_id, r.course_id): r.priority for r in dataset.tables['StudentRequests']}
    demand = Counter(cid for _, cid in request_keys)
    passed = frozenset((r.student_id, r.course_id) for r in dataset.tables.get('PassedCourses', ()))
    qualified = frozenset((q.professor_id, q.course_id) for q in dataset.tables.get('ProfessorCourses', ()) if q.can_teach)
    qualified_courses = {cid for _, cid in qualified}
    capacities = {(cid, rid): min(c.max_section_size, r.capacity)
                  for cid, c in courses.items() for rid, r in rooms.items()}
    compatible = {cid: frozenset(rid for rid, r in rooms.items() if r.room_type == c.required_room_type
                                 and capacities[(cid, rid)] >= c.min_section_size)
                  for cid, c in courses.items()}
    blocked, preferred, preferences = {}, {}, {}
    for role in ('Student', 'Professor'):
        intervals, desired = defaultdict(list), {}
        for row in dataset.tables.get(role + 'Availability', ()):
            slot_key = row.day, row.time_slot
            slot = slots[slot_key]
            if row.status == 'Unavailable':
                intervals[(row.person_id, row.day)].append((slot.start_time, slot.end_time))
            elif row.status == 'Preferred':
                desired[(row.person_id, slot_key)] = row.weight / policy.availability_weight_scale
        blocked[role] = MappingProxyType({key: tuple(value) for key, value in intervals.items()})
        preferred[role] = MappingProxyType(desired)
        person_preferences = defaultdict(list)
        for row in dataset.tables.get(role + 'Preferences', ()):
            person_preferences[row.person_id].append(row)
        preferences[role] = MappingProxyType({key: tuple(value) for key, value in person_preferences.items()})
    difficult = frozenset(cid for cid, c in courses.items() if c.difficulty_level >= policy.difficult_course_threshold)
    difficult_normalizers = dict.fromkeys(students, 0)
    eligible_credits = Counter()
    for sid, cid in request_keys:
        if cid in difficult:
            difficult_normalizers[sid] += courses[cid].sessions_per_week
        if demand[cid] >= courses[cid].min_section_size and (sid, cid) not in passed:
            eligible_credits[sid] += courses[cid].credits

    day_slots = defaultdict(list)
    for slot in slots.values():
        day_slots[slot.day].append(slot)
    calendar_bound = sum(max(0, max(s.end_time for s in values) - min(s.start_time for s in values)
                             - 2 * min(s.end_time - s.start_time for s in values) - policy.free_gap_minutes)
                         for values in day_slots.values())
    gap_bound = policy.gap_normalizer_minutes if policy.gap_normalizer_minutes is not None else calendar_bound
    diagnostics = []
    for cid, course in sorted(courses.items()):
        if demand[cid] < course.min_section_size:
            diagnostics.append(Diagnostic('DATASET_LOW_DEMAND', (cid,), f'{demand[cid]} requests; section minimum {course.min_section_size}'))
        if cid not in qualified_courses:
            diagnostics.append(Diagnostic('DATASET_NO_QUALIFIED_PROFESSOR', (cid,), 'No qualified professor in sample'))
        if not compatible[cid]:
            diagnostics.append(Diagnostic('DATASET_NO_COMPATIBLE_ROOM', (cid,), 'No sufficiently large compatible room in sample'))
    for sid, student in sorted(students.items()):
        if eligible_credits[sid] < student.min_credits:
            diagnostics.append(Diagnostic('DATASET_CREDIT_UPPER_BOUND_BELOW_MINIMUM', (sid,),
                                          f'At most {eligible_credits[sid]} credits before resource/time constraints; minimum {student.min_credits}'))

    return EvaluationContext(
        dataset=dataset, policy=policy,
        dataset_fingerprint=_fingerprint({name: [asdict(row) for row in rows] for name, rows in dataset.tables.items()}),
        policy_fingerprint=_fingerprint(asdict(policy)), courses=courses, students=students, professors=professors,
        rooms=MappingProxyType(rooms), slots=MappingProxyType(slots), requests=request_keys,
        priorities=MappingProxyType(priorities), total_priority=sum(priorities.values()),
        demand=MappingProxyType({cid: demand[cid] for cid in courses}), expected_sections=MappingProxyType(expected), passed=passed,
        qualified=qualified, compatible_rooms=MappingProxyType(compatible), effective_capacities=MappingProxyType(capacities),
        professor_min_minutes=MappingProxyType({pid: p.min_minutes for pid, p in professors.items()}),
        professor_max_minutes=MappingProxyType({pid: p.max_minutes for pid, p in professors.items()}),
        professor_unavailable=blocked['Professor'], student_unavailable=blocked['Student'],
        student_preferences=preferences['Student'], professor_preferences=preferences['Professor'],
        student_preferred_times=preferred['Student'], professor_preferred_times=preferred['Professor'],
        willingness=MappingProxyType({(q.professor_id, q.course_id): q.willingness / policy.willingness_scale
                                      for q in dataset.tables.get('ProfessorCourses', ())}),
        difficult_courses=difficult, difficult_normalizers=MappingProxyType(difficult_normalizers),
        calendar_days=tuple(sorted(day_slots)), gap_normalizer_minutes=gap_bound,
        diagnostics=tuple(diagnostics), _source=weakref.ref(source),
    )
