"""Atomic draft validation and explicit, context-bound evaluation accounting."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .chromosome import Chromosome, RequestKey, SectionGene, validate_structure
from .constraints import StructuralValidationError
from .evaluation_context import EvaluationContext
from .fitness import FitnessEvaluation, evaluate_fitness
from .initialization import chromosome_fingerprint


@dataclass(frozen=True)
class GeneDraft:
    sections: Mapping[str, SectionGene]
    enrollments: Mapping[RequestKey, str | None]


@dataclass(frozen=True)
class EvaluationSnapshot:
    fingerprint: str
    fitness: FitnessEvaluation


@dataclass(frozen=True)
class EvaluationCounts:
    baseline: int = 0
    candidates: int = 0

    @property
    def fitness_calls(self) -> int:
        return self.baseline + self.candidates

    @property
    def hard_calls(self) -> int:
        return self.fitness_calls

    @property
    def soft_calls(self) -> int:
        return self.fitness_calls


def require_inputs(chromosome, context, rng, *, structural=True):
    from random import Random
    if not isinstance(chromosome, Chromosome) or not isinstance(context, EvaluationContext):
        raise TypeError('expected Chromosome and frozen EvaluationContext')
    if not isinstance(rng, Random):
        raise TypeError('rng must be a local random.Random')
    if structural:
        issues = validate_structure(chromosome, context)
        if issues:
            raise StructuralValidationError(issues)


def verify_snapshot(chromosome, context, snapshot):
    if snapshot is None:
        return
    if not isinstance(snapshot, EvaluationSnapshot):
        raise TypeError('evaluation must be an EvaluationSnapshot')
    if snapshot.fitness.hard.context_id != context.context_id or snapshot.fitness.soft.context_id != context.context_id:
        raise ValueError('evaluation snapshot belongs to a different Context')
    if snapshot.fingerprint != chromosome_fingerprint(chromosome):
        raise ValueError('evaluation snapshot is stale for this chromosome')


def evaluate_snapshot(chromosome: Chromosome, context: EvaluationContext) -> EvaluationSnapshot:
    """Explicit external baseline calculation; callers own/count this one call."""
    return EvaluationSnapshot(chromosome_fingerprint(chromosome), evaluate_fitness(chromosome, context))


def commit_draft(sections, enrollments, context) -> Chromosome:
    # No Chromosome is constructed before the temporary mappings pass validation.
    view = GeneDraft(MappingProxyType(sections), MappingProxyType(enrollments))
    issues = validate_structure(view, context)
    if issues:
        raise StructuralValidationError(issues)
    return Chromosome(sections, enrollments)
