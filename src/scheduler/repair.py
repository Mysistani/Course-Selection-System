"""Bounded structural recovery and strictly improving local conflict repair."""
from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random

from .chromosome import Chromosome, SectionGene, validate_structure
from .constraints import StructureIssue, StructuralValidationError
from .evaluation_context import EvaluationContext
from .fitness import evaluate_fitness
from .initialization import chromosome_fingerprint
from .mutation import (_State, _NotApplicable, _InvalidTarget, _apply_draft,
                       _iter_targets, target_matches, violation_factors)
from .operator_support import (EvaluationCounts, EvaluationSnapshot, commit_draft,
                               evaluate_snapshot, require_inputs, verify_snapshot)


@dataclass(frozen=True)
class RepairSettings:
    version: str = 'repair-5b-v1'
    max_local_candidates: int = 5
    max_candidate_evaluations: int = 50
    max_candidate_attempts: int = 100
    max_conflicts: int = 50
    max_structural_steps: int = 20

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError('settings version must be non-empty text')
        for name in ('max_local_candidates', 'max_candidate_evaluations',
                     'max_candidate_attempts', 'max_conflicts', 'max_structural_steps'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f'{name} must be a non-negative integer')


@dataclass(frozen=True)
class RepairReport:
    status: str
    reason: str
    attempts: int = 0
    accepted: int = 0
    rollbacks: int = 0
    evaluations: EvaluationCounts = EvaluationCounts()
    candidates_per_conflict: tuple[int, ...] = ()
    hard_before: int | None = None
    hard_after: int | None = None
    issues: tuple[StructureIssue, ...] = ()


@dataclass(frozen=True)
class RepairResult:
    # None explicitly means unresolved structural failure, never a valid answer.
    chromosome: Chromosome | None
    report: RepairReport
    evaluation: EvaluationSnapshot | None = None


def repair_structure(chromosome: Chromosome, context: EvaluationContext, rng: Random,
                     settings: RepairSettings = RepairSettings(), *,
                     rollback: Chromosome | None = None) -> RepairResult:
    """Conservative reference/slot repair. Missing active resources require rollback.

    Does not invent an educational assignment or choose substitute resources.
    Every changed mapping consumes one step; all changes commit together.
    """
    require_inputs(chromosome, context, rng, structural=False)
    if rollback is not None:
        require_inputs(rollback, context, rng)
    original = validate_structure(chromosome, context)
    if not original:
        return RepairResult(chromosome.clone(), RepairReport('unchanged', 'already structurally valid'))
    sections, enrollments = dict(chromosome.sections), dict(chromosome.enrollments)
    steps = 0

    def update(mapping, key, value=None, *, remove=False):
        nonlocal steps
        if steps >= settings.max_structural_steps:
            raise _NotApplicable('structural step budget exhausted')
        if remove:
            del mapping[key]
        else:
            mapping[key] = value
        steps += 1

    try:
        for sid in sorted(set(sections) - context.expected_sections.keys()):
            update(sections, sid, remove=True)
        for sid, cid in sorted(context.expected_sections.items()):
            s = sections.get(sid)
            if s is None:
                update(sections, sid, SectionGene(sid, cid))
            elif not isinstance(s, SectionGene) or s.course_id != cid:
                raise _NotApplicable('cannot infer course/resource intent of malformed section')
            elif not s.active:
                clean = SectionGene(sid, cid)
                if s != clean:
                    update(sections, sid, clean)
            elif s.section_id != sid:
                update(sections, sid, replace(s, section_id=sid))
        for key in sorted(set(enrollments) - context.requests):
            update(enrollments, key, remove=True)
        for key in sorted(context.requests):
            sid = enrollments.get(key)
            s = sections.get(sid)
            if key not in enrollments or (sid is not None and
                    (s is None or not s.active or s.course_id != key[1])):
                update(enrollments, key, None)
        child = commit_draft(sections, enrollments, context)
        return RepairResult(child, RepairReport('repaired', 'atomic structural commit', attempts=steps,
                                               accepted=1, issues=original))
    except (_NotApplicable, StructuralValidationError) as exc:
        restored = rollback.clone() if rollback is not None else None
        return RepairResult(restored, RepairReport('rolled_back' if restored is not None else 'failed',
                            str(exc), attempts=steps, rollbacks=1, issues=original))


def _local_proposals(state, rng):
    # Round-robin avoids spending the whole budget on time changes alone.
    operations = ['MoveMeeting', 'ChangeProfessor', 'ChangeRoom', 'MoveStudent']
    rng.shuffle(operations)
    generators = [(op, iter(_iter_targets(op, state, rng))) for op in operations]
    while generators:
        remaining = []
        for op, proposals in generators:
            try:
                yield op, next(proposals)
                remaining.append((op, proposals))
            except StopIteration:
                pass
        generators = remaining


def repair_conflicts(chromosome: Chromosome, context: EvaluationContext, rng: Random,
                     settings: RepairSettings = RepairSettings(), *,
                     evaluation: EvaluationSnapshot | None = None) -> RepairResult:
    """At most 5 evaluations/conflict and 50/call by default, plus one baseline.

    A supplied fingerprint/context-bound snapshot removes the baseline call.
    Only strictly smaller comparison_key commits; equal and worse roll back.
    """
    require_inputs(chromosome, context, rng)
    verify_snapshot(chromosome, context, evaluation)
    baseline_calls = int(evaluation is None)
    baseline = evaluation if evaluation is not None else evaluate_snapshot(chromosome, context)
    best, best_snapshot = chromosome, baseline
    attempts = candidate_calls = accepted = rejected = 0
    seen = {baseline.fingerprint}
    per_conflict = []
    for violation in baseline.fitness.hard.violations[:settings.max_conflicts]:
        if candidate_calls >= settings.max_candidate_evaluations or attempts >= settings.max_candidate_attempts:
            break
        local = 0
        state = _State(best, context)
        factors = violation_factors((violation,), state)
        for operation, target in _local_proposals(state, rng):
            if (local >= settings.max_local_candidates or
                    candidate_calls >= settings.max_candidate_evaluations or
                    attempts >= settings.max_candidate_attempts):
                break
            # Even an irrelevant proposal consumes the global attempt budget.
            attempts += 1
            if not target_matches(target, factors, state):
                continue
            try:
                sections, enrollments = _apply_draft(state, operation, target, rng)
                child = commit_draft(sections, enrollments, context)
            except (_NotApplicable, _InvalidTarget, StructuralValidationError):
                rejected += 1
                continue
            fingerprint = chromosome_fingerprint(child)
            if fingerprint in seen:
                rejected += 1
                continue
            seen.add(fingerprint)
            fitness = evaluate_fitness(child, context)
            candidate_calls += 1
            local += 1
            if fitness.comparison_key < best_snapshot.fitness.comparison_key:
                best, best_snapshot = child, EvaluationSnapshot(fingerprint, fitness)
                accepted += 1
            else:
                rejected += 1
        per_conflict.append(local)
    # No accepted proposal is allowed to replace a caller's cache or mappings.
    changed = best_snapshot.fingerprint != baseline.fingerprint
    return RepairResult(best.clone(), RepairReport(
        'improved' if changed else 'unchanged',
        'strict hard-first improvement' if changed else 'no strictly better candidate within budgets',
        attempts=attempts, accepted=accepted, rollbacks=rejected,
        evaluations=EvaluationCounts(baseline_calls, candidate_calls),
        candidates_per_conflict=tuple(per_conflict),
        hard_before=baseline.fitness.hard.total_penalty,
        hard_after=best_snapshot.fitness.hard.total_penalty), best_snapshot)
