"""Bounded, reproducible initial populations; no timetable search or repair."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from random import Random
from statistics import fmean
from time import perf_counter
from types import MappingProxyType
from typing import Mapping

from .chromosome import Chromosome, RequestKey, SectionGene, validate_structure
from .constraints import StructuralValidationError
from .evaluation_context import EvaluationContext
from .fitness import FitnessEvaluation, evaluate_fitness


@dataclass(frozen=True)
class InitializationSettings:
    version: str = 'initialization-v1'
    strategies: tuple[str, ...] = ('compact', 'spread', 'random')
    max_attempts_per_individual: int = 8

    def __post_init__(self):
        object.__setattr__(self, 'strategies', tuple(self.strategies))
        if not self.strategies or any(s not in ('compact', 'spread', 'random') for s in self.strategies):
            raise ValueError('strategies must contain compact, spread or random')
        if type(self.max_attempts_per_individual) is not int or self.max_attempts_per_individual < 1:
            raise ValueError('max_attempts_per_individual must be a positive integer')
        if not isinstance(self.version, str) or not self.version:
            raise ValueError('settings version must be non-empty text')


@dataclass(frozen=True)
class GenerationIssue:
    kind: str
    course_id: str | None
    message: str
    unassigned_count: int = 0


@dataclass(frozen=True)
class InitializedIndividual:
    chromosome: Chromosome
    evaluation: FitnessEvaluation
    fingerprint: str
    strategy: str
    attempts: int
    issues: tuple[GenerationIssue, ...]


@dataclass(frozen=True)
class DiversityStatistics:
    unique_chromosomes: int
    unique_active_sets: int
    unique_professor_assignments: int
    unique_room_assignments: int
    unique_timetables: int
    unique_enrollments: int
    varying_active_slots: int
    varying_professor_choices: int  # Distinct non-None choices only.
    varying_room_choices: int
    varying_meeting_slots: int     # Active timetables only.
    varying_requests: int


@dataclass(frozen=True)
class PopulationStatistics:
    count: int
    unique_count: int
    min_hard_penalty: int
    mean_hard_penalty: float
    max_hard_penalty: int
    mean_soft_score: float
    best_index: int
    best_comparison_key: tuple[int, float]
    feasible_count: int
    mean_request_coverage: float
    min_active_sections: int
    mean_active_sections: float
    max_active_sections: int
    min_unassigned_requests: int
    mean_unassigned_requests: float
    max_unassigned_requests: int
    generation_attempts: int
    rejected_duplicates: int
    accepted_duplicates: int
    preparation_seconds: float
    elapsed_seconds: float
    strategy_counts: Mapping[str, int]
    issue_counts: Mapping[str, int]  # Individual/course issue events, not lost requests.


@dataclass(frozen=True)
class PopulationInitialization:
    individuals: tuple[InitializedIndividual, ...]
    statistics: PopulationStatistics
    diversity: DiversityStatistics
    limitations: tuple[GenerationIssue, ...]
    random_seed: int
    settings: InitializationSettings
    context_id: tuple[str, str]

    @property
    def best_individual(self) -> InitializedIndividual:
        return self.individuals[self.statistics.best_index]


@dataclass(frozen=True)
class _CourseOptions:
    section_ids: tuple[str, ...]
    requests: tuple[RequestKey, ...]
    professors: tuple[str, ...]
    rooms: tuple[str, ...]
    max_capacity: int


def _projections(chromosome: Chromosome):
    rows = sorted(chromosome.sections.items())
    return (
        tuple(sid for sid, section in rows if section.active),
        tuple((sid, section.professor_id) for sid, section in rows),
        tuple((sid, section.room_id) for sid, section in rows),
        tuple((sid, tuple(sorted(section.meeting_slots))) for sid, section in rows),
        tuple(sorted(chromosome.enrollments.items())),
    )


def chromosome_fingerprint(chromosome: Chromosome) -> str:
    """Canonical genotype digest, ignoring cache scores and meeting-list order."""
    # Course binding is included even though structural validation fixes it.
    sections = tuple((sid, s.course_id, s.active, s.professor_id, s.room_id,
                      tuple(sorted(s.meeting_slots))) for sid, s in sorted(chromosome.sections.items()))
    payload = sections, tuple(sorted(chromosome.enrollments.items()))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def measure_diversity(chromosomes: tuple[Chromosome, ...]) -> DiversityStatistics:
    if not chromosomes:
        return DiversityStatistics(*([0] * 11))
    projections = [_projections(c) for c in chromosomes]
    section_keys = sorted(set().union(*(c.sections.keys() for c in chromosomes)))
    request_keys = sorted(set().union(*(c.enrollments.keys() for c in chromosomes)))
    active = professors = rooms = meetings = 0
    for sid in section_keys:
        genes = [c.sections.get(sid) for c in chromosomes]
        active += len({g.active if g is not None else False for g in genes}) > 1
        professors += len({g.professor_id for g in genes if g is not None and g.professor_id is not None}) > 1
        rooms += len({g.room_id for g in genes if g is not None and g.room_id is not None}) > 1
        meetings += len({tuple(sorted(g.meeting_slots)) for g in genes if g is not None and g.active}) > 1
    requests = sum(len({c.enrollments.get(key) for c in chromosomes}) > 1 for key in request_keys)
    return DiversityStatistics(
        len({chromosome_fingerprint(c) for c in chromosomes}),
        *(len({p[i] for p in projections}) for i in range(5)),
        active, professors, rooms, meetings, requests,
    )


class PopulationInitializer:
    """One pinned context and local RNG stream; create a fresh instance to replay."""

    def __init__(self, context: EvaluationContext, random_seed: int = 0,
                 settings: InitializationSettings = InitializationSettings()):
        started = perf_counter()
        if not isinstance(context, EvaluationContext):
            raise TypeError('initialization requires a prepared EvaluationContext')
        if type(random_seed) is not int:
            raise ValueError('random_seed must be an integer')
        if not isinstance(settings, InitializationSettings):
            raise TypeError('expected InitializationSettings')
        self._context, self._seed, self._settings = context, random_seed, settings
        self._rng = Random(random_seed)
        self._allowed_slots = tuple(sorted(key for key, slot in context.slots.items() if slot.is_allowed))
        grouped_requests, grouped_sections, qualified = defaultdict(list), defaultdict(list), defaultdict(list)
        for key in sorted(context.requests):
            if key not in context.passed:
                grouped_requests[key[1]].append(key)
        for sid, cid in sorted(context.expected_sections.items()):
            grouped_sections[cid].append(sid)
        for pid, cid in sorted(context.qualified):
            qualified[cid].append(pid)
        self._options, limitations = {}, []
        for cid, course in context.courses.items():
            requests = tuple(grouped_requests[cid])
            reasons = []
            if len(requests) < course.min_section_size:
                reasons.append(('LOW_DEMAND', f'{len(requests)} eligible requests; minimum {course.min_section_size}'))
            if not qualified[cid]:
                reasons.append(('NO_QUALIFIED_PROFESSOR', 'no CanTeach=True professor'))
            rooms = tuple(sorted(context.compatible_rooms[cid]))
            if not rooms:
                reasons.append(('NO_COMPATIBLE_ROOM', 'no matching room with effective capacity >= minimum'))
            if len(self._allowed_slots) < course.sessions_per_week:
                reasons.append(('INSUFFICIENT_ALLOWED_SLOTS', f'need {course.sessions_per_week} distinct allowed slots; have {len(self._allowed_slots)}'))
            if not grouped_sections[cid] and len(requests) >= course.min_section_size:
                reasons.append(('NO_SECTION_SLOTS', 'dataset has no potential slots for this course'))
            if reasons:
                limitations.extend(GenerationIssue(kind, cid, message, len(requests)) for kind, message in reasons)
                continue
            self._options[cid] = _CourseOptions(tuple(grouped_sections[cid]), requests,
                                                tuple(qualified[cid]), rooms,
                                                max(context.effective_capacities[(cid, rid)] for rid in rooms))
        self._limitations = tuple(limitations)
        self._preparation_seconds = perf_counter() - started

    @property
    def context(self) -> EvaluationContext:
        return self._context

    @property
    def settings(self) -> InitializationSettings:
        return self._settings

    def _construct(self, strategy: str) -> tuple[Chromosome, tuple[GenerationIssue, ...]]:
        context, rng = self._context, self._rng
        sections = {sid: SectionGene(sid, cid) for sid, cid in context.expected_sections.items()}
        enrollments = dict.fromkeys(sorted(context.requests))
        issues = list(self._limitations)
        for cid, options in self._options.items():
            course = context.courses[cid]
            demand, minimum = len(options.requests), course.min_section_size
            upper = min(len(options.section_ids), demand // minimum)
            lower = min(upper, (demand + options.max_capacity - 1) // options.max_capacity)
            count = lower if strategy == 'compact' else upper if strategy == 'spread' else rng.randint(lower, upper)
            chosen = rng.sample(options.section_ids, count)
            # Capacity lookahead only: no teacher/student timetable search.
            target = min(demand, count * options.max_capacity)
            capacities, selected_rooms = {}, {}
            capacity_so_far = 0
            for index, sid in enumerate(chosen):
                needed = max(minimum, target - capacity_so_far - (count - index - 1) * options.max_capacity)
                possible = [rid for rid in options.rooms if context.effective_capacities[(cid, rid)] >= needed]
                room_id = rng.choice(possible)
                capacity = context.effective_capacities[(cid, room_id)]
                selected_rooms[sid], capacities[sid] = room_id, capacity
                capacity_so_far += capacity
            served = min(demand, sum(capacities.values()))
            sizes = dict.fromkeys(chosen, minimum)
            remaining = served - minimum * count
            fill_order = list(chosen)
            rng.shuffle(fill_order)
            if strategy == 'compact':
                for sid in fill_order:
                    extra = min(remaining, capacities[sid] - sizes[sid])
                    sizes[sid] += extra
                    remaining -= extra
            else:
                available = [sid for sid in fill_order if sizes[sid] < capacities[sid]]
                for _ in range(remaining):
                    if strategy == 'spread':
                        smallest = min(sizes[sid] for sid in available)
                        sid = rng.choice([s for s in available if sizes[s] == smallest])
                    else:
                        sid = rng.choice(available)
                    sizes[sid] += 1
                    if sizes[sid] == capacities[sid]:
                        available.remove(sid)
            # Strict Priority cut-off; random ties, then random membership within
            # the served set. Lower priorities never displace higher priorities.
            ordered = sorted(options.requests, key=lambda key: (-context.priorities[key], rng.random()))
            served_requests = ordered[:served]
            rng.shuffle(served_requests)
            offset = 0
            for sid in chosen:
                sections[sid] = SectionGene(sid, cid, True, rng.choice(options.professors),
                                           selected_rooms[sid], tuple(sorted(rng.sample(self._allowed_slots, course.sessions_per_week))))
                for key in served_requests[offset:offset + sizes[sid]]:
                    enrollments[key] = sid
                offset += sizes[sid]
            if served < demand:
                issues.append(GenerationIssue('INSUFFICIENT_SELECTED_CAPACITY', cid,
                                               f'{served} of {demand} requests fit within stable group slots and selected room capacities', demand - served))
        chromosome = Chromosome(sections, enrollments)
        structural_issues = validate_structure(chromosome, context)
        if structural_issues:
            # A programming/input-integrity failure is never hidden as poor fitness.
            raise StructuralValidationError(structural_issues)
        return chromosome, tuple(issues)

    def initialize_one(self) -> InitializedIndividual:
        strategy = self._rng.choice(self._settings.strategies)
        chromosome, issues = self._construct(strategy)
        return InitializedIndividual(chromosome, evaluate_fitness(chromosome, self._context),
                                     chromosome_fingerprint(chromosome), strategy, 1, issues)

    def initialize_population(self, population_size: int) -> PopulationInitialization:
        if type(population_size) is not int or population_size < 1:
            raise ValueError('population_size must be a positive integer')
        started = perf_counter()
        individuals, seen = [], set()
        attempts = rejected = duplicates = 0
        limitations = list(self._limitations)
        # If nothing can be activated, the genotype space is provably a singleton.
        singleton = not self._options
        if singleton and population_size > 1:
            limitations.append(GenerationIssue('SINGLE_POSSIBLE_CHROMOSOME', None,
                                               'all groups must stay inactive; independent duplicate individuals will be returned'))
        for index in range(population_size):
            budget = 1 if singleton else self._settings.max_attempts_per_individual
            for attempt in range(1, budget + 1):
                strategy = self._settings.strategies[index % len(self._settings.strategies)] if attempt == 1 else self._rng.choice(self._settings.strategies)
                chromosome, issues = self._construct(strategy)
                attempts += 1
                fingerprint = chromosome_fingerprint(chromosome)
                if fingerprint not in seen or attempt == budget:
                    break
                rejected += 1
            if fingerprint in seen:
                duplicates += 1
                if not singleton:
                    issues += (GenerationIssue('DIVERSITY_RETRY_BUDGET_EXHAUSTED', None,
                                                f'no new genotype found within {budget} attempts; duplicate accepted independently'),)
            seen.add(fingerprint)
            individuals.append(InitializedIndividual(chromosome, evaluate_fitness(chromosome, self._context),
                                                      fingerprint, strategy, attempt, issues))
        if duplicates and not singleton:
            limitations.append(GenerationIssue('DIVERSITY_LIMIT_REACHED', None,
                                               f'{duplicates} duplicates accepted after bounded search; not a proof that more unique genotypes are impossible'))
        output = tuple(individuals)
        diversity = measure_diversity(tuple(i.chromosome for i in output))
        hard = [i.evaluation.hard.total_penalty for i in output]
        active = [sum(s.active for s in i.chromosome.sections.values()) for i in output]
        unanswered = [len(i.evaluation.hard.metrics.unassigned_requests) for i in output]
        best = min(range(population_size), key=lambda j: output[j].evaluation.comparison_key)
        statistics = PopulationStatistics(
            population_size, diversity.unique_chromosomes, min(hard), fmean(hard), max(hard),
            fmean(i.evaluation.soft.total_score for i in output), best, output[best].evaluation.comparison_key,
            sum(value == 0 for value in hard), fmean(i.evaluation.soft.request_coverage for i in output),
            min(active), fmean(active), max(active), min(unanswered), fmean(unanswered), max(unanswered),
            attempts, rejected, duplicates, self._preparation_seconds, perf_counter() - started,
            MappingProxyType(dict(Counter(i.strategy for i in output))),
            MappingProxyType(dict(Counter(issue.kind for i in output for issue in i.issues))),
        )
        return PopulationInitialization(output, statistics, diversity, tuple(limitations),
                                        self._seed, self._settings, self._context.context_id)


def initialize_chromosome(context: EvaluationContext, random_seed: int = 0,
                          settings: InitializationSettings = InitializationSettings()) -> InitializedIndividual:
    return PopulationInitializer(context, random_seed, settings).initialize_one()


def initialize_population(context: EvaluationContext, population_size: int = 100,
                          random_seed: int = 0,
                          settings: InitializationSettings = InitializationSettings()) -> PopulationInitialization:
    return PopulationInitializer(context, random_seed, settings).initialize_population(population_size)
