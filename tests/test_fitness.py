from dataclasses import replace
import unittest

from scheduler.constraints import evaluate_hard_constraints
from scheduler.domain import Preference
from scheduler.evaluation_context import EvaluationContext
from scheduler.fitness import FitnessEvaluation, evaluate_fitness
from scheduler.policies import DEFAULT_POLICY
from scheduler.soft_fitness import evaluate_soft_fitness
from test_constraints import sample, scheduled, with_rows


class FitnessTests(unittest.TestCase):
    def test_lower_hard_always_wins_even_with_worse_soft(self):
        d = with_rows(sample(), StudentPreferences=(Preference('S1', 'PreferredTimeSlots', None, 10),))
        context = EvaluationContext.from_dataset(d)
        c = scheduled(d)
        result = evaluate_fitness(c, context)
        # Precisely exercise comparison, without conflating the scoring formula.
        bad_hard = replace(result.hard, counts_by_type={'H07_SECTION_UNDERFLOW': 1})
        better_soft = replace(result.soft, total_score=1)
        worse_soft = replace(result.soft, total_score=.1)
        feasible = FitnessEvaluation(result.hard, worse_soft)
        infeasible = FitnessEvaluation(bad_hard, better_soft)
        self.assertEqual(feasible.comparison_key, (0, -.1))
        self.assertLess(feasible, infeasible)

    def test_tie_on_hard_prefers_larger_soft(self):
        d = sample()
        context = EvaluationContext.from_dataset(d)
        result = evaluate_fitness(scheduled(d), context)
        low = FitnessEvaluation(result.hard, replace(result.soft, total_score=.2))
        high = FitnessEvaluation(result.hard, replace(result.soft, total_score=.8))
        self.assertEqual(sorted([low, high])[0], high)

    def test_hard_and_soft_are_independent_and_reported_separately(self):
        d = sample()
        c = scheduled(d)
        context = EvaluationContext.from_dataset(d)
        combined = evaluate_fitness(c, context)
        self.assertEqual(combined.hard, evaluate_hard_constraints(c, context))
        self.assertEqual(combined.soft, evaluate_soft_fitness(c, context))
        self.assertEqual(combined.comparison_key, (0, -1))
        self.assertIsNone(c.hard_score)
        self.assertIsNone(c.soft_score)

    def test_different_policies_or_datasets_cannot_be_mixed_or_compared(self):
        d = sample()
        c = scheduled(d)
        context = EvaluationContext.from_dataset(d)
        other = EvaluationContext.from_dataset(d, replace(DEFAULT_POLICY, neutral_time_score=.25))
        a, b = evaluate_fitness(c, context), evaluate_fitness(c, other)
        with self.assertRaises(ValueError):
            FitnessEvaluation(a.hard, b.soft)
        with self.assertRaises(ValueError):
            a < b
        with self.assertRaises(TypeError):
            evaluate_fitness(c, d)


if __name__ == '__main__':
    unittest.main()
