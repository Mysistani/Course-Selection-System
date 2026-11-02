"""Hard-first comparison; never collapse hard and soft with a large coefficient."""
from dataclasses import dataclass

from .chromosome import Chromosome
from .constraints import HardEvaluation, evaluate_hard_constraints
from .evaluation_context import EvaluationContext
from .soft_fitness import SoftEvaluation, evaluate_soft_fitness


@dataclass(frozen=True)
class FitnessEvaluation:
    hard: HardEvaluation
    soft: SoftEvaluation

    def __post_init__(self):
        if self.hard.context_id != self.soft.context_id:
            raise ValueError('hard and soft results belong to different evaluation contexts')

    @property
    def comparison_key(self) -> tuple[int, float]:
        return self.hard.total_penalty, -self.soft.total_score

    def __lt__(self, other):
        if not isinstance(other, FitnessEvaluation):
            return NotImplemented
        if self.hard.context_id != other.hard.context_id:
            raise ValueError('cannot compare results from different Dataset/DesignPolicy runs')
        return self.comparison_key < other.comparison_key


def evaluate_fitness(chromosome: Chromosome, context: EvaluationContext) -> FitnessEvaluation:
    if not isinstance(context, EvaluationContext):
        raise TypeError('combined evaluation requires one frozen EvaluationContext per run')
    return FitnessEvaluation(evaluate_hard_constraints(chromosome, context),
                             evaluate_soft_fitness(chromosome, context))
