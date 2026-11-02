from dataclasses import replace
import json
import os
from pathlib import Path
from random import Random, getstate
import subprocess
import sys
import unittest
from unittest.mock import patch

from scheduler.chromosome import Chromosome, SectionGene, build_empty_chromosome, validate_structure
from scheduler.constraints import StructuralValidationError
from scheduler.crossover import course_based_crossover
from scheduler.evaluation_context import EvaluationContext
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import (InitializationSettings, chromosome_fingerprint,
                                      initialize_chromosome, initialize_population)
from scheduler.selection import tournament_selection
from test_constraints import sample, scheduled, with_rows


def parents(course_count=2):
    context = EvaluationContext.from_dataset(sample(course_count, demand=16))
    a = initialize_chromosome(context, 10, InitializationSettings(strategies=('compact',))).chromosome
    b = initialize_chromosome(context, 20, InitializationSettings(strategies=('spread',))).chromosome
    b.assign(next(iter(sorted(context.requests))), None)
    return context, a, b


def block(chromosome, course_id):
    return (tuple(sorted((sid, s) for sid, s in chromosome.sections.items() if s.course_id == course_id)),
            tuple(sorted((key, sid) for key, sid in chromosome.enrollments.items() if key[1] == course_id)))


class CrossoverTests(unittest.TestCase):
    def setUp(self):
        self.context, self.a, self.b = parents()

    def assert_whole_blocks(self, child, context, a, b):
        self.assertEqual(set(child.sections), set(context.expected_sections))
        self.assertEqual(set(child.enrollments), context.requests)
        self.assertEqual(validate_structure(child, context), ())
        for cid in context.courses:
            self.assertIn(block(child, cid), (block(a, cid), block(b, cid)))
        for request, sid in child.enrollments.items():
            if sid is not None:
                self.assertTrue(child.sections[sid].active)
                self.assertEqual(child.sections[sid].course_id, request[1])

    def test_single_course_copies_a_complete_parent_block(self):
        context, a, b = parents(1)
        fingerprints = set()
        for seed in range(20):
            child = course_based_crossover(a, b, context, Random(seed))
            self.assert_whole_blocks(child, context, a, b)
            fingerprints.add(chromosome_fingerprint(child))
        self.assertEqual(fingerprints, {chromosome_fingerprint(a), chromosome_fingerprint(b)})

    def test_multiple_courses_include_both_differing_donors_by_default(self):
        for seed in range(20):
            child = course_based_crossover(self.a, self.b, self.context, Random(seed))
            self.assert_whole_blocks(child, self.context, self.a, self.b)
            donors = [0 if block(child, cid) == block(self.a, cid) else 1 for cid in self.context.courses]
            self.assertEqual(set(donors), {0, 1})
            self.assertNotIn(chromosome_fingerprint(child), {chromosome_fingerprint(self.a), chromosome_fingerprint(self.b)})

    def test_active_counts_and_none_are_inherited_with_the_same_donor(self):
        context = self.context
        key = next(iter(sorted(context.requests)))
        found_none = found_assigned = False
        for seed in range(20):
            child = course_based_crossover(self.a, self.b, context, Random(seed))
            self.assert_whole_blocks(child, context, self.a, self.b)
            for cid in context.courses:
                count = sum(s.active for s in child.sections.values() if s.course_id == cid)
                self.assertIn(count, (1, 2))
            found_none |= child.enrollments[key] is None
            found_assigned |= child.enrollments[key] is not None
        self.assertTrue(found_none and found_assigned)

    def test_parent_and_elite_maps_and_scores_stay_unchanged(self):
        self.a.record_evaluation(19, .4)
        self.b.record_evaluation(29, .8)
        elite = self.a
        before = [(chromosome_fingerprint(p), dict(p.sections), dict(p.enrollments), p.hard_score, p.soft_score)
                  for p in (self.a, self.b)]
        state = getstate()
        child = course_based_crossover(self.a, self.b, self.context, Random(4))
        self.assertIs(elite, self.a)
        self.assertEqual(before, [(chromosome_fingerprint(p), dict(p.sections), dict(p.enrollments), p.hard_score, p.soft_score)
                                  for p in (self.a, self.b)])
        self.assertIsNone(child.hard_score)
        self.assertIsNone(child.soft_score)
        self.assertEqual(getstate(), state)

    def test_child_mappings_and_records_are_independent(self):
        child = course_based_crossover(self.a, self.b, self.context, Random(3))
        before = [chromosome_fingerprint(p) for p in (self.a, self.b)]
        for sid in child.sections:
            self.assertIsNot(child.sections[sid], self.a.sections[sid])
            self.assertIsNot(child.sections[sid], self.b.sections[sid])
        request = next(key for key, sid in child.enrollments.items() if sid is not None)
        child.assign(request, None)
        section = next(s for s in child.sections.values() if s.active)
        child.replace_section(replace(section, professor_id='P2'))
        self.assertEqual([chromosome_fingerprint(p) for p in (self.a, self.b)], before)

    def test_seed_and_input_dictionary_order_do_not_change_replay(self):
        first = course_based_crossover(self.a, self.b, self.context, Random(42))
        reverse = lambda c: Chromosome(dict(reversed(list(c.sections.items()))), dict(reversed(list(c.enrollments.items()))))
        second = course_based_crossover(reverse(self.a), reverse(self.b), self.context, Random(42))
        self.assertEqual(chromosome_fingerprint(first), chromosome_fingerprint(second))
        children = {chromosome_fingerprint(course_based_crossover(self.a, self.b, self.context, Random(seed))) for seed in range(20)}
        self.assertGreater(len(children), 1)

    def test_python_hash_seed_does_not_affect_selection_or_crossover(self):
        root = Path(__file__).resolve().parents[1]
        code = ('import sys,json;from random import Random;sys.path.insert(0,"tests");'
                'from test_constraints import sample;'
                'from scheduler.evaluation_context import EvaluationContext;'
                'from scheduler.initialization import initialize_population,chromosome_fingerprint;'
                'from scheduler.selection import tournament_selection;'
                'from scheduler.crossover import course_based_crossover;'
                'ctx=EvaluationContext.from_dataset(sample(3,16));p=initialize_population(ctx,10,2026);'
                'rng=Random(42);a=tournament_selection(p,rng);b=tournament_selection(p,rng);'
                'c=course_based_crossover(a.chromosome,b.chromosome,ctx,rng);'
                'print(json.dumps([a.fingerprint,b.fingerprint,chromosome_fingerprint(c)]))')
        results = []
        for hash_seed in ('1', '99'):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(root / 'src'))
            results.append(json.loads(subprocess.check_output([sys.executable, '-c', code], cwd=root, env=env, text=True)))
        self.assertEqual(results[0], results[1])

    def test_invalid_parent_fails_before_drawing_rng_or_repairing(self):
        invalid = Chromosome({}, self.a.enrollments)
        rng = Random(2)
        before = rng.getstate()
        for a, b in ((invalid, self.b), (self.a, invalid)):
            with self.assertRaises(StructuralValidationError) as error:
                course_based_crossover(a, b, self.context, rng)
            self.assertTrue(error.exception.issues)
            self.assertEqual(rng.getstate(), before)

    def test_worse_child_is_returned_without_evaluation_rejection_or_repair(self):
        d = sample(2)
        ctx = EvaluationContext.from_dataset(d)
        a = scheduled(d)
        for sid in a.sections:
            a.replace_section(replace(a.sections[sid], professor_id='P1', room_id='R1'))
        b = a.clone()
        b.replace_section(replace(b.sections['C1-01'], meeting_slots=[('Saturday', 'T2')]))
        b.replace_section(replace(b.sections['C2-01'], meeting_slots=[('Saturday', 'T1')]))
        self.assertEqual(evaluate_fitness(a, ctx).hard.total_penalty, 0)
        self.assertEqual(evaluate_fitness(b, ctx).hard.total_penalty, 0)
        with patch('scheduler.fitness.evaluate_fitness', side_effect=AssertionError('hidden fitness filtering')):
            child = course_based_crossover(a, b, ctx, Random(42))
        self.assert_whole_blocks(child, ctx, a, b)
        evaluation = evaluate_fitness(child, ctx)
        self.assertEqual(evaluation.hard.total_penalty, 2)
        self.assertEqual(evaluation.hard.counts_by_type['H04_PROFESSOR_OVERLAP'], 1)
        self.assertEqual(evaluation.hard.counts_by_type['H04_ROOM_OVERLAP'], 1)

    def test_batch_creation_uses_one_constructor_and_no_per_gene_mutators(self):
        original = Chromosome.__init__
        calls = []
        def record(instance, *args, **kwargs):
            calls.append(1)
            original(instance, *args, **kwargs)
        with (patch.object(Chromosome, '__init__', record),
              patch.object(Chromosome, 'assign', side_effect=AssertionError('assign in crossover')),
              patch.object(Chromosome, 'replace_section', side_effect=AssertionError('replace in crossover'))):
            child = course_based_crossover(self.a, self.b, self.context, Random(9))
        self.assertEqual(len(calls), 1)
        self.assert_whole_blocks(child, self.context, self.a, self.b)

    def test_identical_or_single_differing_blocks_do_not_get_artificial_genes(self):
        identical = course_based_crossover(self.a, self.a, self.context, Random(5))
        self.assertEqual(chromosome_fingerprint(identical), chromosome_fingerprint(self.a))
        b = self.a.clone()
        b.assign(next(iter(sorted(self.context.requests))), None)
        for seed in range(10):
            child = course_based_crossover(self.a, b, self.context, Random(seed))
            self.assertIn(chromosome_fingerprint(child), {chromosome_fingerprint(self.a), chromosome_fingerprint(b)})

    def test_meeting_order_alone_does_not_count_as_real_block_diversity(self):
        d = sample(2, sessions=2)
        ctx = EvaluationContext.from_dataset(d)
        a = scheduled(d)
        sections = {sid: replace(s, meeting_slots=tuple(reversed(s.meeting_slots))) for sid, s in a.sections.items()}
        b = Chromosome(sections, a.enrollments)
        self.assertEqual(chromosome_fingerprint(a), chromosome_fingerprint(b))
        for seed in range(10):
            child = course_based_crossover(a, b, ctx, Random(seed))
            self.assertEqual(chromosome_fingerprint(child), chromosome_fingerprint(a))

    def test_empty_or_low_demand_dataset_preserves_all_requests(self):
        for d in (sample(demand=3), with_rows(sample(), Courses=(), StudentRequests=())):
            ctx = EvaluationContext.from_dataset(d)
            a, b = build_empty_chromosome(d), build_empty_chromosome(d)
            child = course_based_crossover(a, b, ctx, Random(5))
            self.assertEqual(validate_structure(child, ctx), ())
            self.assertEqual(set(child.enrollments), ctx.requests)
            self.assertTrue(all(sid is None for sid in child.enrollments.values()))
            self.assertEqual(len(child.sections), 0)

    def test_mixed_mode_can_be_explicitly_disabled_for_independent_donor_coins(self):
        children = {chromosome_fingerprint(course_based_crossover(self.a, self.b, self.context, Random(seed), ensure_mixed=False))
                    for seed in range(30)}
        self.assertIn(chromosome_fingerprint(self.a), children)
        self.assertIn(chromosome_fingerprint(self.b), children)

    def test_input_type_errors_are_clear(self):
        for a, b, context, rng in ((None, self.b, self.context, Random(1)),
                                   (self.a, self.b, self.context.dataset, Random(1)),
                                   (self.a, self.b, self.context, None)):
            with self.assertRaises(TypeError):
                course_based_crossover(a, b, context, rng)

    def test_phase_four_population_produces_structurally_valid_evaluated_children(self):
        population = initialize_population(self.context, 10, random_seed=123)
        rng = Random(2026)
        for _ in range(50):
            a, b = tournament_selection(population, rng), tournament_selection(population, rng)
            child = course_based_crossover(a.chromosome, b.chromosome, self.context, rng)
            self.assert_whole_blocks(child, self.context, a.chromosome, b.chromosome)
            result = evaluate_fitness(child, self.context)
            self.assertGreaterEqual(result.hard.total_penalty, 0)
            self.assertTrue(0 <= result.soft.total_score <= 1)
            self.assertEqual(result.hard.context_id, self.context.context_id)
            self.assertIsNone(child.hard_score)


if __name__ == '__main__':
    unittest.main()
