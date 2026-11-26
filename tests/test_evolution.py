"""Lifecycle, exploration, exact costs, termination and recoverable reporting."""
from dataclasses import replace
from pathlib import Path
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scheduler import load_dataset
from scheduler.chromosome import Chromosome, validate_structure
from scheduler.constraints import evaluate_hard_constraints
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionFailure, EvolutionSettings as S, run_evolution
from scheduler.evolution_reporting import result_payload, restore_solution, solution_payload, write_report
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import InitializedIndividual, chromosome_fingerprint, initialize_population
from scheduler.mutation import MutationReport, MutationResult
from scheduler.operator_support import EvaluationCounts, verify_snapshot
from scheduler.repair import repair_conflicts
from scheduler.selection import tournament_selection
from scheduler.soft_fitness import evaluate_soft_fitness
from test_constraints import sample, scheduled, with_rows

ROOT = Path(__file__).resolve().parents[1]


def settings(**kwargs):
    return S(**{'population_size': 6, 'elite_count': 1, 'max_generations': 3,
                'repair_enabled': False, 'random_seed': 17, **kwargs})


def initial_with(context, chromosomes):
    result = initialize_population(context, len(chromosomes), 7)
    members = tuple(InitializedIndividual(c, evaluate_fitness(c, context), chromosome_fingerprint(c),
                                         'controlled', 1, ()) for c in chromosomes)
    return replace(result, individuals=members)


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.dataset = sample(2, demand=16)
        self.context = EvaluationContext.from_dataset(self.dataset)

    def test_initialization_only_matches_phase4(self):
        r = run_evolution(self.context, settings(max_generations=0))
        expected = initialize_population(self.context, 6, 17)
        self.assertEqual([i.fingerprint for i in r.population], [i.fingerprint for i in expected.individuals])
        self.assertEqual(len(r.generations), 1)
        self.assertEqual(r.evaluations.total, 6)
        self.assertEqual(r.stop_reasons, ('max_generations',))
        self.assertEqual(r.evolution_elapsed_seconds >= 0, True)

    def test_population_size_and_best_monotonic_with_elites(self):
        r = run_evolution(self.context, settings(max_generations=5, mutation_probability=1, repair_enabled=True))
        self.assertEqual(len(r.population), 6)
        self.assertTrue(all(g.population_size == 6 for g in r.generations))
        keys = [(g.best_hard_penalty, -g.best_soft_score) for g in r.generations]
        self.assertTrue(all(b <= a for a, b in zip(keys, keys[1:])))
        for previous, current in zip(r.generations, r.generations[1:]):
            self.assertIn(previous.best_fingerprint, current.elite_fingerprints)
        for i in r.population:
            self.assertFalse(validate_structure(i.chromosome, self.context))
            self.assertEqual(i.evaluation, evaluate_fitness(i.chromosome, self.context))

    def test_existing_tournament_and_two_independent_parent_calls(self):
        calls = []
        def select(population, rng, tournament_size):
            selected = tournament_selection(population, rng, tournament_size)
            self.assertEqual(tournament_size, 3)
            calls.append(selected)
            return selected
        with patch('scheduler.evolution.tournament_selection', side_effect=select):
            run_evolution(self.context, settings(max_generations=2))
        self.assertEqual(len(calls), 2 * 5 * 2)

    def test_crossover_enabled_and_disabled(self):
        from scheduler.crossover import course_based_crossover
        for probability in (0, 1):
            with patch('scheduler.evolution.course_based_crossover', wraps=course_based_crossover) as cross:
                r = run_evolution(self.context, settings(max_generations=1, crossover_probability=probability, mutation_probability=0))
            self.assertEqual(cross.call_count, 5 * probability)
            stats = r.generations[1].operators
            self.assertEqual(stats.crossover_applied, 5 * probability)
            self.assertEqual(stats.parent_cloned, 5 * (1 - probability))

    def test_mutation_zero_and_one(self):
        ctx = EvaluationContext.from_dataset(sample(demand=8))  # time moves always available
        for probability in (0, 1):
            r = run_evolution(ctx, settings(max_generations=1, mutation_probability=probability))
            stats = r.generations[1].operators
            self.assertEqual(stats.mutation_applied, 5 * probability)
            self.assertEqual(stats.mutation_skipped, 5 * (1 - probability))
            self.assertEqual(r.evaluations.post_mutation, 5 * probability)

    def test_repair_disabled_and_enabled_never_receives_stale_snapshot(self):
        calls = []
        def repair(c, ctx, rng, configuration, *, evaluation):
            verify_snapshot(c, ctx, evaluation)
            calls.append(chromosome_fingerprint(c))
            return repair_conflicts(c, ctx, rng, configuration, evaluation=evaluation)
        with patch('scheduler.evolution.repair_conflicts', side_effect=repair):
            disabled = run_evolution(self.context, settings(max_generations=1, repair_enabled=False))
            self.assertFalse(calls)
            enabled = run_evolution(self.context, settings(max_generations=1, mutation_probability=1, repair_enabled=True))
        self.assertEqual(len(calls), 5)
        self.assertEqual(disabled.evaluations.repair_candidates, 0)
        self.assertEqual(enabled.evaluations.repair_baseline, 0)
        self.assertEqual(enabled.evaluations.mutation_baseline, 0)

    def test_worse_child_really_enters_population_no_greedy_replacement(self):
        d = sample(2)
        ctx = EvaluationContext.from_dataset(d)
        parent = scheduled(d)
        initial = initial_with(ctx, [parent.clone() for _ in range(3)])
        child = parent.clone()
        child.replace_section(replace(child.sections['C2-01'], professor_id='P1', room_id='R1', meeting_slots=(('Saturday', 'T1'),)))
        self.assertEqual(evaluate_fitness(parent, ctx).hard.total_penalty, 0)
        self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 2)
        with patch('scheduler.evolution.initialize_population', return_value=initial), \
             patch('scheduler.evolution.course_based_crossover', side_effect=lambda *args: child.clone()):
            r = run_evolution(ctx, settings(population_size=3, max_generations=1,
                crossover_probability=1, mutation_probability=0))
        self.assertEqual([i.evaluation.hard.total_penalty for i in r.population], [0, 2, 2])
        self.assertEqual(r.generations[1].operators.worse_than_both_parents, 2)
        self.assertEqual(r.best_overall.evaluation.hard.total_penalty, 0)

    def test_mutation_worsens_and_repair_keeps_child_instead_of_parent(self):
        d = sample(2)
        ctx = EvaluationContext.from_dataset(d)
        parent = scheduled(d)
        initial = initial_with(ctx, [parent.clone() for _ in range(3)])
        def mutate(c, *args, **kwargs):
            changed = c.clone()
            changed.replace_section(replace(changed.sections['C2-01'], professor_id='P1', room_id='R1', meeting_slots=(('Saturday', 'T1'),)))
            report = MutationReport('applied', 'MoveMeeting', 'controlled worse child', 1, 1, False, False, 0,
                                    evaluations=EvaluationCounts(),
                                    unavailable_operations={})
            return MutationResult(changed, report)
        from scheduler.repair import RepairSettings
        with patch('scheduler.evolution.initialize_population', return_value=initial), \
             patch('scheduler.evolution.maybe_mutate', side_effect=mutate):
            r = run_evolution(ctx, settings(population_size=3, max_generations=1, crossover_probability=0,
                mutation_probability=1, repair_enabled=True, repair_settings=RepairSettings(max_candidate_evaluations=0)))
        self.assertEqual([i.evaluation.hard.total_penalty for i in r.population], [0, 2, 2])
        self.assertEqual(r.generations[1].operators.repair_unchanged, 2)

    def test_parent_elite_independence_and_no_reevaluation(self):
        original_initialize = initialize_population
        initial_members = []
        snapshots = []
        def initialize(*args):
            initial = original_initialize(*args)
            for i in initial.individuals:
                i.chromosome.record_evaluation(i.evaluation.hard.total_penalty, i.evaluation.soft.total_score)
                snapshots.append((i.fingerprint, i.chromosome.hard_score, i.chromosome.soft_score))
            initial_members.extend(initial.individuals)
            return initial
        with patch('scheduler.evolution.initialize_population', side_effect=initialize), \
             patch('scheduler.operator_support.evaluate_fitness', wraps=evaluate_fitness) as evaluate:
            r = run_evolution(self.context, settings(max_generations=1, crossover_probability=0, mutation_probability=0))
        self.assertEqual(evaluate.call_count, 5)  # no elite or parent fitness calls
        for member, snapshot in zip(initial_members, snapshots):
            self.assertEqual((chromosome_fingerprint(member.chromosome), member.chromosome.hard_score, member.chromosome.soft_score), snapshot)
        ranked = sorted(initial_members, key=lambda i: i.evaluation.comparison_key)
        self.assertIs(r.population[0].evaluation, ranked[0].evaluation)
        self.assertIsNot(r.population[0].chromosome, ranked[0].chromosome)
        self.assertIsNone(r.population[0].chromosome.hard_score)
        key = next(iter(r.best_overall.chromosome.enrollments))
        r.best_overall.chromosome.assign(key, None)
        self.assertEqual(r.population[0].fingerprint, chromosome_fingerprint(r.population[0].chromosome))

    def test_elite_ties_are_stable_by_population_index(self):
        d = sample()
        ctx = EvaluationContext.from_dataset(d)
        a, b, c = [scheduled(d) for _ in range(3)]
        b.replace_section(replace(b.sections['C1-01'], meeting_slots=(('Monday', 'T1'),)))
        c.replace_section(replace(c.sections['C1-01'], meeting_slots=(('Saturday', 'T2'),)))
        initial = initial_with(ctx, [b, a, c])
        with patch('scheduler.evolution.initialize_population', return_value=initial):
            r = run_evolution(ctx, settings(population_size=3, elite_count=2, max_generations=1))
        self.assertEqual(r.generations[1].elite_fingerprints, (chromosome_fingerprint(b), chromosome_fingerprint(a)))

    def test_structural_failure_reports_stage_and_preserves_last_population(self):
        bad = scheduled(self.dataset)
        bad = Chromosome({k: v for k, v in bad.sections.items() if k != 'C1-01'}, bad.enrollments)
        with patch('scheduler.evolution.course_based_crossover', return_value=bad), self.assertRaises(EvolutionFailure) as error:
            run_evolution(self.context, settings(crossover_probability=1))
        result = error.exception.result
        self.assertIn('stage=child_baseline', str(error.exception))
        self.assertEqual(result.stop_reasons, ('failure',))
        self.assertEqual(len(result.generations), 1)
        self.assertEqual(len(result.population), 6)
        self.assertTrue(all(not validate_structure(i.chromosome, self.context) for i in result.population))
        self.assertEqual(result.evaluations.total, 6)
        self.assertFalse(result_payload(result)['evaluation_accounting_complete'])

    def test_bad_initializer_reports_failure(self):
        with patch('scheduler.evolution.initialize_population', side_effect=ValueError('bad initialization')), self.assertRaisesRegex(EvolutionFailure, 'initialization'):
            run_evolution(self.context, settings())

    def test_reproducibility_and_global_rng_dataset_context_unchanged(self):
        before = random.getstate(), repr(self.context), self.context.dataset_fingerprint, self.dataset.tables
        a = run_evolution(self.context, settings(repair_enabled=True))
        b = run_evolution(self.context, settings(repair_enabled=True))
        self.assertEqual(a.evaluations, b.evaluations)
        self.assertEqual([i.fingerprint for i in a.population], [i.fingerprint for i in b.population])
        for first, second in zip(a.generations, b.generations):
            self.assertEqual(replace(first, generation_elapsed_seconds=0, cumulative_elapsed_seconds=0),
                             replace(second, generation_elapsed_seconds=0, cumulative_elapsed_seconds=0))
        self.assertEqual(before, (random.getstate(), repr(self.context), self.context.dataset_fingerprint, self.dataset.tables))

    def test_hash_seed_reproducibility(self):
        code = '''from test_constraints import sample
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings,run_evolution
r=run_evolution(EvaluationContext.from_dataset(sample(2,16)),EvolutionSettings(population_size=4,elite_count=1,max_generations=2,random_seed=17))
print([i.fingerprint for i in r.population],r.evaluations)
'''
        outputs = []
        for seed in ('1', '98'):
            env = {**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONPATH': os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'tests')))}
            outputs.append(subprocess.check_output([sys.executable, '-c', code], env=env, cwd=ROOT, text=True))
        self.assertEqual(*outputs)

    def test_first_feasible_opt_in(self):
        ctx = EvaluationContext.from_dataset(sample())
        r = run_evolution(ctx, settings(stop_on_first_feasible=True))
        self.assertEqual(r.stop_reasons, ('first_feasible',))
        self.assertEqual(len(r.generations), 1)
        r = run_evolution(ctx, settings(stop_on_first_feasible=False))
        self.assertEqual(len(r.generations), 4)
        self.assertIsNotNone(r.best_feasible)

    def test_stagnation(self):
        ctx = EvaluationContext.from_dataset(sample())
        r = run_evolution(ctx, settings(max_generations=10, stagnation_generations=2, crossover_probability=0, mutation_probability=0))
        self.assertEqual(r.stop_reasons, ('stagnation',))
        self.assertEqual(len(r.generations), 3)

    def test_soft_improvement_resets_stagnation(self):
        d = sample(demand=9)
        ctx = EvaluationContext.from_dataset(d)
        full = scheduled(d)
        partial = full.clone()
        partial.assign(next(iter(partial.enrollments)), None)
        initial = initial_with(ctx, [partial.clone() for _ in range(3)])
        with patch('scheduler.evolution.initialize_population', return_value=initial), \
             patch('scheduler.evolution.course_based_crossover', side_effect=lambda *args: full.clone()):
            r = run_evolution(ctx, settings(population_size=3, max_generations=10, stagnation_generations=1,
                                           crossover_probability=1, mutation_probability=0))
        self.assertEqual([g.best_hard_penalty for g in r.generations], [0, 0, 0])
        self.assertEqual([g.best_soft_score for g in r.generations], [8/9, 1, 1])
        self.assertEqual(len(r.generations), 3)  # Soft improvement prevents stopping after generation 1

    def test_exact_call_audit_with_real_hard_and_soft(self):
        d = sample(3, demand=16)
        d = with_rows(d, TimeSlots=d.tables['TimeSlots'][:1])
        ctx = EvaluationContext.from_dataset(d)
        with patch('scheduler.initialization.evaluate_fitness', wraps=evaluate_fitness) as initialize, \
             patch('scheduler.operator_support.evaluate_fitness', wraps=evaluate_fitness) as external, \
             patch('scheduler.repair.evaluate_fitness', wraps=evaluate_fitness) as repair, \
             patch('scheduler.fitness.evaluate_hard_constraints', wraps=evaluate_hard_constraints) as hard, \
             patch('scheduler.fitness.evaluate_soft_fitness', wraps=evaluate_soft_fitness) as soft:
            r = run_evolution(ctx, settings(max_generations=2, mutation_probability=1, repair_enabled=True))
        self.assertEqual(r.evaluations.initialization, initialize.call_count)
        self.assertEqual(r.evaluations.child_baseline + r.evaluations.post_mutation, external.call_count)
        self.assertEqual(r.evaluations.repair_candidates, repair.call_count)
        self.assertGreater(repair.call_count, 0)
        self.assertEqual(r.evaluations.total, hard.call_count)
        self.assertEqual(r.evaluations.total, soft.call_count)
        self.assertEqual(r.evaluations.total, sum(g.fitness_evaluation_count for g in r.generations))

    def test_budget_initialization_only_and_partial_generation(self):
        for budget in (6, 7, 9, 13):
            with patch('scheduler.initialization.evaluate_fitness', wraps=evaluate_fitness) as init, \
                 patch('scheduler.operator_support.evaluate_fitness', wraps=evaluate_fitness) as external:
                r = run_evolution(self.context, settings(max_generations=30, max_fitness_evaluations=budget,
                                                       mutation_probability=0, repair_enabled=False))
            self.assertEqual(r.evaluations.total, budget)
            self.assertEqual(init.call_count + external.call_count, budget)
            self.assertIn('fitness_budget', r.stop_reasons)
            self.assertEqual(len(r.population), 6)
            partial = r.partial_generation.evaluations.total if r.partial_generation else 0
            self.assertEqual(sum(g.fitness_evaluation_count for g in r.generations) + partial, budget)

    def test_budget_reserves_possible_mutation_and_clamps_repair(self):
        d = sample(3, 16)
        ctx = EvaluationContext.from_dataset(with_rows(d, TimeSlots=d.tables['TimeSlots'][:1]))
        for budget in (6, 7, 8, 10, 20):
            with patch('scheduler.initialization.evaluate_fitness', wraps=evaluate_fitness) as init, \
                 patch('scheduler.operator_support.evaluate_fitness', wraps=evaluate_fitness) as external, \
                 patch('scheduler.repair.evaluate_fitness', wraps=evaluate_fitness) as repair:
                r = run_evolution(ctx, settings(max_generations=10, max_fitness_evaluations=budget,
                                               mutation_probability=1, repair_enabled=True))
            self.assertLessEqual(r.evaluations.total, budget)
            self.assertEqual(r.evaluations.total, init.call_count + external.call_count + repair.call_count)
            self.assertIn('fitness_budget', r.stop_reasons)

    def test_positive_hard_is_never_reported_feasible(self):
        d = sample(demand=7)
        d = with_rows(d, Students=tuple(replace(s, min_credits=3) for s in d.tables['Students']))
        r = run_evolution(EvaluationContext.from_dataset(d), settings(max_generations=2))
        self.assertEqual(r.best_overall.evaluation.hard.total_penalty, 21)
        self.assertFalse(r.feasible_found)
        self.assertIsNone(r.best_feasible)
        report = result_payload(r)
        self.assertFalse(report['best_overall']['feasible'])
        self.assertTrue(report['best_overall']['violations'])

    def test_best_seen_includes_intermediate_before_worsening_mutation(self):
        d = sample(2)
        ctx = EvaluationContext.from_dataset(d)
        full = scheduled(d)
        bad = full.clone()
        bad.replace_section(replace(bad.sections['C2-01'], professor_id='P1', room_id='R1', meeting_slots=(('Saturday', 'T1'),)))
        initial = initial_with(ctx, [bad.clone() for _ in range(3)])
        def mutate(*args, **kwargs):
            return MutationResult(bad.clone(), MutationReport('applied', 'MoveMeeting', 'controlled', 1, 1,
                                  False, False, 0, EvaluationCounts(), {}))
        with patch('scheduler.evolution.initialize_population', return_value=initial), \
             patch('scheduler.evolution.course_based_crossover', side_effect=lambda *args: full.clone()), \
             patch('scheduler.evolution.maybe_mutate', side_effect=mutate):
            r = run_evolution(ctx, settings(population_size=3, crossover_probability=1, mutation_probability=1, max_generations=1))
        self.assertEqual(r.best_overall.evaluation.hard.total_penalty, 0)
        self.assertEqual(r.best_feasible.evaluation.hard.total_penalty, 0)
        self.assertEqual(r.final_population_best.evaluation.hard.total_penalty, 2)

    def test_operator_statistics_partition_and_self_mating(self):
        r = run_evolution(self.context, settings())
        for g in r.generations[1:]:
            s = g.operators
            self.assertEqual(s.better_than_both_parents + s.between_parents + s.worse_than_both_parents, s.children)
            self.assertEqual(s.children, 5)
            self.assertEqual(s.mutation_applied + s.mutation_skipped + s.mutation_failed + s.mutation_not_applicable, s.children)
            self.assertLessEqual(s.identical_to_a_parent, s.children)
            self.assertEqual(s.self_mating_rate, s.self_matings / s.children)

    def test_same_parent_is_allowed_and_reported(self):
        with patch('scheduler.evolution.tournament_selection', side_effect=lambda population, *args: population[0]):
            r = run_evolution(self.context, settings(max_generations=1))
        self.assertEqual(r.generations[1].operators.self_matings, 5)
        self.assertEqual(r.generations[1].operators.self_mating_rate, 1)

    def test_stale_repair_output_is_not_admitted(self):
        from scheduler.repair import RepairResult, RepairReport
        def stale(c, ctx, rng, configuration, *, evaluation):
            changed = c.clone()
            s = next(s for s in changed.sections.values() if s.active)
            slot = next(k for k in ctx.slots if k not in s.meeting_slots)
            changed.replace_section(replace(s, meeting_slots=(slot,)))
            return RepairResult(changed, RepairReport('unchanged', 'controlled stale output'), evaluation)
        with patch('scheduler.evolution.repair_conflicts', side_effect=stale), self.assertRaises(EvolutionFailure) as error:
            run_evolution(self.context, settings(repair_enabled=True))
        self.assertIn('stale', str(error.exception))
        self.assertEqual(len(error.exception.result.generations), 1)

    def test_cli_writes_recoverable_report_and_rejects_input_overwrite(self):
        (ROOT / 'outputs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'outputs') as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to((ROOT / 'outputs').resolve()))
            path = Path(directory) / 'cli.json'
            subprocess.check_output([sys.executable, str(ROOT / 'scripts/run_evolution_experiment.py'),
                '--population-size', '3', '--elite-count', '1', '--max-generations', '0', '--output', str(path)], cwd=ROOT)
            report = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(report['completed_generations'], 0)
            self.assertEqual(report['evaluation_usage']['total'], 3)
            self.assertTrue(Path(report['solutions_path']).exists())
        input_path = ROOT / 'data/UniversityDataset_Corrected.xlsx'
        process = subprocess.run([sys.executable, str(ROOT / 'scripts/run_evolution_experiment.py'),
            '--output', str(input_path)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn('overwrite', process.stderr)

    def test_settings_reject_invalid_or_all_elites(self):
        invalid = ({'elite_count': 0}, {'elite_count': 6}, {'population_size': 1}, {'tournament_size': 7},
            {'max_generations': -1}, {'max_fitness_evaluations': 5}, {'stagnation_generations': 0},
            {'mutation_probability': float('nan')}, {'crossover_probability': 2}, {'repair_enabled': 1})
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                settings(**kwargs)
        self.assertEqual(settings(mutation_probability=0).mutation_settings.mutation_probability, 0)

    def test_json_solution_roundtrip_and_tamper_rejection(self):
        r = run_evolution(self.context, settings(max_generations=1))
        (ROOT / 'outputs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'outputs') as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to((ROOT / 'outputs').resolve()))
            path = Path(directory) / 'report.json'
            write_report(r, path)
            report = json.loads(path.read_text(encoding='utf-8'))
            solutions = json.loads(Path(report['solutions_path']).read_text(encoding='utf-8'))
            restored = restore_solution(solutions['best_overall'], self.context)
            self.assertEqual(chromosome_fingerprint(restored), r.best_overall.fingerprint)
            self.assertEqual(evaluate_fitness(restored, self.context), r.best_overall.evaluation)
            self.assertEqual(report['evaluation_usage']['total'], r.evaluations.total)
            self.assertEqual(len(report['generations']), 2)
            self.assertTrue(report['evaluation_accounting_complete'])
        payload = solution_payload(r.best_overall, r.context_id)
        payload['enrollments'].append(payload['enrollments'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            restore_solution(payload, self.context)
        payload = solution_payload(r.best_overall, r.context_id)
        payload['fingerprint'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            restore_solution(payload, self.context)
        payload['context_id'] = ['other', 'policy']
        with self.assertRaisesRegex(ValueError, 'Context'):
            restore_solution(payload, self.context)

    def test_real_dataset_one_generation_and_input_unchanged(self):
        import hashlib
        path = ROOT / 'data/UniversityDataset_Corrected.xlsx'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        d = load_dataset(path)
        ctx = EvaluationContext.from_dataset(d)
        r = run_evolution(ctx, settings(max_generations=1, repair_enabled=True))
        self.assertEqual(len(r.population), 6)
        self.assertEqual(len(r.generations), 2)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        for member in r.population:
            self.assertEqual(member.evaluation, evaluate_fitness(member.chromosome, ctx))


if __name__ == '__main__':
    unittest.main()
