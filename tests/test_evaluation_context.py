from dataclasses import FrozenInstanceError, replace
import gc
from types import MappingProxyType
import unittest
from unittest.mock import patch
import weakref

from scheduler.chromosome import build_empty_chromosome, validate_structure
from scheduler.constraints import evaluate_hard_constraints
from scheduler.domain import Dataset, Preference
from scheduler.evaluation_context import EvaluationContext, _CACHE, _build_context
from scheduler.policies import DEFAULT_POLICY, DesignPolicy
from test_constraints import sample, scheduled, with_rows


class EvaluationContextTests(unittest.TestCase):
    def test_legacy_hard_api_and_explicit_context_are_equivalent(self):
        d = sample()
        c = scheduled(d)
        context = EvaluationContext.from_dataset(d)
        expected = evaluate_hard_constraints(c, d)
        self.assertEqual(expected, evaluate_hard_constraints(c, context))
        self.assertEqual(expected, evaluate_hard_constraints(c, d, context=context))
        self.assertEqual(expected, evaluate_hard_constraints(c, context.dataset, context=context))

    def test_static_preparation_only_once_for_repeated_legacy_evaluation(self):
        d = sample()
        # Build candidate before the patch, but select a fresh Dataset identity.
        c = scheduled(d)
        d = replace(d)
        with patch('scheduler.evaluation_context._build_context', wraps=_build_context) as build:
            for _ in range(5):
                result = evaluate_hard_constraints(c, d)
                validate_structure(c, d)
            self.assertEqual(build.call_count, 1)
        context = EvaluationContext.from_dataset(d)
        with patch('scheduler.chromosome._expected_section_slots', side_effect=AssertionError('repeated scan')):
            for _ in range(3):
                self.assertEqual(evaluate_hard_constraints(c, context), result)
        self.assertIs(result.diagnostics, context.diagnostics)

    def test_context_and_nested_tables_are_immutable(self):
        d = sample()
        context = EvaluationContext.from_dataset(d)
        with self.assertRaises(FrozenInstanceError):
            context.policy = replace(DEFAULT_POLICY, neutral_time_score=.2)
        with self.assertRaises(TypeError):
            context.demand['C1'] = 999
        with self.assertRaises(TypeError):
            context.dataset.tables['Courses'] = ()
        with self.assertRaises(AttributeError):
            context.compatible_rooms['C1'].add('R2')
        with self.assertRaises(FrozenInstanceError):
            context.courses['C1'].credits = 99

    def test_caller_containers_cannot_mutate_dataset_or_policy(self):
        original = sample()
        mutable = {k: list(v) for k, v in original.tables.items()}
        d = Dataset(mutable, 'fixture', [])
        context = EvaluationContext.from_dataset(d)
        mutable['Courses'].clear()
        mutable['Students'] = []
        self.assertEqual(len(d.tables['Courses']), 1)
        self.assertEqual(len(context.students), 8)
        components = ['requests', 'students', 'professors']
        policy = DesignPolicy(soft_components=components)
        components.clear()
        self.assertEqual(policy.soft_components, ('requests', 'students', 'professors'))

    def test_new_dataset_rejected_against_pinned_context(self):
        d = sample()
        c = scheduled(d)
        context = EvaluationContext.from_dataset(d)
        changed = with_rows(d, Courses=(replace(d.tables['Courses'][0], credits=4),))
        with self.assertRaisesRegex(ValueError, 'Dataset differs'):
            evaluate_hard_constraints(c, changed, context=context)
        other = EvaluationContext.from_dataset(changed)
        self.assertNotEqual(context.context_id, other.context_id)
        self.assertEqual(context.courses['C1'].credits, 3)

    def test_policy_change_requires_new_context_and_cannot_rebind_existing(self):
        d = sample()
        context = EvaluationContext.from_dataset(d)
        policy = replace(DEFAULT_POLICY, neutral_time_score=.25)
        other = EvaluationContext.from_dataset(d, policy)
        self.assertIsNot(context, other)
        self.assertNotEqual(context.context_id, other.context_id)
        self.assertIs(EvaluationContext.from_dataset(d, policy), other)
        with self.assertRaisesRegex(ValueError, 'cannot replace'):
            evaluate_hard_constraints(scheduled(d), context, context=other)
        with self.assertRaises(FrozenInstanceError):
            context.policy.neutral_time_score = .25

    def test_weak_cache_does_not_retain_source_dataset(self):
        d = sample()
        identity = id(d)
        reference = weakref.ref(d)
        context = EvaluationContext.from_dataset(d)
        del d
        gc.collect()
        self.assertIsNone(reference())
        self.assertNotIn(identity, _CACHE)
        self.assertEqual(context.demand['C1'], 8)
        self.assertEqual(evaluate_hard_constraints(build_empty_chromosome(context.dataset), context).total_penalty, 0)

    def test_context_fingerprint_uses_data_not_only_declared_workbook_hash(self):
        d = sample()
        changed = with_rows(d, StudentPreferences=(Preference('S1', 'PreferredTeachingDays', 2, 7),))
        self.assertEqual(d.sha256, changed.sha256)
        self.assertNotEqual(EvaluationContext.from_dataset(d).context_id, EvaluationContext.from_dataset(changed).context_id)

    def test_policy_rejects_unsupported_or_unsafe_values(self):
        for changes in ({'neutral_time_score': float('nan')}, {'gap_normalizer_minutes': 0},
                        {'difficult_course_threshold': 3}, {'free_gap_minutes': 31},
                        {'minimum_professor_hours': 'hard'}, {'soft_aggregation': 'large_penalty'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(DEFAULT_POLICY, **changes)

    def test_shortfall_diagnostics_are_candidate_dependent_not_static_penalties(self):
        d = sample()
        d = with_rows(d, Professors=(replace(d.tables['Professors'][0], min_hours=3),))
        context = EvaluationContext.from_dataset(d)
        result = evaluate_hard_constraints(scheduled(d), context)
        self.assertEqual(result.total_penalty, 0)
        self.assertEqual(context.diagnostics, ())
        self.assertEqual(result.metrics.professor_minute_shortfall['P1'], 90)
        self.assertEqual(result.diagnostics[0].kind, 'PROFESSOR_MIN_HOURS_SHORTFALL')
        empty = evaluate_hard_constraints(build_empty_chromosome(d), context)
        self.assertEqual(empty.metrics.professor_minute_shortfall['P1'], 180)
        self.assertEqual(context.diagnostics, ())


if __name__ == '__main__':
    unittest.main()
