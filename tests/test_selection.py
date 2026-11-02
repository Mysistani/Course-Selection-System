from dataclasses import replace
from random import Random, getstate
from types import MappingProxyType
import unittest

from scheduler.evaluation_context import EvaluationContext
from scheduler.fitness import FitnessEvaluation, evaluate_fitness
from scheduler.initialization import InitializedIndividual, chromosome_fingerprint, initialize_population
from scheduler.policies import DEFAULT_POLICY
from scheduler.selection import tournament_selection
from test_constraints import sample, scheduled


def member(context, hard=0, soft=.5):
    chromosome = scheduled(context.dataset)
    result = evaluate_fitness(chromosome, context)
    # Controlled score records isolate selection's comparator from evaluator tests.
    result = FitnessEvaluation(replace(result.hard, counts_by_type=MappingProxyType({'test': hard})),
                               replace(result.soft, total_score=soft))
    return InitializedIndividual(chromosome, result, chromosome_fingerprint(chromosome), 'fixture', 1, ())


class TrackingRandom(Random):
    def sample(self, population, k, **kwargs):
        self.sampled = super().sample(population, k, **kwargs)
        self.sample_size = k
        return self.sampled


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.context = EvaluationContext.from_dataset(sample())

    def test_default_tournament_is_three_without_replacement(self):
        population = [member(self.context, hard=i) for i in range(10)]
        rng = TrackingRandom(11)
        selected = tournament_selection(population, rng)
        self.assertEqual(rng.sample_size, 3)
        self.assertEqual(len({id(i) for i in rng.sampled}), 3)
        self.assertEqual(selected.evaluation.comparison_key, min(i.evaluation.comparison_key for i in rng.sampled))

    def test_less_hard_beats_better_soft(self):
        best = member(self.context, hard=0, soft=.1)
        population = [member(self.context, hard=1, soft=1), best, member(self.context, hard=2, soft=.9)]
        self.assertIs(tournament_selection(population, Random(10)), best)

    def test_equal_hard_prefers_larger_soft(self):
        population = [member(self.context, hard=3, soft=.2), member(self.context, hard=3, soft=.9),
                      member(self.context, hard=3, soft=.5)]
        self.assertIs(tournament_selection(population, Random(2)), population[1])

    def test_complete_ties_are_random_and_seed_reproducible(self):
        population = [member(self.context) for _ in range(3)]
        first = tournament_selection(population, Random(42))
        second = tournament_selection(population, Random(42))
        self.assertIs(first, second)
        winners = {id(tournament_selection(population, Random(seed))) for seed in range(30)}
        self.assertEqual(winners, {id(i) for i in population})

    def test_oversized_tournament_and_empty_population_are_clear_errors(self):
        population = [member(self.context), member(self.context)]
        with self.assertRaisesRegex(ValueError, 'population size'):
            tournament_selection(population, Random(1))
        with self.assertRaisesRegex(ValueError, 'non-empty'):
            tournament_selection([], Random(1))
        for size in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                tournament_selection(population, Random(1), size)

    def test_different_context_anywhere_in_population_is_rejected_before_rng_draw(self):
        other = EvaluationContext.from_dataset(self.context.dataset, replace(DEFAULT_POLICY, neutral_time_score=.25))
        population = [member(self.context), member(self.context), member(other)]
        rng = Random(5)
        before = rng.getstate()
        with self.assertRaisesRegex(ValueError, 'different EvaluationContexts'):
            tournament_selection(population, rng, 1)
        self.assertEqual(rng.getstate(), before)
        self.assertIn(tournament_selection(population, rng, allow_mixed_contexts=True), population)

    def test_independent_parent_draws_may_choose_same_parent(self):
        population = [member(self.context, hard=2), member(self.context, hard=1), member(self.context, hard=3)]
        rng = Random(9)
        a = tournament_selection(population, rng)
        b = tournament_selection(population, rng)
        self.assertIs(a, population[1])
        self.assertIs(a, b)
        single = [population[0]]
        self.assertIs(tournament_selection(single, rng, 1), single[0])

    def test_parents_elite_scores_and_global_random_are_unchanged(self):
        population = [member(self.context, hard=i) for i in range(3)]
        for i in population:
            i.chromosome.record_evaluation(99, .123)
        elite = population[0]
        before = [(i.fingerprint, i.evaluation, dict(i.chromosome.sections), dict(i.chromosome.enrollments),
                   i.chromosome.hard_score, i.chromosome.soft_score) for i in population]
        global_state = getstate()
        tournament_selection(population, Random(20))
        self.assertIs(population[0], elite)
        self.assertEqual(before, [(i.fingerprint, i.evaluation, dict(i.chromosome.sections), dict(i.chromosome.enrollments),
                                  i.chromosome.hard_score, i.chromosome.soft_score) for i in population])
        self.assertEqual(getstate(), global_state)

    def test_sampled_changed_genotype_is_rejected_instead_of_using_stale_score(self):
        population = [member(self.context) for _ in range(3)]
        population[0].chromosome.assign(('S1', 'C1'), None)
        with self.assertRaisesRegex(ValueError, 'changed since evaluation'):
            tournament_selection(population, Random(2))

    def test_phase_four_population_object_and_tuple_are_supported(self):
        population = initialize_population(self.context, 10, 7)
        selected = tournament_selection(population, Random(13))
        self.assertTrue(any(selected is i for i in population.individuals))
        replay = tournament_selection(population.individuals, Random(13))
        self.assertIs(selected, replay)

    def test_rng_and_unevaluated_inputs_are_rejected(self):
        with self.assertRaises(TypeError):
            tournament_selection([member(self.context)], None, 1)
        with self.assertRaisesRegex(TypeError, 'InitializedIndividual'):
            tournament_selection([scheduled(self.context.dataset)], Random(1), 1)
        with self.assertRaises(TypeError):
            tournament_selection({'not': 'a sequence'}, Random(1), 1)
        with self.assertRaisesRegex(TypeError, 'FitnessEvaluation'):
            tournament_selection([replace(member(self.context), evaluation=None)], Random(1), 1)


if __name__ == '__main__':
    unittest.main()
