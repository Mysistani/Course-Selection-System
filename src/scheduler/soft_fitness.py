"""Independent, reportable soft objectives under a frozen evaluation policy."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .chromosome import Chromosome, SlotKey, validate_structure
from .constraints import StructuralValidationError
from .domain import Dataset
from .evaluation_context import EvaluationContext, resolve_context
from .policies import DEFAULT_POLICY, DesignPolicy


@dataclass(frozen=True)
class PreferenceScore:
    preference_type: str
    weight: int
    score: float
    raw_value: float
    normalizer: float
    status: str = 'active'


@dataclass(frozen=True)
class PersonSatisfaction:
    score: float | None  # None means excluded, never "dissatisfied".
    included: bool
    reason: str
    components: Mapping[str, PreferenceScore]
    actual_days: int
    excess_gap_minutes: int


@dataclass(frozen=True)
class SoftEvaluation:
    total_score: float
    request_coverage: float
    coverage_status: str
    request_count: int
    assigned_request_count: int
    served_priority: int
    total_priority: int
    student_satisfaction: float | None
    professor_satisfaction: float | None
    students: Mapping[str, PersonSatisfaction]
    professors: Mapping[str, PersonSatisfaction]
    context_id: tuple[str, str]
    policy_version: str
    aggregation: str


@dataclass(frozen=True)
class _Activity:
    course_id: str
    slot_key: SlotKey
    start: int
    end: int


def _unit_score(excess: int, normalizer: int) -> float:
    if normalizer <= 0:
        return 1.0 if excess == 0 else 0.0
    return max(0.0, min(1.0, 1 - excess / normalizer))


def _excess_gaps(activities: list[_Activity], free_minutes: int) -> int:
    by_day = defaultdict(list)
    for activity in activities:
        by_day[activity.slot_key[0]].append((activity.start, activity.end))
    excess = 0
    for intervals in by_day.values():
        # Merge overlap: a nested class cannot create an imaginary free gap.
        ordered = sorted(intervals)
        end = ordered[0][1]
        for start, next_end in ordered[1:]:
            excess += max(0, start - end - free_minutes)
            end = max(end, next_end)
    return excess


def aggregate_soft_scores(coverage: float, students: float | None,
                          professors: float | None, *, assigned_count: int,
                          policy: DesignPolicy = DEFAULT_POLICY) -> float:
    """Last user decision selects coverage * mean(defined satisfaction groups).

    equal_components is the explicitly named previous architectural alternative.
    Missing groups are excluded, not assigned an artificial dissatisfaction.
    """
    if assigned_count == 0:
        return 0.0
    groups = [score for score in (students, professors) if score is not None]
    if policy.soft_aggregation == 'coverage_product':
        # With no active preferences, coverage alone is the applicable objective.
        return coverage * (sum(groups) / len(groups) if groups else 1.0)
    return (coverage + sum(groups)) / (1 + len(groups))


def _person(person_id: str, activities: list[_Activity], role: str,
            context: EvaluationContext) -> PersonSatisfaction:
    preferences = (context.student_preferences if role == 'student' else context.professor_preferences).get(person_id, ())
    days = len({a.slot_key[0] for a in activities})
    gap = _excess_gaps(activities, context.policy.free_gap_minutes)
    if role == 'professor' and not activities:
        return PersonSatisfaction(None, False, 'inactive_professor', MappingProxyType({}), 0, 0)
    temporal = context.student_preferred_times if role == 'student' else context.professor_preferred_times
    components = {}
    for preference in preferences:
        kind, target = preference.preference_type, preference.target
        if kind == 'PreferredTeachingDays':
            raw, normalizer = abs(days - target), len(context.calendar_days)
            score = _unit_score(raw, normalizer)
        elif kind == 'MinimizeGaps':
            raw, normalizer = gap, context.gap_normalizer_minutes
            score = _unit_score(raw, normalizer)
        elif kind == 'PreferredTimeSlots':
            raw = sum(temporal.get((person_id, a.slot_key), context.policy.neutral_time_score) for a in activities)
            normalizer = len(activities)
            score = raw / normalizer if normalizer else 0.0
        elif kind == 'PreferredCourses':
            raw = sum((a.end - a.start) * context.willingness.get((person_id, a.course_id), 0.0) for a in activities)
            normalizer = sum(a.end - a.start for a in activities)
            score = raw / normalizer if normalizer else 0.0
        elif kind == 'SpreadDifficultCourses':
            by_day = defaultdict(set)
            for activity in activities:
                if activity.course_id in context.difficult_courses:
                    by_day[activity.slot_key[0]].add(activity.course_id)
            raw = sum(max(0, len(courses) - target) for courses in by_day.values())
            normalizer = context.difficult_normalizers.get(person_id, 0)
            score = _unit_score(raw, normalizer)
        else:
            raise ValueError(f'unsupported preference type: {kind}')
        status = 'active' if preference.weight else 'zero_weight'
        if not activities:
            score, status = 0.0, 'inactive_student'
        components[kind] = PreferenceScore(kind, preference.weight, score, raw, normalizer, status)
    weight = sum(p.weight for p in components.values())
    if weight == 0:
        return PersonSatisfaction(None, False, 'no_positive_preference_weights', MappingProxyType(components), days, gap)
    score = sum(p.weight * p.score for p in components.values()) / weight
    return PersonSatisfaction(score, True, 'included' if activities else 'inactive_student', MappingProxyType(components), days, gap)


def evaluate_soft_fitness(chromosome: Chromosome, dataset: Dataset | EvaluationContext, *,
                          context: EvaluationContext | None = None) -> SoftEvaluation:
    context = resolve_context(dataset, context)
    issues = validate_structure(chromosome, context)
    if issues:
        raise StructuralValidationError(issues)
    by_section = {}
    professor_activities, student_activities = defaultdict(list), defaultdict(list)
    for sid, section in sorted(chromosome.sections.items()):
        if not section.active:
            continue
        activities = []
        for key in section.meeting_slots:
            slot = context.slots[key]
            activities.append(_Activity(section.course_id, key, slot.start_time, slot.end_time))
        by_section[sid] = activities
        professor_activities[section.professor_id].extend(activities)
    served_priority = assigned = 0
    for request, section_id in chromosome.enrollments.items():
        if section_id is not None:
            assigned += 1
            served_priority += context.priorities[request]
            student_activities[request[0]].extend(by_section[section_id])
    coverage = served_priority / context.total_priority if context.total_priority else 0.0
    coverage_status = 'no_input_requests' if not context.requests else 'computed'
    students = {sid: _person(sid, student_activities[sid], 'student', context) for sid in context.students}
    professors = {pid: _person(pid, professor_activities[pid], 'professor', context) for pid in context.professors}

    def mean_defined(people):
        scores = [p.score for p in people.values() if p.included]
        return sum(scores) / len(scores) if scores else None

    student_score, professor_score = mean_defined(students), mean_defined(professors)
    score = aggregate_soft_scores(coverage, student_score, professor_score, assigned_count=assigned, policy=context.policy)
    return SoftEvaluation(score, coverage, coverage_status, len(context.requests), assigned,
                          served_priority, context.total_priority, student_score, professor_score,
                          MappingProxyType(students), MappingProxyType(professors), context.context_id,
                          context.policy.version, context.policy.soft_aggregation)
