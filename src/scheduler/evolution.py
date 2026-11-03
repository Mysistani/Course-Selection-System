"""Generational GA with independent elites, explicit evaluation costs, no greedy replacement."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from random import Random
from statistics import fmean
from time import perf_counter
from typing import Callable

from .chromosome import Chromosome, validate_structure
from .constraints import StructuralValidationError
from .crossover import course_based_crossover
from .evaluation_context import EvaluationContext
from .initialization import (InitializedIndividual, InitializationSettings,
                             chromosome_fingerprint, initialize_population)
from .mutation import MutationSettings, maybe_mutate
from .operator_support import EvaluationSnapshot, evaluate_snapshot, verify_snapshot
from .repair import RepairSettings, repair_conflicts
from .selection import tournament_selection


@dataclass(frozen=True)
class EvolutionSettings:
    version: str = 'evolution-phase6-v1'
    population_size: int = 100
    tournament_size: int = 3
    elite_count: int = 2
    max_generations: int = 500
    mutation_probability: float = .30
    crossover_probability: float = .70
    repair_enabled: bool = True
    random_seed: int = 0
    stop_on_first_feasible: bool = False
    stagnation_generations: int | None = None
    max_fitness_evaluations: int | None = None
    initialization_settings: InitializationSettings = InitializationSettings()
    mutation_settings: MutationSettings = MutationSettings()
    repair_settings: RepairSettings = RepairSettings()

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError('version must be non-empty text')
        if type(self.population_size) is not int or self.population_size < 2:
            raise ValueError('population_size must be an integer >=2')
        for name in ('tournament_size', 'elite_count'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.tournament_size > self.population_size:
            raise ValueError('tournament_size exceeds population_size')
        if self.elite_count >= self.population_size:
            raise ValueError('elite_count must leave room for at least one child')
        if type(self.max_generations) is not int or self.max_generations < 0:
            raise ValueError('max_generations must be a non-negative integer')
        if type(self.random_seed) is not int:
            raise ValueError('random_seed must be an integer')
        for name in ('mutation_probability', 'crossover_probability'):
            if type(getattr(self, name)) not in (int, float) or not 0 <= getattr(self, name) <= 1:
                raise ValueError(f'{name} must be in [0,1]')
        for name in ('repair_enabled', 'stop_on_first_feasible'):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f'{name} must be Boolean')
        for name in ('stagnation_generations', 'max_fitness_evaluations'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f'{name} must be None or a positive integer')
        if self.max_fitness_evaluations is not None and self.max_fitness_evaluations < self.population_size:
            raise ValueError('fitness budget must cover the entire initial population')
        for name, cls in (('initialization_settings', InitializationSettings),
                          ('mutation_settings', MutationSettings), ('repair_settings', RepairSettings)):
            if not isinstance(getattr(self, name), cls):
                raise TypeError(f'{name} must be {cls.__name__}')
        # One authoritative gate; the full nested settings printed in reports agree.
        object.__setattr__(self, 'mutation_settings', replace(
            self.mutation_settings, mutation_probability=self.mutation_probability))


@dataclass(frozen=True)
class EvaluationUsage:
    initialization: int = 0
    child_baseline: int = 0
    post_mutation: int = 0
    mutation_baseline: int = 0
    repair_baseline: int = 0
    repair_candidates: int = 0

    @property
    def total(self):
        return sum(getattr(self, f.name) for f in fields(self))

    def __sub__(self, other):
        return EvaluationUsage(**{f.name: getattr(self, f.name) - getattr(other, f.name) for f in fields(self)})


@dataclass(frozen=True)
class OperatorStatistics:
    children: int = 0
    crossover_applied: int = 0
    parent_cloned: int = 0
    self_matings: int = 0
    mutation_applied: int = 0
    mutation_skipped: int = 0
    mutation_failed: int = 0
    mutation_not_applicable: int = 0
    mutation_targeted: int = 0
    mutation_fallback: int = 0
    mutation_operations: tuple[tuple[str, int], ...] = ()
    repair_improved: int = 0
    repair_unchanged: int = 0
    repair_disabled: int = 0
    better_than_both_parents: int = 0
    between_parents: int = 0  # inclusive ties; these three categories partition children
    worse_than_both_parents: int = 0
    identical_to_a_parent: int = 0  # independent, overlapping genotype statistic

    @property
    def self_mating_rate(self):
        return self.self_matings / self.children if self.children else 0.0


@dataclass(frozen=True)
class GenerationStatistics:
    generation_index: int
    population_size: int
    best_hard_penalty: int
    mean_hard_penalty: float
    best_soft_score: float  # Soft of hard-first best, not independent maximum Soft
    mean_soft_score: float
    feasible_count: int
    unique_chromosomes: int
    mean_request_coverage: float
    active_sections: float  # mean per individual
    unassigned_requests: float  # mean per individual
    best_fingerprint: str
    elite_fingerprints: tuple[str, ...]
    operators: OperatorStatistics
    evaluations: EvaluationUsage
    fitness_evaluation_count: int
    candidate_repair_evaluation_count: int
    cumulative_fitness_evaluation_count: int
    generation_elapsed_seconds: float
    cumulative_elapsed_seconds: float


@dataclass(frozen=True)
class PartialGeneration:
    generation_index: int
    completed_children: int
    operators: OperatorStatistics
    evaluations: EvaluationUsage
    elapsed_seconds: float
    reason: str


@dataclass(frozen=True)
class EvolutionResult:
    settings: EvolutionSettings
    context_id: tuple[str, str]
    dataset_sha256: str
    policy_version: str
    population: tuple[InitializedIndividual, ...]  # last COMPLETE population
    generations: tuple[GenerationStatistics, ...]  # includes initialization as generation 0
    best_overall: InitializedIndividual
    best_feasible: InitializedIndividual | None
    final_population_best: InitializedIndividual
    stop_reasons: tuple[str, ...]
    evaluations: EvaluationUsage
    context_elapsed_seconds: float
    initialization_elapsed_seconds: float
    evolution_elapsed_seconds: float
    initialization_limitations: tuple
    partial_generation: PartialGeneration | None = None
    failure: str | None = None

    @property
    def feasible_found(self):
        return self.best_feasible is not None


class EvolutionFailure(RuntimeError):
    """Fail visibly with stage information and the last valid completed result."""
    def __init__(self, message, result=None):
        self.result = result
        super().__init__(message)


def _copy_member(member, strategy=None):
    return replace(member, chromosome=member.chromosome.clone(), strategy=strategy or member.strategy)


def _checked_member(chromosome, snapshot, context, strategy):
    issues = validate_structure(chromosome, context)
    if issues:
        raise StructuralValidationError(issues)
    verify_snapshot(chromosome, context, snapshot)
    return InitializedIndividual(chromosome, snapshot.fitness, snapshot.fingerprint, strategy, 1, ())


def _statistics(index, population, elites, operators, usage, total, elapsed, cumulative):
    best = min(population, key=lambda i: i.evaluation.comparison_key)
    return GenerationStatistics(
        index, len(population), best.evaluation.hard.total_penalty,
        fmean(i.evaluation.hard.total_penalty for i in population), best.evaluation.soft.total_score,
        fmean(i.evaluation.soft.total_score for i in population),
        sum(i.evaluation.hard.is_feasible for i in population), len({i.fingerprint for i in population}),
        fmean(i.evaluation.soft.request_coverage for i in population),
        fmean(sum(s.active for s in i.chromosome.sections.values()) for i in population),
        fmean(len(i.evaluation.hard.metrics.unassigned_requests) for i in population),
        best.fingerprint, elites, operators, usage, usage.total, usage.repair_candidates,
        total.total, elapsed, cumulative)


@dataclass(frozen=True)
class EvolutionProgress:
    """Detached observational copies at a completed-generation boundary."""
    statistics: GenerationStatistics
    population: tuple[InitializedIndividual, ...]
    best_overall: InitializedIndividual
    best_feasible: InitializedIndividual | None
    stagnant_generations: int


def run_evolution(context: EvaluationContext, settings: EvolutionSettings = EvolutionSettings(), *,
                  context_elapsed_seconds: float = 0.0,
                  on_generation: Callable[[EvolutionProgress], None] | None = None,
                  stop_requested: Callable[[], bool] | None = None) -> EvolutionResult:
    """Run sequential generations with a pinned context and local RNG streams.

    Fitness budget covers initialization and every real child/repair evaluation.
    An interrupted generation is reported separately; it never becomes a partial
    population. No worse child is replaced by its parent.
    """
    if not isinstance(context, EvaluationContext) or not isinstance(settings, EvolutionSettings):
        raise TypeError('expected frozen EvaluationContext and EvolutionSettings')
    import math
    if type(context_elapsed_seconds) not in (int, float) or not math.isfinite(context_elapsed_seconds) or context_elapsed_seconds < 0:
        raise ValueError('context_elapsed_seconds must be finite and non-negative')
    started = perf_counter()
    try:
        initial = initialize_population(context, settings.population_size, settings.random_seed, settings.initialization_settings)
        population = initial.individuals
        if len(population) != settings.population_size or initial.context_id != context.context_id:
            raise ValueError('initializer returned wrong population size or Context')
        for i in population:
            _checked_member(i.chromosome, EvaluationSnapshot(i.fingerprint, i.evaluation), context, i.strategy)
    except Exception as exc:
        raise EvolutionFailure(f'initialization failed: {type(exc).__name__}: {exc}') from exc
    initialization_seconds = perf_counter() - started
    usage = EvaluationUsage(initialization=len(population))
    best = best_feasible = None

    def observe(member):
        nonlocal best, best_feasible
        if best is None or member.evaluation.comparison_key < best.evaluation.comparison_key:
            best = _copy_member(member)
        if member.evaluation.hard.is_feasible and (best_feasible is None or
                member.evaluation.comparison_key < best_feasible.evaluation.comparison_key):
            best_feasible = _copy_member(member)

    for member in population:
        observe(member)
    history = [_statistics(0, population, (), OperatorStatistics(), usage, usage,
                           initialization_seconds, context_elapsed_seconds + initialization_seconds)]
    evolution_start = perf_counter()
    rng = Random(settings.random_seed)  # separate stream from Phase4's initialization RNG
    stagnant = 0

    def remaining():
        return None if settings.max_fitness_evaluations is None else settings.max_fitness_evaluations - usage.total

    def finish(reasons, partial=None, failure=None):
        return EvolutionResult(settings, context.context_id, context.dataset.sha256, context.policy.version,
            population, tuple(history), _copy_member(best), _copy_member(best_feasible) if best_feasible else None,
            _copy_member(min(population, key=lambda i: i.evaluation.comparison_key)), tuple(reasons), usage,
            context_elapsed_seconds, initialization_seconds, perf_counter() - evolution_start,
            initial.limitations, partial, failure)

    def boundary_reasons():
        reasons = []
        if stop_requested is not None and stop_requested():
            reasons.append('manual_stop')
        if settings.stop_on_first_feasible and best_feasible is not None:
            reasons.append('first_feasible')
        if settings.stagnation_generations is not None and stagnant >= settings.stagnation_generations:
            reasons.append('stagnation')
        if len(history) - 1 >= settings.max_generations:
            reasons.append('max_generations')
        if remaining() is not None and remaining() < 1 + int(settings.mutation_probability > 0):
            reasons.append('fitness_budget')
        return reasons

    def publish():
        if on_generation is not None:
            try:
                on_generation(EvolutionProgress(history[-1], tuple(_copy_member(i) for i in population),
                    _copy_member(best), _copy_member(best_feasible) if best_feasible else None, stagnant))
            except Exception as exc:
                message = f'progress observer failed: {type(exc).__name__}: {exc}'
                raise EvolutionFailure(message, finish(('failure',), failure=message)) from exc

    publish()
    while True:
        reasons = boundary_reasons()
        if reasons:
            return finish(reasons)
        index = len(history)
        generation_start, before_usage = perf_counter(), usage
        ranked = sorted(population, key=lambda i: i.evaluation.comparison_key)  # stable index ties
        next_population = [_copy_member(i, 'elite') for i in ranked[:settings.elite_count]]
        elite_fingerprints = tuple(i.fingerprint for i in next_population)
        counters = {f.name: 0 for f in fields(OperatorStatistics) if f.name != 'mutation_operations'}
        operation_counts = {}

        def operator_stats():
            return OperatorStatistics(**counters, mutation_operations=tuple(sorted(operation_counts.items())))

        stage, child_index = 'selection', 0
        try:
            while len(next_population) < settings.population_size:
                # Reserve the mandatory baseline and a possible post-mutation call.
                # Repair gets only the actual remaining budget, never this reserve.
                if remaining() is not None and remaining() < 1 + int(settings.mutation_probability > 0):
                    partial = PartialGeneration(index, counters['children'], operator_stats(), usage - before_usage,
                                                perf_counter() - generation_start, 'fitness_budget')
                    return finish(('fitness_budget',), partial)
                child_index = len(next_population) - settings.elite_count
                stage = 'selection'
                a = tournament_selection(population, rng, settings.tournament_size)
                b = tournament_selection(population, rng, settings.tournament_size)
                counters['self_matings'] += a is b
                stage = 'crossover_or_clone'
                if rng.random() < settings.crossover_probability:
                    child = course_based_crossover(a.chromosome, b.chromosome, context, rng)
                    strategy = 'crossover'
                    counters['crossover_applied'] += 1
                else:
                    child = rng.choice((a, b)).chromosome.clone()
                    strategy = 'clone'
                    counters['parent_cloned'] += 1
                stage = 'child_baseline'
                issues = validate_structure(child, context)
                if issues:
                    raise StructuralValidationError(issues)
                usage = replace(usage, child_baseline=usage.child_baseline + 1)
                snapshot = evaluate_snapshot(child, context)
                observe(_checked_member(child, snapshot, context, strategy))
                stage = 'mutation'
                mutation = maybe_mutate(child, context, rng, settings.mutation_settings, evaluation=snapshot)
                usage = replace(usage, mutation_baseline=usage.mutation_baseline + mutation.report.evaluations.fitness_calls)
                if mutation.report.evaluations.fitness_calls:
                    raise ValueError('mutation unexpectedly evaluated despite supplied snapshot')
                counters['mutation_' + mutation.report.status] += 1
                counters['mutation_targeted'] += mutation.report.targeted
                counters['mutation_fallback'] += mutation.report.fallback
                child = mutation.chromosome
                if mutation.report.status == 'applied':
                    operation_counts[mutation.report.operation] = operation_counts.get(mutation.report.operation, 0) + 1
                    stage = 'post_mutation'
                    issues = validate_structure(child, context)
                    if issues:
                        raise StructuralValidationError(issues)
                    usage = replace(usage, post_mutation=usage.post_mutation + 1)
                    snapshot = evaluate_snapshot(child, context)
                    observe(_checked_member(child, snapshot, context, strategy))
                else:
                    verify_snapshot(child, context, snapshot)
                if settings.repair_enabled:
                    stage = 'repair'
                    repair_settings = settings.repair_settings
                    if remaining() is not None:
                        repair_settings = replace(repair_settings, max_candidate_evaluations=min(
                            repair_settings.max_candidate_evaluations, remaining()))
                    repaired = repair_conflicts(child, context, rng, repair_settings, evaluation=snapshot)
                    usage = replace(usage, repair_baseline=usage.repair_baseline + repaired.report.evaluations.baseline,
                                    repair_candidates=usage.repair_candidates + repaired.report.evaluations.candidates)
                    if repaired.report.evaluations.baseline or (remaining() is not None and remaining() < 0):
                        raise ValueError('repair violated the supplied snapshot/evaluation budget contract')
                    if repaired.evaluation is None or repaired.chromosome is None:
                        raise ValueError('conflict repair did not return a valid evaluated individual')
                    if repaired.evaluation.fitness.comparison_key > snapshot.fitness.comparison_key:
                        raise ValueError('conflict repair worsened its own input')
                    child, snapshot = repaired.chromosome, repaired.evaluation
                    counters['repair_' + repaired.report.status] += 1
                else:
                    counters['repair_disabled'] += 1
                stage = 'admission'
                member = _checked_member(child, snapshot, context, strategy)
                # Deliberately admit without a parent-vs-child acceptance predicate.
                next_population.append(member)
                observe(member)
                counters['children'] += 1
                key, ak, bk = member.evaluation.comparison_key, a.evaluation.comparison_key, b.evaluation.comparison_key
                category = ('better_than_both_parents' if key < min(ak, bk) else
                            'worse_than_both_parents' if key > max(ak, bk) else 'between_parents')
                counters[category] += 1
                counters['identical_to_a_parent'] += member.fingerprint in (a.fingerprint, b.fingerprint)
        except Exception as exc:
            message = f'generation={index}, child={child_index}, stage={stage}: {type(exc).__name__}: {exc}'
            partial = PartialGeneration(index, counters['children'], operator_stats(), usage - before_usage,
                                        perf_counter() - generation_start, 'failure')
            raise EvolutionFailure(message, finish(('failure',), partial, message)) from exc
        previous_key = history[-1].best_hard_penalty, -history[-1].best_soft_score
        completed_population = tuple(next_population)
        current = min(completed_population, key=lambda i: i.evaluation.comparison_key)
        if current.evaluation.comparison_key > previous_key:
            raise EvolutionFailure('elitism invariant violated', finish(('failure',), failure='elitism invariant violated'))
        population = completed_population
        stagnant = 0 if current.evaluation.comparison_key < previous_key else stagnant + 1
        history.append(_statistics(index, population, elite_fingerprints, operator_stats(), usage - before_usage,
                                   usage, perf_counter() - generation_start,
                                   context_elapsed_seconds + initialization_seconds + perf_counter() - evolution_start))
        publish()
