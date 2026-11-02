from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
from random import getstate
from statistics import fmean
import subprocess
import sys
import unittest
from unittest.mock import patch

from scheduler.chromosome import Chromosome, SectionGene, StructureIssue, validate_structure
from scheduler.constraints import StructuralValidationError
from scheduler.domain import Qualification, Room
from scheduler.evaluation_context import EvaluationContext
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import (InitializationSettings, PopulationInitializer,
                                      chromosome_fingerprint, initialize_chromosome,
                                      initialize_population, measure_diversity)
from test_constraints import sample, with_rows


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = sample(3, demand=16)
        self.context = EvaluationContext.from_dataset(self.dataset)

    def test_equal_seed_replays_population_and_initial_evaluations(self):
        first = initialize_population(self.context, 10, random_seed=123)
        second = initialize_population(self.context, 10, random_seed=123)
        self.assertEqual([i.fingerprint for i in first.individuals], [i.fingerprint for i in second.individuals])
        self.assertEqual([i.evaluation for i in first.individuals], [i.evaluation for i in second.individuals])
        self.assertEqual(first.diversity, second.diversity)
        for field in ('generation_attempts', 'rejected_duplicates', 'accepted_duplicates', 'best_index', 'strategy_counts'):
            self.assertEqual(getattr(first.statistics, field), getattr(second.statistics, field))

    def test_different_seed_changes_population_in_diverse_space(self):
        first = initialize_population(self.context, 10, random_seed=1)
        second = initialize_population(self.context, 10, random_seed=2)
        self.assertNotEqual([i.fingerprint for i in first.individuals], [i.fingerprint for i in second.individuals])

    def test_seed_replays_across_different_python_hash_seeds(self):
        root = Path(__file__).resolve().parents[1]
        code = ('import sys,json;sys.path.insert(0,"tests");'
                'from test_constraints import sample;'
                'from scheduler.evaluation_context import EvaluationContext;'
                'from scheduler.initialization import initialize_population;'
                'r=initialize_population(EvaluationContext.from_dataset(sample(3,16)),10,123);'
                'print(json.dumps([i.fingerprint for i in r.individuals]))')
        outputs = []
        for hash_seed in ('1', '99'):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(root / 'src'))
            outputs.append(json.loads(subprocess.check_output([sys.executable, '-c', code], cwd=root, env=env, text=True)))
        self.assertEqual(outputs[0], outputs[1])

    def test_population_sizes_one_ten_and_hundred_are_structurally_valid(self):
        for size in (1, 10, 100):
            with self.subTest(size=size):
                result = initialize_population(self.context, size, random_seed=42)
                self.assertEqual(len(result.individuals), size)
                self.assertEqual(result.statistics.count, size)
                self.assertLessEqual(result.statistics.generation_attempts, size * result.settings.max_attempts_per_individual)
                for individual in result.individuals:
                    self.assertEqual(validate_structure(individual.chromosome, self.context), ())
                    self.assertEqual(set(individual.chromosome.sections), set(self.context.expected_sections))
                    self.assertEqual(set(individual.chromosome.enrollments), self.context.requests)

    def test_input_context_and_global_random_state_unchanged(self):
        before_tables = dict(self.dataset.tables)
        before_rng = getstate()
        before_context = (dict(self.context.demand), self.context.diagnostics, dict(self.context.priorities), self.context.context_id)
        with patch('scheduler.evaluation_context._build_context', side_effect=AssertionError('context rebuilt')):
            initialize_population(self.context, 10, random_seed=14)
        self.assertEqual(dict(self.dataset.tables), before_tables)
        self.assertEqual(getstate(), before_rng)
        self.assertEqual((dict(self.context.demand), self.context.diagnostics, dict(self.context.priorities), self.context.context_id), before_context)

    def test_individuals_are_independent_and_caches_remain_unset(self):
        result = initialize_population(self.context, 10, random_seed=42)
        first, second = (i.chromosome for i in result.individuals[:2])
        before = chromosome_fingerprint(second)
        key = next(key for key, sid in first.enrollments.items() if sid is not None)
        first.assign(key, None)
        self.assertEqual(chromosome_fingerprint(second), before)
        self.assertIsNot(first, second)
        self.assertTrue(all(i.chromosome.hard_score is None and i.chromosome.soft_score is None for i in result.individuals))

    def test_single_professor_and_room_are_valid_reusable_options(self):
        d = sample(demand=24)
        d = with_rows(d, Classrooms=(replace(d.tables['Classrooms'][0], capacity=10),))
        ctx = EvaluationContext.from_dataset(d)
        individual = initialize_chromosome(ctx, random_seed=10)
        active = [s for s in individual.chromosome.sections.values() if s.active]
        self.assertEqual(len(active), 3)
        self.assertEqual({s.professor_id for s in active}, {'P1'})
        self.assertEqual({s.room_id for s in active}, {'R1'})
        self.assertEqual(individual.chromosome.section_sizes(), {'C1-01': 8, 'C1-02': 8, 'C1-03': 8})
        self.assertEqual(individual.evaluation.hard.counts_by_type['H07_SECTION_OVERFLOW'], 0)

    def test_missing_professor_or_room_leaves_inactive_groups_and_reports_reason(self):
        for changed, kind in (({'ProfessorCourses': ()}, 'NO_QUALIFIED_PROFESSOR'),
                              ({'Classrooms': ()}, 'NO_COMPATIBLE_ROOM')):
            with self.subTest(kind=kind):
                ctx = EvaluationContext.from_dataset(with_rows(sample(), **changed))
                result = initialize_population(ctx, 10, random_seed=0)
                self.assertTrue(any(i.kind == kind and i.course_id == 'C1' for i in result.limitations))
                self.assertTrue(all(not s.active for i in result.individuals for s in i.chromosome.sections.values()))
                self.assertTrue(all(sid is None for i in result.individuals for sid in i.chromosome.enrollments.values()))
                self.assertEqual(result.statistics.generation_attempts, 10)
                self.assertEqual(result.statistics.unique_count, 1)

    def test_low_demand_retains_requests_without_fabricated_groups(self):
        ctx = EvaluationContext.from_dataset(sample(demand=7))
        individual = initialize_chromosome(ctx, random_seed=2)
        self.assertEqual(len(individual.chromosome.sections), 0)
        self.assertEqual(len(individual.chromosome.enrollments), 7)
        self.assertTrue(all(sid is None for sid in individual.chromosome.enrollments.values()))
        self.assertTrue(any(i.kind == 'LOW_DEMAND' and i.unassigned_count == 7 for i in individual.issues))

    def test_capacity_shortage_keeps_none_and_serves_highest_priorities(self):
        d = sample(demand=15)
        d = with_rows(d, Classrooms=(replace(d.tables['Classrooms'][0], capacity=10),),
                      StudentRequests=tuple(replace(r, priority=(i % 10) + 1) for i, r in enumerate(d.tables['StudentRequests'])))
        ctx = EvaluationContext.from_dataset(d)
        individual = initialize_chromosome(ctx, random_seed=1)
        served = [key for key, sid in individual.chromosome.enrollments.items() if sid is not None]
        unanswered = [key for key, sid in individual.chromosome.enrollments.items() if sid is None]
        self.assertEqual((len(served), len(unanswered)), (10, 5))
        self.assertGreaterEqual(min(ctx.priorities[key] for key in served), max(ctx.priorities[key] for key in unanswered))
        self.assertEqual(individual.evaluation.hard.counts_by_type['H07_SECTION_OVERFLOW'], 0)
        self.assertTrue(any(i.kind == 'INSUFFICIENT_SELECTED_CAPACITY' and i.unassigned_count == 5 for i in individual.issues))

    def test_capacity_lookahead_uses_room_large_enough_to_serve_requests(self):
        d = sample(demand=16)
        d = with_rows(d, Classrooms=(Room('Small', 'F', 'Theory', 8, 'Board'), Room('Large', 'F', 'Theory', 20, 'Board')))
        ctx = EvaluationContext.from_dataset(d)
        settings = InitializationSettings(strategies=('compact',))
        individual = initialize_chromosome(ctx, random_seed=3, settings=settings)
        active = [s for s in individual.chromosome.sections.values() if s.active]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].room_id, 'Large')
        self.assertEqual(len(individual.evaluation.hard.metrics.unassigned_requests), 0)

    def test_multiple_group_counts_mixed_strategies_and_population_diversity(self):
        result = initialize_population(self.context, 30, random_seed=13)
        self.assertEqual(set(result.statistics.strategy_counts), {'compact', 'spread', 'random'})
        self.assertEqual(result.statistics.unique_count, 30)
        self.assertGreater(result.diversity.unique_active_sets, 1)
        self.assertGreater(result.diversity.varying_professor_choices, 0)
        self.assertGreater(result.diversity.varying_room_choices, 0)
        self.assertGreater(result.diversity.unique_timetables, 1)
        self.assertGreater(result.diversity.unique_enrollments, 1)
        self.assertLess(result.statistics.min_active_sections, result.statistics.max_active_sections)

    def test_time_constraints_and_resource_eligibility(self):
        d = sample(sessions=2)
        d = with_rows(d, TimeSlots=tuple(replace(slot, is_allowed=slot.day == 'Saturday') for slot in d.tables['TimeSlots']),
                      Classrooms=d.tables['Classrooms'] + (Room('WrongRoom', 'F', 'NotTheory', 100, 'Board'),),
                      ProfessorCourses=d.tables['ProfessorCourses'] + (Qualification('Unqualified', 'C1', False, 10),))
        ctx = EvaluationContext.from_dataset(d)
        result = initialize_population(ctx, 10, random_seed=44)
        for individual in result.individuals:
            for section in individual.chromosome.sections.values():
                if section.active:
                    self.assertEqual(section.professor_id, 'P1')
                    self.assertEqual(section.room_id, 'R1')
                    self.assertEqual(len(section.meeting_slots), 2)
                    self.assertEqual(len(set(section.meeting_slots)), 2)
                    self.assertTrue(all(ctx.slots[key].is_allowed for key in section.meeting_slots))
            self.assertEqual(individual.evaluation.hard.counts_by_type['H02_DISALLOWED_SLOT'], 0)

    def test_not_enough_allowed_slots_reports_failure_without_activating(self):
        d = sample(sessions=2)
        d = with_rows(d, TimeSlots=(d.tables['TimeSlots'][0],))
        ctx = EvaluationContext.from_dataset(d)
        result = initialize_population(ctx, 100, random_seed=3)
        self.assertEqual(result.statistics.generation_attempts, 100)
        self.assertEqual(result.statistics.unique_count, 1)
        self.assertTrue(any(i.kind == 'INSUFFICIENT_ALLOWED_SLOTS' for i in result.limitations))
        self.assertEqual(len(result.best_individual.evaluation.hard.metrics.unassigned_requests), 8)

    def test_initialization_does_not_solve_all_resource_conflicts(self):
        d = sample(2)
        d = with_rows(d, Professors=d.tables['Professors'][:1], Classrooms=d.tables['Classrooms'][:1],
                      ProfessorCourses=tuple(q for q in d.tables['ProfessorCourses'] if q.professor_id == 'P1'),
                      TimeSlots=d.tables['TimeSlots'][:1])
        ctx = EvaluationContext.from_dataset(d)
        individual = initialize_chromosome(ctx, random_seed=11)
        self.assertEqual(individual.evaluation.hard.total_penalty, 2)
        self.assertEqual(individual.evaluation.hard.counts_by_type['H04_PROFESSOR_OVERLAP'], 1)
        self.assertEqual(individual.evaluation.hard.counts_by_type['H04_ROOM_OVERLAP'], 1)
        self.assertEqual(validate_structure(individual.chromosome, ctx), ())

    def test_full_fitness_for_every_returned_individual_without_assign_calls(self):
        with (patch.object(Chromosome, 'assign', side_effect=AssertionError('per-request copy')),
             patch.object(Chromosome, 'replace_section', side_effect=AssertionError('per-section copy')),
             patch('scheduler.initialization.evaluate_fitness', wraps=evaluate_fitness) as evaluate):
            result = initialize_population(self.context, 10, random_seed=7)
        self.assertEqual(evaluate.call_count, 10)
        for individual in result.individuals:
            self.assertEqual(individual.evaluation, evaluate_fitness(individual.chromosome, self.context))

    def test_singleton_and_bounded_duplicate_search_are_reported(self):
        d = sample()
        # Exactly one teacher, room, potential slot, allowed time, all requests served.
        d = with_rows(d, TimeSlots=d.tables['TimeSlots'][:1])
        ctx = EvaluationContext.from_dataset(d)
        settings = InitializationSettings(max_attempts_per_individual=2)
        with patch('scheduler.initialization.evaluate_fitness', wraps=evaluate_fitness) as evaluate:
            result = initialize_population(ctx, 10, random_seed=3, settings=settings)
        self.assertEqual(evaluate.call_count, 10)
        self.assertEqual(result.statistics.unique_count, 1)
        self.assertEqual(result.statistics.accepted_duplicates, 9)
        self.assertEqual(result.statistics.generation_attempts, 19)
        self.assertEqual(result.statistics.rejected_duplicates, 9)
        self.assertTrue(any(i.kind == 'DIVERSITY_LIMIT_REACHED' for i in result.limitations))
        self.assertEqual(len({id(i.chromosome) for i in result.individuals}), 10)
        result.individuals[0].chromosome.assign(('S1', 'C1'), None)
        self.assertEqual(result.individuals[1].chromosome.section_sizes()['C1-01'], 8)

    def test_statistics_match_manual_recalculation(self):
        result = initialize_population(self.context, 10, random_seed=111)
        stats = result.statistics
        hard = [i.evaluation.hard.total_penalty for i in result.individuals]
        self.assertEqual((stats.min_hard_penalty, stats.mean_hard_penalty, stats.max_hard_penalty), (min(hard), fmean(hard), max(hard)))
        self.assertEqual(stats.mean_soft_score, fmean(i.evaluation.soft.total_score for i in result.individuals))
        self.assertEqual(stats.mean_request_coverage, fmean(i.evaluation.soft.request_coverage for i in result.individuals))
        self.assertEqual(stats.mean_active_sections, fmean(sum(s.active for s in i.chromosome.sections.values()) for i in result.individuals))
        self.assertEqual(stats.mean_unassigned_requests, fmean(len(i.evaluation.hard.metrics.unassigned_requests) for i in result.individuals))
        self.assertEqual(stats.feasible_count, sum(value == 0 for value in hard))
        self.assertEqual(stats.best_comparison_key, min(i.evaluation.comparison_key for i in result.individuals))
        self.assertIs(result.best_individual, result.individuals[stats.best_index])
        self.assertEqual(stats.unique_count, len({i.fingerprint for i in result.individuals}))
        self.assertEqual(stats.generation_attempts, stats.count + stats.rejected_duplicates)
        self.assertEqual(stats.accepted_duplicates, stats.count - stats.unique_count)
        self.assertGreaterEqual(stats.elapsed_seconds, 0)
        self.assertGreaterEqual(stats.preparation_seconds, 0)

    def test_diversity_counts_can_be_calculated_by_hand(self):
        first = Chromosome({'C-01': SectionGene('C-01', 'C')}, {('S', 'C'): None})
        second = Chromosome({'C-01': SectionGene('C-01', 'C', True, 'P', 'R', [('Saturday', 'T1')])}, {('S', 'C'): 'C-01'})
        stats = measure_diversity((first, second))
        self.assertEqual((stats.unique_chromosomes, stats.unique_active_sets, stats.unique_enrollments, stats.varying_requests), (2, 2, 2, 1))
        self.assertEqual((stats.varying_professor_choices, stats.varying_room_choices, stats.varying_active_slots), (0, 0, 1))
        self.assertEqual(measure_diversity((first, first)).unique_chromosomes, 1)
        self.assertEqual(measure_diversity(()).unique_chromosomes, 0)

    def test_invalid_parameters_are_rejected(self):
        for size in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                initialize_population(self.context, size)
        for seed in (None, 'seed', True):
            with self.assertRaises(ValueError):
                initialize_chromosome(self.context, seed)
        for settings in ({'max_attempts_per_individual': 0}, {'strategies': ()}, {'strategies': ('repair',)}):
            with self.assertRaises(ValueError):
                InitializationSettings(**settings)
        with self.assertRaises(TypeError):
            initialize_chromosome(self.dataset)

    def test_single_chromosome_api_replays_and_reports_initial_evaluation(self):
        a = initialize_chromosome(self.context, 123)
        b = initialize_chromosome(self.context, 123)
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.evaluation, b.evaluation)
        self.assertEqual(a.attempts, 1)
        self.assertEqual(a.evaluation.hard.context_id, self.context.context_id)

    def test_structural_failure_is_not_hidden_or_retried_forever(self):
        issue = StructureIssue('sections[X]', 'unexpected slot')
        with patch('scheduler.initialization.validate_structure', return_value=(issue,)) as validate:
            with self.assertRaises(StructuralValidationError) as error:
                initialize_population(self.context, 100)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(error.exception.issues, (issue,))

    def test_reports_are_immutable_and_inactive_slots_have_no_resources(self):
        result = initialize_population(self.context, 10, random_seed=5)
        with self.assertRaises(FrozenInstanceError):
            result.statistics.count = 999
        with self.assertRaises(TypeError):
            result.statistics.strategy_counts['repair'] = 1
        for individual in result.individuals:
            for section in individual.chromosome.sections.values():
                if not section.active:
                    self.assertEqual((section.professor_id, section.room_id, section.meeting_slots), (None, None, ()))


if __name__ == '__main__':
    unittest.main()
