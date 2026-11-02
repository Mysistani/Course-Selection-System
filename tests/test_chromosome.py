from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
import unittest

from scheduler.chromosome import (Chromosome, SectionGene, build_empty_chromosome,
                                  validate_structure)
from scheduler.domain import Course, Dataset, Professor, Request, Room, Slot, Student


def sample_dataset(demand=16):
    """Small deliberate fixture; unrelated to random Excel sample statistics."""
    students = tuple(Student(f'S{i}', 'M', 1, 0, 20) for i in range(demand))
    tables = {
        'Courses': (Course('C1', 'M', 'Course', 3, 1, 'Theory', 2, 90, 'Theory', 8, 20, 3),),
        'Professors': (Professor('P1', 'M', 'Professor', 0, 18),),
        'Classrooms': (Room('R1', 'F', 'Theory', 20, 'Board'),),
        'TimeSlots': (Slot('SAT-T1', 'Saturday', 'T1', 480, 570, True),
                      Slot('MON-T2', 'Monday', 'T2', 600, 690, True)),
        'Students': students,
        'StudentRequests': tuple(Request(s.student_id, 'C1', 5) for s in students),
    }
    return Dataset(MappingProxyType(tables), 'fixture', ())


def active(sid='C1-01'):
    return SectionGene(sid, 'C1', True, 'P1', 'R1', [('Saturday', 'T1'), ('Monday', 'T2')])


class ChromosomeTests(unittest.TestCase):
    def setUp(self):
        self.dataset = sample_dataset()
        self.chromosome = build_empty_chromosome(self.dataset)

    def test_scaffold_has_stable_slots_and_all_unassigned_requests(self):
        self.assertEqual(list(self.chromosome.sections), ['C1-01', 'C1-02'])
        self.assertEqual(len(self.chromosome.enrollments), 16)
        self.assertTrue(all(v is None for v in self.chromosome.enrollments.values()))
        self.assertEqual(validate_structure(self.chromosome, self.dataset), ())
        self.assertIsNone(self.chromosome.hard_score)

    def test_low_demand_preserves_requests_without_custom_slots(self):
        dataset = sample_dataset(3)
        empty = build_empty_chromosome(dataset)
        self.assertEqual(len(empty.sections), 0)
        self.assertEqual(len(empty.enrollments), 3)
        self.assertEqual(validate_structure(empty, dataset), ())
        with self.assertRaisesRegex(ValueError, 'stable slots'):
            build_empty_chromosome(dataset, section_limits={'C1': 2})

    def test_list_input_is_copied_and_normalized(self):
        slots = [('Saturday', 'T1')]
        section = SectionGene('C1-01', 'C1', meeting_slots=slots)
        slots.append(('Monday', 'T2'))
        self.assertEqual(section.meeting_slots, (('Saturday', 'T1'),))
        with self.assertRaises(FrozenInstanceError):
            section.active = True

    def test_clone_preserves_parent_and_resets_child_scores(self):
        parent = self.chromosome
        parent.record_evaluation(3, .7)
        child = parent.clone()
        child.replace_section(active())
        child.assign(('S0', 'C1'), 'C1-01')
        self.assertFalse(parent.sections['C1-01'].active)
        self.assertIsNone(parent.enrollments[('S0', 'C1')])
        self.assertEqual(parent.hard_score, 3)
        self.assertIsNone(child.hard_score)

    def test_each_mutation_invalidates_evaluation(self):
        self.chromosome.record_evaluation(0, .8)
        self.chromosome.replace_section(active())
        self.assertIsNone(self.chromosome.soft_score)
        self.chromosome.record_evaluation(1, .6)
        self.chromosome.assign(('S0', 'C1'), 'C1-01')
        self.assertIsNone(self.chromosome.hard_score)

    def test_assign_to_inactive_unknown_or_wrong_course_is_rejected(self):
        with self.assertRaises(ValueError):
            self.chromosome.assign(('S0', 'C1'), 'C1-01')
        with self.assertRaises(KeyError):
            self.chromosome.assign(('S0', 'C1'), 'missing')
        other = Chromosome({'X': SectionGene('X', 'C2', True)}, self.chromosome.enrollments)
        with self.assertRaises(ValueError):
            other.assign(('S0', 'C1'), 'X')

    def test_sizes_are_derived(self):
        self.chromosome.replace_section(active())
        self.chromosome.assign(('S0', 'C1'), 'C1-01')
        self.assertEqual(self.chromosome.section_sizes(), {'C1-01': 1, 'C1-02': 0})
        self.chromosome.assign(('S0', 'C1'), None)
        self.assertEqual(self.chromosome.section_sizes()['C1-01'], 0)

    def test_missing_request_and_dangling_assignment_are_reported(self):
        c = Chromosome(self.chromosome.sections, {('S0', 'C1'): 'missing'})
        issues = validate_structure(c, self.dataset)
        self.assertEqual(sum('input request is missing' in i.message for i in issues), 15)
        self.assertTrue(any('unknown section' in i.message for i in issues))

    def test_wrong_course_inactive_and_bad_dictionary_key(self):
        c = Chromosome({'wrong': SectionGene('C1-01', 'C1')}, {('S0', 'C1'): 'wrong'})
        messages = [i.message for i in validate_structure(c, self.dataset)]
        self.assertIn('dictionary key differs from section_id', messages)
        self.assertIn('assignment references an inactive section', messages)

    def test_duplicate_unknown_and_missing_meetings(self):
        for slots, message in [([('Saturday', 'T1')] * 2, 'duplicate meeting slot'),
                               ([('Saturday', 'T1')], 'incorrect number of weekly meetings'),
                               ([('Saturday', 'T1'), ('Never', 'T9')], 'unknown meeting slot')]:
            c = Chromosome({'C1-01': replace(active(), meeting_slots=slots)}, self.chromosome.enrollments)
            self.assertTrue(any(i.message == message for i in validate_structure(c, self.dataset)))

    def test_feasibility_is_not_claimed_by_structural_validation(self):
        # Both groups overlap and have <8 students; later hard evaluator handles it.
        c = Chromosome({'C1-01': active(), 'C1-02': active('C1-02')}, self.chromosome.enrollments)
        self.assertEqual(validate_structure(c, self.dataset), ())
        self.assertIsNone(c.hard_score)

    def test_public_mappings_cannot_bypass_score_invalidation(self):
        with self.assertRaises(TypeError):
            self.chromosome.sections['C1-01'] = active()
        with self.assertRaises(TypeError):
            self.chromosome.enrollments[('S0', 'C1')] = 'C1-01'

    def test_bad_limits_and_course_change_are_rejected(self):
        for limits in ({'unknown': 1}, {'C1': -1}, {'C1': True}):
            with self.assertRaises(ValueError):
                build_empty_chromosome(self.dataset, section_limits=limits)
        with self.assertRaises(ValueError):
            self.chromosome.replace_section(SectionGene('C1-01', 'C2'))

    def test_same_dataset_always_has_same_slots(self):
        other = build_empty_chromosome(self.dataset, section_limits={'C1': 2})
        other.replace_section(active())
        self.assertEqual(set(other.sections), set(self.chromosome.sections))
        self.assertEqual(validate_structure(other, self.dataset), ())
        self.assertEqual(list(other.clone().sections), list(other.sections))

    def test_slot_boundaries_use_floor_not_rounding(self):
        for demand, expected in ((0, 0), (7, 0), (8, 1), (15, 1), (16, 2), (23, 2), (24, 3)):
            with self.subTest(demand=demand):
                dataset = sample_dataset(demand)
                chromosome = build_empty_chromosome(dataset)
                self.assertEqual(len(chromosome.sections), expected)
                self.assertEqual(len(chromosome.enrollments), demand)
                self.assertEqual(validate_structure(chromosome, dataset), ())

    def test_missing_and_extra_slots_are_detected_even_if_inactive(self):
        sections = dict(self.chromosome.sections)
        del sections['C1-02']
        sections['C1-03'] = SectionGene('C1-03', 'C1')
        c = Chromosome(sections, self.chromosome.enrollments)
        issues = validate_structure(c, self.dataset)
        self.assertTrue(any(i.location == 'sections[C1-02]' and 'is missing' in i.message for i in issues))
        self.assertTrue(any(i.location == 'sections[C1-03]' and 'extra section slot' in i.message for i in issues))

    def test_duplicate_section_ids_are_detected(self):
        c = Chromosome({'C1-01': SectionGene('C1-01', 'C1'),
                        'C1-02': SectionGene('C1-01', 'C1')}, self.chromosome.enrollments)
        self.assertTrue(any(i.message == 'duplicate section_id' for i in validate_structure(c, self.dataset)))

    def test_slot_cannot_be_rebound_to_another_existing_course(self):
        tables = dict(self.dataset.tables)
        tables['Courses'] += (replace(tables['Courses'][0], course_id='C2'),)
        dataset = replace(self.dataset, tables=MappingProxyType(tables))
        sections = dict(self.chromosome.sections)
        sections['C1-01'] = SectionGene('C1-01', 'C2')
        c = Chromosome(sections, self.chromosome.enrollments)
        self.assertTrue(any('belongs to a different course' in i.message for i in validate_structure(c, dataset)))

    def test_inactive_slots_must_clear_all_resources(self):
        for dirty in (SectionGene('C1-01', 'C1', professor_id='P1'),
                      SectionGene('C1-01', 'C1', room_id='R1'),
                      SectionGene('C1-01', 'C1', meeting_slots=[('Saturday', 'T1')])):
            c = self.chromosome.clone()
            c.replace_section(dirty)
            self.assertTrue(any('inactive section must have no' in i.message for i in validate_structure(c, self.dataset)))

    def test_manual_dataset_duplicate_requests_do_not_collapse_into_dict(self):
        tables = dict(self.dataset.tables)
        tables['StudentRequests'] += (replace(tables['StudentRequests'][0], priority=9),)
        dataset = replace(self.dataset, tables=MappingProxyType(tables))
        with self.assertRaisesRegex(ValueError, 'duplicate student request'):
            build_empty_chromosome(dataset)
        with self.assertRaisesRegex(ValueError, 'duplicate student request'):
            validate_structure(self.chromosome, dataset)

    def test_structure_does_not_evaluate_hard_constraints(self):
        tables = dict(self.dataset.tables)
        tables['TimeSlots'] = tuple(replace(s, is_allowed=False) for s in tables['TimeSlots'])
        tables['Classrooms'] = (replace(tables['Classrooms'][0], capacity=1, room_type='WrongType'),)
        tables['Professors'] = (replace(tables['Professors'][0], max_hours=0),)
        tables['Students'] = tuple(replace(s, min_credits=20, max_credits=20) for s in tables['Students'])
        # No qualification or availability tables: structural validation must not need them.
        dataset = replace(self.dataset, tables=MappingProxyType(tables))
        c = self.chromosome.clone()
        c.replace_section(active())
        c.replace_section(active('C1-02'))
        for i in range(15):
            c.assign((f'S{i}', 'C1'), 'C1-01')
        c.assign(('S15', 'C1'), 'C1-02')
        self.assertEqual(validate_structure(c, dataset), ())

    def test_constructor_owns_dicts_and_private_storage_is_not_mutable(self):
        sections = dict(self.chromosome.sections)
        enrollments = dict(self.chromosome.enrollments)
        c = Chromosome(sections, enrollments)
        c.record_evaluation(1, .5)
        sections['C1-01'] = active()
        enrollments[('S0', 'C1')] = 'C1-01'
        self.assertFalse(c.sections['C1-01'].active)
        self.assertIsNone(c.enrollments[('S0', 'C1')])
        self.assertEqual(c.hard_score, 1)
        with self.assertRaises(TypeError):
            c._sections['C1-01'] = active()
        with self.assertRaises(TypeError):
            c._enrollments[('S0', 'C1')] = 'C1-01'

    def test_unassignment_and_deactivation_invalidate_scores(self):
        c = self.chromosome
        c.replace_section(active())
        c.assign(('S0', 'C1'), 'C1-01')
        c.record_evaluation(3, .7)
        c.assign(('S0', 'C1'), None)
        self.assertIsNone(c.hard_score)
        self.assertIsNone(c.soft_score)
        c.record_evaluation(2, .6)
        c.replace_section(SectionGene('C1-01', 'C1'))
        self.assertIsNone(c.hard_score)
        self.assertIsNone(c.soft_score)
        self.assertEqual(validate_structure(c, self.dataset), ())

    def test_rejected_edits_leave_state_and_scores_unchanged(self):
        c = self.chromosome
        c.record_evaluation(5, .4)
        before = (dict(c.sections), dict(c.enrollments))
        for edit in (lambda: c.assign(('ghost', 'C1'), None),
                     lambda: c.assign(('S0', 'C1'), 'C1-01'),
                     lambda: c.replace_section(SectionGene('C1-03', 'C1')),
                     lambda: c.replace_section(SectionGene('C1-01', 'C2'))):
            with self.assertRaises((KeyError, ValueError)):
                edit()
            self.assertEqual((dict(c.sections), dict(c.enrollments)), before)
            self.assertEqual((c.hard_score, c.soft_score), (5, .4))

    def test_nested_mutable_ids_are_rejected(self):
        for field in ('professor_id', 'room_id'):
            with self.assertRaises(ValueError):
                SectionGene('C1-01', 'C1', **{field: ['not immutable']})
        with self.assertRaises(TypeError):
            Chromosome({}, {('S0', 'C1'): ['C1-01']})

    def test_explicit_invalidation_and_score_validation(self):
        self.chromosome.record_evaluation(0, .5)
        self.chromosome.invalidate_evaluation()
        self.assertIsNone(self.chromosome.hard_score)
        self.assertIsNone(self.chromosome.soft_score)
        for hard, soft in ((-1, .5), (1, float('nan')), (float('inf'), 0), (0, 2), (True, .5)):
            with self.assertRaises(ValueError):
                self.chromosome.record_evaluation(hard, soft)
        self.assertIsNone(self.chromosome.hard_score)
        with self.assertRaises(AttributeError):
            self.chromosome.hard_score = 0


if __name__ == '__main__':
    unittest.main()
