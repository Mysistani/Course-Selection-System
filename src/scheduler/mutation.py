"""Atomic domain mutations. Probability gate and operator execution are separate."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import combinations, islice
import math
from random import Random
from types import MappingProxyType
from typing import Mapping

from .chromosome import Chromosome, RequestKey, SectionGene, SlotKey
from .constraints import StructuralValidationError
from .evaluation_context import EvaluationContext
from .initialization import chromosome_fingerprint
from .operator_support import (EvaluationCounts, EvaluationSnapshot, GeneDraft,
                               commit_draft, evaluate_snapshot, require_inputs, verify_snapshot)

OPERATIONS = ('MoveMeeting', 'SwapMeetings', 'ChangeProfessor', 'ChangeRoom',
              'SplitSection', 'MergeSections', 'ActivateDeactivate', 'MoveStudent',
              'SwapStudents', 'AssignUnassign')


@dataclass(frozen=True)
class MutationSettings:
    version: str = 'mutation-5b-v1'
    mutation_probability: float = .30
    targeted_probability: float = .70
    max_attempts: int = 8
    max_proposals_per_operator: int = 8
    operator_weights: tuple[tuple[str, float], ...] = tuple((op, 1.0) for op in OPERATIONS)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError('settings version must be non-empty text')
        object.__setattr__(self, 'operator_weights', tuple(tuple(pair) for pair in self.operator_weights))
        for name in ('mutation_probability', 'targeted_probability'):
            value = getattr(self, name)
            if type(value) not in (float, int) or not 0 <= value <= 1:
                raise ValueError(f'{name} must be in [0,1]')
        for name in ('max_attempts', 'max_proposals_per_operator'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')
        names = [op for op, _ in self.operator_weights]
        if len(names) != len(set(names)) or any(op not in OPERATIONS for op in names):
            raise ValueError('operator weights must have unique known names')
        if any(type(w) not in (int, float) or not math.isfinite(w) or w < 0 for _, w in self.operator_weights):
            raise ValueError('operator weights must be finite and non-negative')


@dataclass(frozen=True)
class MutationTarget:
    section_id: str | None = None
    other_section_id: str | None = None
    meeting_index: int | None = None
    other_meeting_index: int | None = None
    slot: SlotKey | None = None
    professor_id: str | None = None
    room_id: str | None = None
    request: RequestKey | None = None
    other_request: RequestKey | None = None
    action: str | None = None  # activate/deactivate or assign/unassign


@dataclass(frozen=True)
class MutationReport:
    status: str  # applied, not_applicable, failed, skipped
    operation: str | None
    reason: str
    attempts: int
    discovery_proposals: int
    targeted: bool
    fallback: bool
    rollbacks: int
    evaluations: EvaluationCounts
    unavailable_operations: Mapping[str, str]
    changed_sections: tuple[str, ...] = ()
    changed_requests: tuple[RequestKey, ...] = ()


@dataclass(frozen=True)
class MutationResult:
    chromosome: Chromosome
    report: MutationReport


class _NotApplicable(ValueError):
    pass


class _InvalidTarget(ValueError):
    pass


class _State:
    def __init__(self, chromosome, context):
        self.chromosome, self.context = chromosome, context
        self.active = tuple(sorted(sid for sid, s in chromosome.sections.items() if s.active))
        self.members = {sid: [] for sid in chromosome.sections}
        self.unassigned = defaultdict(list)
        for key, sid in sorted(chromosome.enrollments.items()):
            if sid is not None:
                self.members[sid].append(key)
            elif key not in context.passed:
                self.unassigned[key[1]].append(key)
        self.allowed = tuple(sorted(key for key, s in context.slots.items() if s.is_allowed))
        self.professors = defaultdict(list)
        for pid, cid in sorted(context.qualified):
            self.professors[cid].append(pid)

    def capacity(self, sid):
        section = self.chromosome.sections[sid]
        return self.context.effective_capacities[(section.course_id, section.room_id)]


def _require(condition, reason):
    if not condition:
        raise _NotApplicable(reason)


def _active(state, sid):
    section = state.chromosome.sections.get(sid)
    if section is None or not section.active:
        raise _InvalidTarget('target must be an existing active section')
    return section


def _new_group(state, sid, room, rng):
    context = state.context
    course_id = context.expected_sections[sid]
    course = context.courses[course_id]
    _require(bool(state.professors[course_id]), 'no qualified professor')
    _require(len(state.allowed) >= course.sessions_per_week, 'not enough allowed slots')
    return SectionGene(sid, course_id, True, rng.choice(state.professors[course_id]), room,
                       tuple(sorted(rng.sample(state.allowed, course.sessions_per_week))))


def _apply_draft(state, operation, target, rng):
    c, ctx = state.chromosome, state.context
    sections, enrollments = dict(c.sections), dict(c.enrollments)
    sid, other = target.section_id, target.other_section_id
    if operation == 'MoveMeeting':
        section = _active(state, sid)
        index = target.meeting_index
        if type(index) is not int or not 0 <= index < len(section.meeting_slots):
            raise _InvalidTarget('invalid meeting index')
        if target.slot not in state.allowed:
            raise _InvalidTarget('new slot is unknown or forbidden')
        slots = list(section.meeting_slots)
        _require(target.slot not in slots, 'new slot is duplicate or unchanged')
        slots[index] = target.slot
        sections[sid] = replace(section, meeting_slots=slots)
    elif operation == 'SwapMeetings':
        a, b = _active(state, sid), _active(state, other)
        i, j = target.meeting_index, target.other_meeting_index
        if type(i) is not int or type(j) is not int or not 0 <= i < len(a.meeting_slots) or not 0 <= j < len(b.meeting_slots):
            raise _InvalidTarget('invalid meeting indices')
        _require(sid != other, 'swapping meeting order in one group does not change genotype')
        av, bv = list(a.meeting_slots), list(b.meeting_slots)
        _require(av[i] != bv[j], 'meeting slots are identical')
        av[i], bv[j] = bv[j], av[i]
        _require(len(set(av)) == len(av) and len(set(bv)) == len(bv), 'swap would duplicate a meeting')
        _require(all(key in state.allowed for key in (av[i], bv[j])), 'swap uses a forbidden slot')
        sections[sid], sections[other] = replace(a, meeting_slots=av), replace(b, meeting_slots=bv)
    elif operation == 'ChangeProfessor':
        section = _active(state, sid)
        _require(target.professor_id != section.professor_id, 'professor unchanged')
        if (target.professor_id, section.course_id) not in ctx.qualified:
            raise _InvalidTarget('replacement professor is not qualified')
        sections[sid] = replace(section, professor_id=target.professor_id)
    elif operation == 'ChangeRoom':
        section = _active(state, sid)
        _require(target.room_id != section.room_id, 'room unchanged')
        if target.room_id not in ctx.compatible_rooms[section.course_id]:
            raise _InvalidTarget('replacement room is not compatible')
        _require(len(state.members[sid]) <= ctx.effective_capacities[(section.course_id, target.room_id)], 'replacement room lacks capacity')
        sections[sid] = replace(section, room_id=target.room_id)
    elif operation == 'SplitSection':
        source = _active(state, sid)
        destination = sections.get(other)
        _require(destination is not None and not destination.active and destination.course_id == source.course_id,
                 'need an inactive slot of the same course')
        minimum = ctx.courses[source.course_id].min_section_size
        n = len(state.members[sid])
        _require(n >= 2 * minimum, 'split needs at least two minimum-size groups')
        _require(target.room_id in ctx.compatible_rooms[source.course_id], 'no compatible destination room')
        cap = ctx.effective_capacities[(source.course_id, target.room_id)]
        low, high = max(minimum, n - cap), min(state.capacity(sid), n - minimum)
        _require(low <= high, 'no capacity-feasible partition')
        keep = rng.randint(low, high)
        members = list(state.members[sid])
        rng.shuffle(members)
        sections[other] = _new_group(state, other, target.room_id, rng)
        for key in members[keep:]:
            enrollments[key] = other
    elif operation == 'MergeSections':
        source, destination = _active(state, sid), _active(state, other)
        _require(sid != other and source.course_id == destination.course_id, 'merge needs two distinct groups of one course')
        total = len(state.members[sid]) + len(state.members[other])
        _require(ctx.courses[source.course_id].min_section_size <= total <= state.capacity(other), 'destination lacks capacity for merge')
        for key in state.members[sid]:
            enrollments[key] = other
        sections[sid] = SectionGene(sid, source.course_id)
    elif operation == 'ActivateDeactivate':
        section = sections.get(sid)
        if section is None:
            raise _InvalidTarget('unknown section')
        cid = section.course_id
        minimum = ctx.courses[cid].min_section_size
        if target.action == 'activate':
            _require(not section.active, 'section already active')
            _require(target.room_id in ctx.compatible_rooms[cid], 'no compatible activation room')
            requests = state.unassigned[cid]
            _require(len(requests) >= minimum, 'not enough unassigned requests to activate')
            cap = ctx.effective_capacities[(cid, target.room_id)]
            count = rng.randint(minimum, min(cap, len(requests)))
            ordered = sorted(requests, key=lambda key: (-ctx.priorities[key], rng.random()))
            sections[sid] = _new_group(state, sid, target.room_id, rng)
            for key in ordered[:count]:
                enrollments[key] = sid
        elif target.action == 'deactivate':
            _require(section.active, 'section already inactive')
            destinations = [s for s in state.active if s != sid and sections[s].course_id == cid
                            and minimum <= len(state.members[s]) <= state.capacity(s)]
            free = {s: state.capacity(s) - len(state.members[s]) for s in destinations}
            _require(sum(free.values()) >= len(state.members[sid]), 'cannot redistribute all source students')
            for key in state.members[sid]:
                choices = [s for s in destinations if free[s] > 0]
                destination = rng.choice(choices)
                enrollments[key] = destination
                free[destination] -= 1
            sections[sid] = SectionGene(sid, cid)
        else:
            raise _InvalidTarget('action must be activate or deactivate')
    elif operation in ('MoveStudent', 'SwapStudents'):
        request = target.request
        if request not in enrollments or enrollments[request] is None:
            raise _InvalidTarget('request must currently be assigned')
        source = enrollments[request]
        if operation == 'SwapStudents':
            second = target.other_request
            _require(second in enrollments and enrollments[second] is not None, 'second request must be assigned')
            destination = enrollments[second]
            _require(source != destination and request[1] == second[1], 'swap needs different groups of the same course')
            enrollments[request], enrollments[second] = destination, source
        else:
            destination = target.other_section_id
            section = _active(state, destination)
            _require(source != destination and section.course_id == request[1], 'destination must be another group of the same course')
            minimum = ctx.courses[request[1]].min_section_size
            _require(minimum <= len(state.members[source]) - 1 <= state.capacity(source), 'source would violate group bounds')
            _require(minimum <= len(state.members[destination]) + 1 <= state.capacity(destination), 'destination would violate group bounds')
            enrollments[request] = destination
    elif operation == 'AssignUnassign':
        request = target.request
        if request not in enrollments:
            raise _InvalidTarget('unknown request')
        minimum = ctx.courses[request[1]].min_section_size
        if target.action == 'assign':
            _require(enrollments[request] is None and request not in ctx.passed, 'request is already assigned or passed')
            section = _active(state, sid)
            _require(section.course_id == request[1], 'wrong course')
            _require(minimum <= len(state.members[sid]) + 1 <= state.capacity(sid), 'assignment violates group bounds')
            enrollments[request] = sid
        elif target.action == 'unassign':
            source = enrollments[request]
            _require(source is not None, 'request already unassigned')
            _require(minimum <= len(state.members[source]) - 1 <= state.capacity(source), 'unassignment violates group bounds')
            enrollments[request] = None
        else:
            raise _InvalidTarget('action must be assign or unassign')
    else:
        raise ValueError(f'unknown mutation operation: {operation}')
    return sections, enrollments


def _iter_targets(operation, state, rng):
    """Lazy proposals: no student-pair Cartesian product is materialized."""
    active = list(state.active)
    rng.shuffle(active)
    ctx, c = state.context, state.chromosome
    if operation == 'MoveMeeting':
        for sid in active:
            slots = list(state.allowed)
            rng.shuffle(slots)
            for index in range(len(c.sections[sid].meeting_slots)):
                for slot in slots:
                    if slot not in c.sections[sid].meeting_slots:
                        yield MutationTarget(section_id=sid, meeting_index=index, slot=slot)
    elif operation == 'SwapMeetings':
        for sid, other in combinations(active, 2):
            for i in range(len(c.sections[sid].meeting_slots)):
                for j in range(len(c.sections[other].meeting_slots)):
                    yield MutationTarget(sid, other, i, j)
    elif operation in ('ChangeProfessor', 'ChangeRoom', 'SplitSection'):
        for sid in active:
            section = c.sections[sid]
            if operation == 'ChangeProfessor':
                choices = [p for p in state.professors[section.course_id] if p != section.professor_id]
                rng.shuffle(choices)
                for pid in choices:
                    yield MutationTarget(section_id=sid, professor_id=pid)
            else:
                choices = sorted(ctx.compatible_rooms[section.course_id])
                rng.shuffle(choices)
                for rid in choices:
                    if operation == 'ChangeRoom':
                        if rid != section.room_id:
                            yield MutationTarget(section_id=sid, room_id=rid)
                    else:
                        inactive = [s for s, g in sorted(c.sections.items()) if not g.active and g.course_id == section.course_id]
                        rng.shuffle(inactive)
                        for other in inactive:
                            yield MutationTarget(sid, other, room_id=rid)
    elif operation == 'MergeSections':
        for sid in active:
            for other in active:
                if sid != other and c.sections[sid].course_id == c.sections[other].course_id:
                    yield MutationTarget(sid, other)
    elif operation == 'ActivateDeactivate':
        ids = sorted(c.sections)
        rng.shuffle(ids)
        for sid in ids:
            section = c.sections[sid]
            if section.active:
                yield MutationTarget(section_id=sid, action='deactivate')
            else:
                for rid in sorted(ctx.compatible_rooms[section.course_id]):
                    yield MutationTarget(section_id=sid, room_id=rid, action='activate')
    elif operation in ('MoveStudent', 'SwapStudents', 'AssignUnassign'):
        requests = sorted(c.enrollments)
        rng.shuffle(requests)
        for key in requests:
            source = c.enrollments[key]
            if operation == 'AssignUnassign':
                if source is not None:
                    yield MutationTarget(request=key, action='unassign')
                else:
                    for sid in active:
                        if c.sections[sid].course_id == key[1]:
                            yield MutationTarget(section_id=sid, request=key, action='assign')
            elif source is not None:
                for sid in active:
                    if sid != source and c.sections[sid].course_id == key[1]:
                        if operation == 'MoveStudent':
                            yield MutationTarget(other_section_id=sid, request=key)
                        elif state.members[sid]:
                            yield MutationTarget(request=key, other_request=rng.choice(state.members[sid]))


def violation_factors(violations, state):
    sections, students = set(), set()
    for violation in violations:
        sections.update(violation.section_ids)
        if violation.student_id:
            students.add(violation.student_id)
        if violation.professor_id or violation.room_id:
            sections.update(sid for sid in state.active if
                            (violation.professor_id and state.chromosome.sections[sid].professor_id == violation.professor_id) or
                            (violation.room_id and state.chromosome.sections[sid].room_id == violation.room_id))
    return sections, students


def target_matches(target, factors, state):
    sections, students = factors
    if target.section_id in sections or target.other_section_id in sections:
        return True
    for key in (target.request, target.other_request):
        if key is not None and (key[0] in students or state.chromosome.enrollments.get(key) in sections):
            return True
    return False


def _unchanged(chromosome, status, operation, reason, **values):
    defaults = dict(attempts=0, discovery_proposals=0, targeted=False, fallback=False, rollbacks=0,
                    evaluations=EvaluationCounts(), unavailable_operations=MappingProxyType({}))
    defaults.update(values)
    return MutationResult(chromosome.clone(), MutationReport(status, operation, reason, **defaults))


def _commit(chromosome, context, operation, sections, enrollments, **report):
    view = GeneDraft(sections, enrollments)
    if chromosome_fingerprint(view) == chromosome_fingerprint(chromosome):
        raise _NotApplicable('proposal does not change genotype')
    child = commit_draft(sections, enrollments, context)
    changed_sections = tuple(sid for sid in sorted(sections) if sections[sid] != chromosome.sections[sid])
    changed_requests = tuple(key for key in sorted(enrollments) if enrollments[key] != chromosome.enrollments[key])
    return MutationResult(child, MutationReport('applied', operation, 'atomic commit',
                          changed_sections=changed_sections, changed_requests=changed_requests, **report))


def apply_mutation(chromosome: Chromosome, context: EvaluationContext, rng: Random,
                   operation: str, target: MutationTarget | None = None,
                   settings: MutationSettings = MutationSettings()) -> MutationResult:
    """Unconditional one-operation API. No probability gate or fitness calls."""
    require_inputs(chromosome, context, rng)
    if operation not in OPERATIONS:
        raise ValueError('unknown operation')
    if target is not None and not isinstance(target, MutationTarget):
        raise TypeError('target must be a MutationTarget')
    state = _State(chromosome, context)
    proposals = (target,) if target is not None else islice(_iter_targets(operation, state, rng), settings.max_attempts)
    attempts, reason = 0, 'no applicable target found within the bounded search'
    for proposal in proposals:
        attempts += 1
        try:
            sections, enrollments = _apply_draft(state, operation, proposal, rng)
            return _commit(chromosome, context, operation, sections, enrollments,
                           attempts=attempts, discovery_proposals=attempts, targeted=False, fallback=False,
                           rollbacks=attempts - 1, evaluations=EvaluationCounts(), unavailable_operations=MappingProxyType({}))
        except _NotApplicable as exc:
            reason = str(exc)
        except (_InvalidTarget, StructuralValidationError) as exc:
            if target is not None:
                return _unchanged(chromosome, 'failed', operation, str(exc), attempts=attempts, discovery_proposals=attempts, rollbacks=attempts)
            reason = str(exc)
    return _unchanged(chromosome, 'not_applicable', operation, reason, attempts=attempts,
                      discovery_proposals=attempts, rollbacks=attempts)


def mutate(chromosome: Chromosome, context: EvaluationContext, rng: Random,
           settings: MutationSettings = MutationSettings(), *,
           evaluation: EvaluationSnapshot | None = None) -> MutationResult:
    """Choose an applicable operator and mutate; gate is only in maybe_mutate."""
    require_inputs(chromosome, context, rng)
    verify_snapshot(chromosome, context, evaluation)
    state = _State(chromosome, context)
    targeted = rng.random() < settings.targeted_probability
    baseline_calls = 0
    if targeted and evaluation is None:
        evaluation = evaluate_snapshot(chromosome, context)
        baseline_calls = 1
    factors = violation_factors(evaluation.fitness.hard.violations, state) if targeted else (set(), set())
    pools, unavailable, discovered = {}, {}, 0
    for operation, weight in settings.operator_weights:
        if not weight:
            continue
        plans = []
        for proposal in islice(_iter_targets(operation, state, rng), settings.max_proposals_per_operator):
            discovered += 1
            try:
                sections, enrollments = _apply_draft(state, operation, proposal, rng)
                plans.append((proposal, sections, enrollments))
            except (_NotApplicable, _InvalidTarget):
                continue
        if plans:
            pools[operation] = plans
        else:
            unavailable[operation] = 'no applicable proposal found within discovery budget'
    counts = EvaluationCounts(baseline=baseline_calls)
    reason, attempts = 'no applicable operator found within discovery budgets', 0
    fallback = False
    for _ in range(settings.max_attempts):
        if not pools:
            break
        attempts += 1
        names = list(pools)
        operation = rng.choices(names, [dict(settings.operator_weights)[name] for name in names], k=1)[0]
        plans = pools[operation]
        focused = [p for p in plans if target_matches(p[0], factors, state)] if targeted else []
        fallback = targeted and not focused
        plan = rng.choice(focused or plans)
        plans.remove(plan)
        if not plans:
            del pools[operation]
        try:
            return _commit(chromosome, context, operation, plan[1], plan[2], attempts=attempts,
                           discovery_proposals=discovered, targeted=targeted, fallback=fallback,
                           rollbacks=attempts - 1, evaluations=counts,
                           unavailable_operations=MappingProxyType(unavailable))
        except (_NotApplicable, StructuralValidationError) as exc:
            reason = str(exc)
    return _unchanged(chromosome, 'not_applicable' if attempts == 0 else 'failed', None, reason,
                      attempts=attempts, discovery_proposals=discovered, targeted=targeted, fallback=fallback,
                      rollbacks=attempts, evaluations=counts, unavailable_operations=MappingProxyType(unavailable))


def maybe_mutate(chromosome: Chromosome, context: EvaluationContext, rng: Random,
                 settings: MutationSettings = MutationSettings(), *,
                 evaluation: EvaluationSnapshot | None = None) -> MutationResult:
    require_inputs(chromosome, context, rng)
    verify_snapshot(chromosome, context, evaluation)
    if rng.random() >= settings.mutation_probability:
        return _unchanged(chromosome, 'skipped', None, 'mutation probability gate did not start')
    return mutate(chromosome, context, rng, settings, evaluation=evaluation)
