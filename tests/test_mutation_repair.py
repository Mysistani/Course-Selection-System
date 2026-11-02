"""Atomic operator boundaries and genuinely consumed repair budgets."""
from dataclasses import replace
from random import Random
import random
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scheduler.chromosome import Chromosome, SectionGene, build_empty_chromosome, validate_structure
from scheduler.constraints import StructuralValidationError, Violation
from scheduler.crossover import course_based_crossover
from scheduler.evaluation_context import EvaluationContext
from scheduler.fitness import FitnessEvaluation, evaluate_fitness
from scheduler.initialization import chromosome_fingerprint, initialize_population
from scheduler.mutation import (OPERATIONS, MutationSettings, MutationTarget as T,
                                apply_mutation, maybe_mutate, mutate)
from scheduler.operator_support import EvaluationSnapshot, evaluate_snapshot
from scheduler.repair import RepairSettings, repair_conflicts, repair_structure
from scheduler.selection import tournament_selection
from test_constraints import sample, scheduled, with_rows


def fixture(n=16, sizes=None, capacity=20, courses=1):
    d = sample(courses, demand=n)
    d = with_rows(d, Classrooms=tuple(replace(r, capacity=capacity) for r in d.tables['Classrooms']),
                  Courses=tuple(replace(c, max_section_size=capacity) for c in d.tables['Courses']))
    ctx = EvaluationContext.from_dataset(d)
    c = scheduled(d)
    if sizes is not None:
        sections, enrollments = dict(c.sections), {key: None for key in c.enrollments}
        for cid in ctx.courses:
            keys = sorted(key for key in enrollments if key[1] == cid)
            offset = 0
            for i, count in enumerate(sizes, 1):
                sid = f'{cid}-{i:02d}'
                sections[sid] = SectionGene(sid, cid, True, 'P1', 'R1',
                                             (('Saturday', f'T{i}'),))
                for key in keys[offset:offset + count]:
                    enrollments[key] = sid
                offset += count
        c = Chromosome(sections, enrollments)
    return c, ctx


class MutationTests(unittest.TestCase):
    def run_op(self, c, ctx, op, target=None, applied=True):
        before = chromosome_fingerprint(c)
        c.record_evaluation(17, .25)
        maps = (c.sections, c.enrollments)
        result = apply_mutation(c, ctx, Random(12), op, target)
        self.assertEqual(chromosome_fingerprint(c), before)
        self.assertEqual((c.hard_score, c.soft_score), (17, .25))
        self.assertEqual(c.sections, maps[0])
        self.assertEqual(c.enrollments, maps[1])
        self.assertEqual(validate_structure(result.chromosome, ctx), ())
        self.assertIsNot(result.chromosome.sections, c.sections)
        self.assertIsNone(result.chromosome.hard_score)
        self.assertIsNone(result.chromosome.soft_score)
        self.assertEqual(result.report.evaluations.fitness_calls, 0)
        if applied:
            self.assertEqual(result.report.status, 'applied', result.report)
            self.assertNotEqual(chromosome_fingerprint(result.chromosome), before)
        else:
            self.assertIn(result.report.status, ('not_applicable', 'failed'))
            self.assertEqual(chromosome_fingerprint(result.chromosome), before)
        return result.chromosome

    def test_move_meeting(self):
        c, ctx = fixture()
        child = self.run_op(c, ctx, 'MoveMeeting', T(section_id='C1-01', meeting_index=0, slot=('Monday', 'T1')))
        self.assertEqual(child.sections['C1-01'].meeting_slots, (('Monday', 'T1'),))

    def test_move_unknown_or_duplicate_meeting_rejected(self):
        c, ctx = fixture()
        for slot in [('Never', 'T1'), ('Saturday', 'T1')]:
            self.run_op(c, ctx, 'MoveMeeting', T(section_id='C1-01', meeting_index=0, slot=slot), False)

    def test_forbidden_slot_rejected(self):
        d = sample()
        d = with_rows(d, TimeSlots=tuple(replace(s, is_allowed=False) if s.time_slot == 'T2' else s for s in d.tables['TimeSlots']))
        self.run_op(scheduled(d), EvaluationContext.from_dataset(d), 'MoveMeeting',
                    T(section_id='C1-01', meeting_index=0, slot=('Saturday', 'T2')), False)

    def test_swap_meetings_preserves_session_count(self):
        d = sample(2, sessions=2)
        c, ctx = scheduled(d), EvaluationContext.from_dataset(d)
        child = self.run_op(c, ctx, 'SwapMeetings', T('C1-01', 'C2-01', 0, 0))
        self.assertEqual([len(child.sections[s].meeting_slots) for s in sorted(child.sections)], [2, 2])
        self.run_op(c, ctx, 'SwapMeetings', T('C1-01', 'C2-01', 1, 1), False)

    def test_swap_creating_duplicate_rejected(self):
        d = sample(2, sessions=2)
        self.run_op(scheduled(d), EvaluationContext.from_dataset(d), 'SwapMeetings', T('C1-01', 'C2-01', 0, 1), False)

    def test_change_professor_multiple_and_single(self):
        c, ctx = fixture(courses=2)
        child = self.run_op(c, ctx, 'ChangeProfessor', T(section_id='C1-01', professor_id='P2'))
        self.assertEqual(child.sections['C1-01'].professor_id, 'P2')
        c, ctx = fixture()
        self.run_op(c, ctx, 'ChangeProfessor', applied=False)
        self.run_op(c, ctx, 'ChangeProfessor', T(section_id='C1-01', professor_id='absent'), False)

    def test_only_one_compatible_lab(self):
        d = sample(2)
        d = with_rows(d, Courses=tuple(replace(c, required_room_type='Lab') if c.course_id == 'C1' else c for c in d.tables['Courses']),
                      Classrooms=tuple(replace(r, room_type='Lab') if r.room_id == 'R1' else r for r in d.tables['Classrooms']))
        self.run_op(scheduled(d), EvaluationContext.from_dataset(d), 'ChangeRoom', T(section_id='C1-01', room_id='R2'), False)
        one = with_rows(d, Courses=d.tables['Courses'][:1], StudentRequests=tuple(r for r in d.tables['StudentRequests'] if r.course_id == 'C1'))
        self.run_op(scheduled(one), EvaluationContext.from_dataset(one), 'ChangeRoom', applied=False)

    def test_change_room_capacity(self):
        c, ctx = fixture(courses=2)
        self.run_op(c, ctx, 'ChangeRoom', T(section_id='C1-01', room_id='R2'))
        d = with_rows(ctx.dataset, Classrooms=tuple(replace(r, capacity=8) if r.room_id == 'R2' else r for r in ctx.dataset.tables['Classrooms']))
        self.run_op(c, EvaluationContext.from_dataset(d), 'ChangeRoom', T(section_id='C1-01', room_id='R2'), False)

    def test_split_15_rejected_and_16_two_eights(self):
        for size in (15, 16):
            c, ctx = fixture(sizes=[size])
            self.assertEqual(evaluate_fitness(c, ctx).hard.total_penalty, 0)
            child = self.run_op(c, ctx, 'SplitSection', T('C1-01', 'C1-02', room_id='R1'), size == 16)
            if size == 16:
                self.assertEqual(child.section_sizes(), {'C1-01': 8, 'C1-02': 8})
                hard = evaluate_fitness(child, ctx).hard
                self.assertEqual(hard.counts_by_type['H07_SECTION_UNDERFLOW'], 0)
                self.assertEqual(hard.counts_by_type['H07_SECTION_OVERFLOW'], 0)
                self.assertEqual(hard.total_penalty, 0)  # seed chooses a distinct time

    def test_split_removes_exact_overflow(self):
        c, ctx = fixture(capacity=8)
        self.assertEqual(evaluate_fitness(c, ctx).hard.total_penalty, 8)
        child = self.run_op(c, ctx, 'SplitSection', T('C1-01', 'C1-02', room_id='R1'))
        self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 0)

    def test_merge_capacity_and_atomic_rollback(self):
        for capacity in (15, 16):
            c, ctx = fixture(sizes=[8, 8], capacity=capacity)
            self.assertEqual(evaluate_fitness(c, ctx).hard.total_penalty, 0)
            child = self.run_op(c, ctx, 'MergeSections', T('C1-01', 'C1-02'), capacity == 16)
            self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 0)
            if capacity == 16:
                self.assertEqual(child.sections['C1-01'], SectionGene('C1-01', 'C1'))
                self.assertEqual(set(child.enrollments.values()), {'C1-02'})

    def test_move_student_minimum_and_capacity(self):
        c, ctx = fixture(sizes=[8, 8])
        key = next(key for key, sid in c.enrollments.items() if sid == 'C1-01')
        self.run_op(c, ctx, 'MoveStudent', T(other_section_id='C1-02', request=key), False)
        self.assertEqual(evaluate_fitness(c, ctx).hard.total_penalty, 0)
        c, ctx = fixture(17, [9, 8])
        key = next(key for key, sid in c.enrollments.items() if sid == 'C1-01')
        child = self.run_op(c, ctx, 'MoveStudent', T(other_section_id='C1-02', request=key))
        self.assertEqual(child.section_sizes(), {'C1-01': 8, 'C1-02': 9})
        self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 0)

    def test_swap_students_counts_preserved(self):
        c, ctx = fixture(sizes=[8, 8])
        keys = [next(k for k, v in c.enrollments.items() if v == sid) for sid in ('C1-01', 'C1-02')]
        child = self.run_op(c, ctx, 'SwapStudents', T(request=keys[0], other_request=keys[1]))
        self.assertEqual(child.section_sizes(), c.section_sizes())
        self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 0)

    def test_activate_deactivate_batch_assignments(self):
        c, ctx = fixture(sizes=[8])
        child = self.run_op(c, ctx, 'ActivateDeactivate', T(section_id='C1-02', room_id='R1', action='activate'))
        self.assertEqual(child.section_sizes(), {'C1-01': 8, 'C1-02': 8})
        merged = self.run_op(child, ctx, 'ActivateDeactivate', T(section_id='C1-02', action='deactivate'))
        self.assertEqual(set(merged.enrollments.values()), {'C1-01'})

    def test_empty_activation_or_failed_redistribution_rejected(self):
        c, ctx = fixture()
        self.run_op(c, ctx, 'ActivateDeactivate', T(section_id='C1-02', room_id='R1', action='activate'), False)
        self.run_op(c, ctx, 'ActivateDeactivate', T(section_id='C1-01', action='deactivate'), False)

    def test_assign_unassign_preserves_none_keys(self):
        c, ctx = fixture(9, [8])
        key = next(k for k, sid in c.enrollments.items() if sid is None)
        child = self.run_op(c, ctx, 'AssignUnassign', T(section_id='C1-01', request=key, action='assign'))
        self.assertEqual(evaluate_fitness(child, ctx).hard.total_penalty, 0)
        restored = self.run_op(child, ctx, 'AssignUnassign', T(request=key, action='unassign'))
        self.assertIn(key, restored.enrollments)
        self.assertIsNone(restored.enrollments[key])
        self.assertEqual(evaluate_fitness(restored, ctx).hard.total_penalty, 0)
        assigned = next(k for k, sid in c.enrollments.items() if sid)
        self.run_op(c, ctx, 'AssignUnassign', T(request=assigned, action='unassign'), False)

    def test_seed_reproducible_and_global_rng_untouched(self):
        c, ctx = fixture(courses=2)
        state = random.getstate()
        a, b = mutate(c, ctx, Random(111)), mutate(c, ctx, Random(111))
        self.assertEqual(a.report, b.report)
        self.assertEqual(chromosome_fingerprint(a.chromosome), chromosome_fingerprint(b.chromosome))
        self.assertEqual(random.getstate(), state)

    def test_probability_gate_separate(self):
        c, ctx = fixture()
        with patch('scheduler.mutation.evaluate_snapshot', side_effect=AssertionError('hidden evaluation')):
            result = maybe_mutate(c, ctx, Random(1), MutationSettings(mutation_probability=0))
        self.assertEqual(result.report.status, 'skipped')
        self.assertEqual(result.report.evaluations.fitness_calls, 0)
        self.assertEqual(mutate(c, ctx, Random(1), MutationSettings(mutation_probability=0)).report.status, 'applied')

    def test_no_applicable_operator_and_attempt_budget(self):
        c, ctx = fixture()
        result = mutate(c, ctx, Random(1), MutationSettings(operator_weights=(('ChangeProfessor', 1),), max_attempts=2))
        self.assertEqual(result.report.status, 'not_applicable')
        self.assertLessEqual(result.report.attempts, 2)
        self.assertIn('ChangeProfessor', result.report.unavailable_operations)
        c, ctx = fixture(8)
        result = apply_mutation(c, ctx, Random(1), 'AssignUnassign', settings=MutationSettings(max_attempts=2))
        self.assertEqual(result.report.attempts, 2)

    def test_snapshot_context_and_staleness_guard(self):
        c, ctx = fixture()
        snapshot = evaluate_snapshot(c, ctx)
        other = EvaluationContext.from_dataset(with_rows(ctx.dataset, Classrooms=tuple(replace(r, capacity=21) for r in ctx.dataset.tables['Classrooms'])))
        for function in (mutate, maybe_mutate, repair_conflicts):
            with self.assertRaisesRegex(ValueError, 'Context'):
                function(c, other, Random(1), evaluation=snapshot)
        child = apply_mutation(c, ctx, Random(1), 'MoveMeeting').chromosome
        with self.assertRaisesRegex(ValueError, 'stale'):
            repair_conflicts(child, ctx, Random(1), evaluation=snapshot)

    def test_raw_chromosome_with_incompatible_schema_rejected(self):
        c, ctx = fixture()
        other = EvaluationContext.from_dataset(sample(2))
        for function in (mutate, maybe_mutate, repair_conflicts):
            with self.assertRaises(StructuralValidationError):
                function(c, other, Random(1))

    def test_failed_split_after_partition_preserves_every_gene(self):
        c, ctx = fixture()
        d = with_rows(ctx.dataset, ProfessorCourses=())
        self.run_op(c, EvaluationContext.from_dataset(d), 'SplitSection',
                    T('C1-01', 'C1-02', room_id='R1'), False)

    def test_zero_weights_and_mutation_commit_attempt_cap(self):
        c, ctx = fixture()
        result = mutate(c, ctx, Random(1), MutationSettings(operator_weights=()))
        self.assertEqual(result.report.status, 'not_applicable')
        with patch('scheduler.mutation._commit', side_effect=StructuralValidationError(())) as commit:
            result = mutate(c, ctx, Random(1), MutationSettings(max_attempts=2))
        self.assertEqual(commit.call_count, 2)
        self.assertEqual(result.report.status, 'failed')
        self.assertEqual(result.report.rollbacks, 2)
        self.assertEqual(chromosome_fingerprint(result.chromosome), chromosome_fingerprint(c))

    def test_targeted_fallback_and_evaluation_accounting(self):
        c, ctx = fixture()
        settings = MutationSettings(targeted_probability=1)
        result = mutate(c, ctx, Random(1), settings)
        self.assertTrue(result.report.targeted)
        self.assertTrue(result.report.fallback)  # no violations
        self.assertEqual(result.report.evaluations.baseline, 1)
        snapshot = evaluate_snapshot(c, ctx)
        result = mutate(c, ctx, Random(1), settings, evaluation=snapshot)
        self.assertEqual(result.report.evaluations.fitness_calls, 0)

    def test_targeted_mutation_uses_violation_factor(self):
        c, ctx = fixture(courses=2)
        c.replace_section(replace(c.sections['C1-01'], professor_id='P2', meeting_slots=(('Saturday', 'T2'),)))
        result = mutate(c, ctx, Random(12), MutationSettings(targeted_probability=1, operator_weights=(('ChangeProfessor', 1),)))
        self.assertEqual(result.report.status, 'applied')
        self.assertTrue(result.report.targeted)
        self.assertFalse(result.report.fallback)
        self.assertIn(result.report.changed_sections[0], ('C1-01', 'C2-01'))

    def test_empty_resource_space_terminates(self):
        d = with_rows(sample(), ProfessorCourses=(), Classrooms=())
        ctx = EvaluationContext.from_dataset(d)
        c = build_empty_chromosome(d)
        result = mutate(c, ctx, Random(1))
        self.assertEqual(result.report.status, 'not_applicable')
        self.assertEqual(result.report.attempts, 0)
        self.assertFalse(validate_structure(result.chromosome, ctx))

    def test_child_edit_is_independent(self):
        c, ctx = fixture()
        before = chromosome_fingerprint(c)
        child = apply_mutation(c, ctx, Random(1), 'MoveMeeting').chromosome
        key = next(iter(child.enrollments))
        child.assign(key, None)
        child.replace_section(replace(child.sections['C1-01'], meeting_slots=(('Saturday', 'T3'),)))
        self.assertEqual(chromosome_fingerprint(c), before)

    def test_reproducible_across_hash_seeds_and_mapping_order(self):
        code = '''from random import Random
from test_mutation_repair import fixture
from scheduler.chromosome import Chromosome
from scheduler.mutation import mutate
from scheduler.initialization import chromosome_fingerprint
c,ctx=fixture(courses=2)
c=Chromosome({s:c.sections[s] for s in set(c.sections)}, {k:c.enrollments[k] for k in set(c.enrollments)})
print(chromosome_fingerprint(mutate(c,ctx,Random(12)).chromosome))
'''
        root = Path(__file__).resolve().parents[1]
        outputs = []
        for seed in ('1', '1223'):
            env = {**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONPATH': os.pathsep.join((str(root / 'src'), str(root / 'tests')))}
            outputs.append(subprocess.check_output([sys.executable, '-c', code], cwd=root, env=env, text=True))
        self.assertEqual(*outputs)


class RepairTests(unittest.TestCase):
    def test_structural_reference_repair_no_fabrication(self):
        c, ctx = fixture()
        sections = dict(c.sections)
        sections.pop('C1-02')
        sections['extra'] = SectionGene('extra', 'C1')
        assignments = dict(c.enrollments)
        assignments[('S1', 'C1')] = 'extra'
        assignments.pop(('S2', 'C1'))
        bad = Chromosome(sections, assignments)
        before = chromosome_fingerprint(bad)
        result = repair_structure(bad, ctx, Random(1))
        self.assertEqual(result.report.status, 'repaired')
        self.assertFalse(validate_structure(result.chromosome, ctx))
        self.assertIsNone(result.chromosome.enrollments[('S1', 'C1')])
        self.assertIsNone(result.chromosome.enrollments[('S2', 'C1')])
        self.assertEqual(chromosome_fingerprint(bad), before)
        self.assertEqual(result.report.evaluations.fitness_calls, 0)

    def test_structural_missing_resource_failure_and_rollback(self):
        c, ctx = fixture()
        sections = dict(c.sections)
        sections['C1-01'] = replace(sections['C1-01'], room_id='missing')
        bad = Chromosome(sections, c.enrollments)
        result = repair_structure(bad, ctx, Random(1))
        self.assertIsNone(result.chromosome)
        self.assertEqual(result.report.status, 'failed')
        restored = repair_structure(bad, ctx, Random(1), rollback=c)
        self.assertEqual(restored.report.status, 'rolled_back')
        self.assertEqual(chromosome_fingerprint(restored.chromosome), chromosome_fingerprint(c))
        with self.assertRaises(StructuralValidationError):
            repair_conflicts(bad, ctx, Random(1))

    def test_structural_budget_zero(self):
        c, ctx = fixture()
        bad = Chromosome({k: v for k, v in c.sections.items() if k != 'C1-02'}, c.enrollments)
        result = repair_structure(bad, ctx, Random(1), RepairSettings(max_structural_steps=0), rollback=c)
        self.assertEqual(result.report.attempts, 0)
        self.assertEqual(result.report.status, 'rolled_back')

    def test_real_repair_improves_and_preserves_parent_cache(self):
        c, ctx = fixture(courses=2)
        c.replace_section(replace(c.sections['C2-01'], professor_id='P1', room_id='R1', meeting_slots=(('Saturday', 'T1'),)))
        before = evaluate_fitness(c, ctx)
        self.assertEqual(before.hard.total_penalty, 2)
        c.record_evaluation(2, .3)
        fp = chromosome_fingerprint(c)
        result = repair_conflicts(c, ctx, Random(12))
        self.assertEqual(result.report.status, 'improved')
        self.assertLess(result.report.hard_after, 2)
        self.assertLess(result.evaluation.fitness.comparison_key, before.comparison_key)
        self.assertEqual(chromosome_fingerprint(c), fp)
        self.assertEqual((c.hard_score, c.soft_score), (2, .3))
        self.assertIsNone(result.chromosome.hard_score)

    def test_equal_and_worse_are_rejected(self):
        c, ctx = fixture(courses=2)
        baseline = evaluate_snapshot(c, ctx)
        # Deliberate diagnostic fixture: ensure local proposals exist even when comparator ties.
        v = Violation('H04_PROFESSOR_OVERLAP', 1, section_ids=('C1-01', 'C2-01'))
        hard = replace(baseline.fitness.hard, violations=(v,))
        baseline = replace(baseline, fitness=FitnessEvaluation(hard, baseline.fitness.soft))
        worse_counts = dict(hard.counts_by_type)
        worse_counts['H04_PROFESSOR_OVERLAP'] = 10
        worse = FitnessEvaluation(replace(hard, counts_by_type=worse_counts), baseline.fitness.soft)
        for returned in (baseline.fitness, worse):
            with patch('scheduler.repair.evaluate_fitness', return_value=returned) as evaluate:
                result = repair_conflicts(c, ctx, Random(1), evaluation=baseline)
            self.assertEqual(result.report.status, 'unchanged')
            self.assertEqual(chromosome_fingerprint(result.chromosome), baseline.fingerprint)
            self.assertGreater(evaluate.call_count, 0)
            self.assertEqual(evaluate.call_count, result.report.evaluations.candidates)
            self.assertEqual(result.report.evaluations.baseline, 0)

    def test_exact_local_and_global_evaluation_caps(self):
        c, ctx = fixture(n=8, courses=2)
        snapshot = evaluate_snapshot(c, ctx)
        violations = tuple(Violation('H04_PROFESSOR_OVERLAP', 1, section_ids=('C1-01', 'C2-01')) for _ in range(20))
        baseline = replace(snapshot, fitness=FitnessEvaluation(replace(snapshot.fitness.hard, violations=violations), snapshot.fitness.soft))
        # Supply many valid, distinct candidates independently of small fixture search space.
        # Each iteration uses real slot/professor/room combinations, no fabricated identifiers.
        from itertools import product
        from scheduler.mutation import _State, _apply_draft
        from scheduler.operator_support import commit_draft
        candidates = []
        for a, b, p, r in product(ctx.slots, ctx.slots, ctx.professors, ctx.rooms):
            sections = dict(c.sections)
            sections['C1-01'] = replace(sections['C1-01'], meeting_slots=(a,), professor_id=p, room_id=r)
            sections['C2-01'] = replace(sections['C2-01'], meeting_slots=(b,))
            candidate = commit_draft(sections, dict(c.enrollments), ctx)
            if chromosome_fingerprint(candidate) != snapshot.fingerprint:
                candidates.append((sections, dict(c.enrollments)))
        iterator = iter(candidates)
        proposals = [('MoveMeeting', T(section_id='C1-01'))] * 100
        with patch('scheduler.repair._local_proposals', side_effect=lambda *args: iter(proposals)), \
             patch('scheduler.repair._apply_draft', side_effect=lambda *args: next(iterator)), \
             patch('scheduler.repair.evaluate_fitness', return_value=baseline.fitness) as evaluate:
            result = repair_conflicts(c, ctx, Random(1), evaluation=baseline)
        self.assertEqual(evaluate.call_count, 50)
        self.assertEqual(result.report.evaluations.candidates, 50)
        self.assertEqual(result.report.candidates_per_conflict, (5,) * 10)
        self.assertEqual(result.report.attempts, 50)
        self.assertEqual(result.report.rollbacks, 50)

    def test_budget_zero_and_explicit_baseline_count(self):
        c, ctx = fixture()
        with patch('scheduler.operator_support.evaluate_fitness', wraps=evaluate_fitness) as evaluate:
            result = repair_conflicts(c, ctx, Random(1), RepairSettings(max_candidate_evaluations=0))
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(result.report.evaluations.fitness_calls, 1)
        self.assertEqual(result.report.evaluations.hard_calls, 1)
        self.assertEqual(result.report.evaluations.soft_calls, 1)
        self.assertEqual(result.report.evaluations.candidates, 0)

    def test_attempt_budget_infeasible_candidates(self):
        c, ctx = fixture(courses=2)
        snapshot = evaluate_snapshot(c, ctx)
        v = Violation('H04_PROFESSOR_OVERLAP', 1, section_ids=('C1-01', 'C2-01'))
        snapshot = replace(snapshot, fitness=FitnessEvaluation(replace(snapshot.fitness.hard, violations=(v,)), snapshot.fitness.soft))
        from scheduler.mutation import _NotApplicable
        with patch('scheduler.repair._apply_draft', side_effect=_NotApplicable('no resource')):
            result = repair_conflicts(c, ctx, Random(1), RepairSettings(max_candidate_attempts=3), evaluation=snapshot)
        self.assertEqual(result.report.attempts, 3)
        self.assertEqual(result.report.evaluations.fitness_calls, 0)
        self.assertEqual(result.report.rollbacks, 3)

    def test_settings_invalid(self):
        for kwargs in ({'max_candidate_evaluations': -1}, {'max_local_candidates': True}):
            with self.assertRaises(ValueError):
                RepairSettings(**kwargs)
        for kwargs in ({'mutation_probability': 2}, {'max_attempts': 0}, {'operator_weights': (('fake', 1),)}):
            with self.assertRaises(ValueError):
                MutationSettings(**kwargs)

    def test_phase5a_children_integration(self):
        c, ctx = fixture(courses=2)
        population = initialize_population(ctx, population_size=10, random_seed=43)
        rng = Random(13)
        state = random.getstate()
        for _ in range(15):
            a = tournament_selection(population.individuals, rng)
            b = tournament_selection(population.individuals, rng)
            fingerprints = chromosome_fingerprint(a.chromosome), chromosome_fingerprint(b.chromosome)
            child = course_based_crossover(a.chromosome, b.chromosome, ctx, rng)
            mutated = mutate(child, ctx, rng)
            repaired = repair_conflicts(mutated.chromosome, ctx, rng)
            self.assertFalse(validate_structure(repaired.chromosome, ctx))
            self.assertEqual(repaired.evaluation.fitness, evaluate_fitness(repaired.chromosome, ctx))
            self.assertEqual(fingerprints, (chromosome_fingerprint(a.chromosome), chromosome_fingerprint(b.chromosome)))
            self.assertLessEqual(repaired.report.evaluations.candidates, 50)
            self.assertTrue(all(n <= 5 for n in repaired.report.candidates_per_conflict))
        self.assertEqual(random.getstate(), state)


if __name__ == '__main__':
    unittest.main()
