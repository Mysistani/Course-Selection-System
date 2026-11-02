"""Hand-calculable hard constraints; no Excel or random schedules needed."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from itertools import combinations
from types import MappingProxyType
import unittest

from scheduler.chromosome import Chromosome, SectionGene, build_empty_chromosome, validate_structure
from scheduler.constraints import StructuralValidationError, Violation, evaluate_hard_constraints
from scheduler.domain import (Availability, Course, Dataset, Faculty, Major, PassedCourse,
                              Professor, Qualification, Request, Room, Slot, Student)


def sample(course_count=1, demand=8, sessions=1):
    """Each course has its own cohort, professor and room; no conflicts by default."""
    courses = tuple(Course(f'C{i}', 'M', f'Course {i}', 3, 1, 'Theory', sessions, 90,
                           'Theory', 8, 20, 3) for i in range(1, course_count + 1))
    students, requests = [], []
    for i, course in enumerate(courses):
        for j in range(demand):
            sid = f'S{i * demand + j + 1}'
            students.append(Student(sid, 'M', 1, 0, 30))
            requests.append(Request(sid, course.course_id, 5))
    tables = {
        'Faculties': (Faculty('F', 'Faculty'),), 'Majors': (Major('M', 'F', 'Major'),),
        'Courses': courses, 'Students': tuple(students), 'StudentRequests': tuple(requests),
        'Professors': tuple(Professor(f'P{i}', 'M', f'Professor {i}', 0, 100) for i in range(1, course_count + 1)),
        'Classrooms': tuple(Room(f'R{i}', 'F', 'Theory', 20, 'Board') for i in range(1, course_count + 1)),
        'ProfessorCourses': tuple(Qualification(f'P{i}', c.course_id, True, 5)
                                  for i in range(1, course_count + 1) for c in courses),
        'PassedCourses': (), 'ProfessorAvailability': (), 'StudentAvailability': (),
        'ProfessorPreferences': (), 'StudentPreferences': (),
        'TimeSlots': (Slot('SAT-T1', 'Saturday', 'T1', 480, 570, True),
                      Slot('SAT-T2', 'Saturday', 'T2', 600, 690, True),
                      Slot('SAT-T3', 'Saturday', 'T3', 720, 810, True),
                      Slot('MON-T1', 'Monday', 'T1', 480, 570, True)),
    }
    return Dataset(MappingProxyType(tables), 'hand-calculated-fixture', ())


def with_rows(dataset, **tables):
    return replace(dataset, tables=MappingProxyType({**dataset.tables, **tables}))


def scheduled(dataset):
    c = build_empty_chromosome(dataset)
    for i, course in enumerate(dataset.tables['Courses'], 1):
        sid = f'{course.course_id}-01'
        if sid not in c.sections:
            continue
        slots = [('Saturday', f'T{i}')]
        if course.sessions_per_week == 2:
            slots.append(('Monday', 'T1'))
        c.replace_section(SectionGene(sid, course.course_id, True, f'P{i}', f'R{i}', slots))
    for request in c.enrollments:
        sid = f'{request[1]}-01'
        if sid in c.sections:
            c.assign(request, sid)
    return c


def overlap_second_slot(dataset):
    return with_rows(dataset, TimeSlots=tuple(replace(s, start_time=525, end_time=615)
                     if s.slot_id == 'SAT-T2' else s for s in dataset.tables['TimeSlots']))


class HardConstraintsTests(unittest.TestCase):
    def assert_counts(self, result, expected):
        self.assertEqual({k: v for k, v in result.counts_by_type.items() if v}, expected)
        self.assertEqual(result.total_penalty, sum(expected.values()))
        self.assertEqual(sum(v.severity for v in result.violations), result.total_penalty)
        self.assertEqual(result.is_feasible, not expected)

    def test_feasible_chromosome_and_derived_metrics(self):
        d = sample(2)
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {})
        self.assertEqual(result.violations, ())
        self.assertEqual(result.metrics.section_sizes, {'C1-01': 8, 'C2-01': 8})
        self.assertEqual(result.metrics.professor_minutes, {'P1': 90, 'P2': 90})
        self.assertEqual(result.metrics.student_credits['S1'], 3)
        self.assertEqual(result.diagnostics, ())

    def test_professor_conflict_uses_intervals_not_slot_ids(self):
        d = overlap_second_slot(sample(2))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], professor_id='P1'))
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H04_PROFESSOR_OVERLAP': 1})
        v, = result.violations
        self.assertEqual(v.professor_id, 'P1')
        self.assertEqual(v.section_ids, ('C1-01', 'C2-01'))
        self.assertEqual(v.course_ids, ('C1', 'C2'))
        self.assertEqual(v.slot_keys, (('Saturday', 'T1'), ('Saturday', 'T2')))
        self.assertEqual((v.day, v.start_minute, v.end_minute), ('Saturday', 525, 570))

    def test_room_conflict_is_independent(self):
        d = overlap_second_slot(sample(2))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], room_id='R1'))
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H04_ROOM_OVERLAP': 1})
        self.assertEqual(result.violations[0].room_id, 'R1')
        self.assertEqual(result.violations[0].section_ids, ('C1-01', 'C2-01'))

    def test_student_conflict_is_one_pair_for_that_student(self):
        d = overlap_second_slot(sample(2))
        d = with_rows(d, StudentRequests=d.tables['StudentRequests'] + (Request('S1', 'C2', 5),))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H04_STUDENT_OVERLAP': 1})
        self.assertEqual(result.violations[0].student_id, 'S1')
        self.assertEqual(result.violations[0].section_ids, ('C1-01', 'C2-01'))
        self.assertEqual(result.metrics.student_credits['S1'], 6)

    def test_missing_and_false_qualification_both_fail_once_per_section(self):
        for qualifications in ((), (Qualification('P1', 'C1', False, 5),)):
            with self.subTest(qualifications=qualifications):
                d = with_rows(sample(), ProfessorCourses=qualifications)
                result = evaluate_hard_constraints(scheduled(d), d)
                self.assert_counts(result, {'H03_UNQUALIFIED_PROFESSOR': 1})
                v, = result.violations
                self.assertEqual((v.professor_id, v.section_ids, v.course_ids), ('P1', ('C1-01',), ('C1',)))
                self.assertTrue(any(x.kind == 'DATASET_NO_QUALIFIED_PROFESSOR' for x in result.diagnostics))

    def test_room_type_mismatch_once_per_section(self):
        d = sample(sessions=2)
        d = with_rows(d, Classrooms=(replace(d.tables['Classrooms'][0], room_type='ComputerLab'),))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H06_ROOM_TYPE': 1})
        self.assertEqual(result.violations[0].room_id, 'R1')
        self.assertEqual(result.violations[0].section_ids, ('C1-01',))

    def test_group_seven_vs_eight_and_empty_active_group(self):
        for size, penalty in ((0, 8), (7, 1), (8, 0)):
            with self.subTest(size=size):
                d = sample()
                c = scheduled(d)
                for i in range(size + 1, 9):
                    c.assign((f'S{i}', 'C1'), None)
                result = evaluate_hard_constraints(c, d)
                self.assert_counts(result, {'H07_SECTION_UNDERFLOW': penalty} if penalty else {})
                self.assertEqual(result.metrics.section_sizes['C1-01'], size)
                if penalty:
                    v, = result.violations
                    self.assertEqual((v.section_ids, v.measured_value, v.limit, v.unit), (('C1-01',), size, 8, 'students'))

    def test_effective_capacity_is_minimum_of_course_and_room(self):
        for limiting in ('course', 'room'):
            for demand, penalty in ((8, 0), (9, 1)):
                with self.subTest(limiting=limiting, demand=demand):
                    d = sample(demand=demand)
                    if limiting == 'course':
                        d = with_rows(d, Courses=(replace(d.tables['Courses'][0], max_section_size=8),))
                    else:
                        d = with_rows(d, Classrooms=(replace(d.tables['Classrooms'][0], capacity=8),))
                    result = evaluate_hard_constraints(scheduled(d), d)
                    self.assert_counts(result, {'H07_SECTION_OVERFLOW': penalty} if penalty else {})
                    self.assertEqual(result.metrics.section_capacities['C1-01'], 8)
                    if penalty:
                        v, = result.violations
                        self.assertEqual((v.section_ids, v.measured_value, v.limit, v.severity), (('C1-01',), 9, 8, 1))

    def test_credit_underflow_overflow_and_no_enrollments(self):
        d = sample()
        d = with_rows(d, Students=tuple(replace(s, min_credits=5, max_credits=20) if s.student_id == 'S1'
                      else replace(s, max_credits=2) if s.student_id == 'S2' else s for s in d.tables['Students']))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H08_CREDIT_UNDERFLOW': 2, 'H08_CREDIT_OVERFLOW': 1})
        self.assertEqual({v.student_id: (v.measured_value, v.limit) for v in result.violations}, {'S1': (3, 5), 'S2': (3, 2)})
        empty = build_empty_chromosome(d)
        result = evaluate_hard_constraints(empty, d)
        self.assert_counts(result, {'H08_CREDIT_UNDERFLOW': 5})
        self.assertEqual(result.metrics.student_credits['S1'], 0)
        self.assertEqual(result.metrics.professor_minutes['P1'], 0)
        self.assertEqual(len(result.metrics.unassigned_requests), 8)

    def test_teaching_limit_exact_and_ceiling_thirty_minute_blocks(self):
        for limit, blocks in ((90, 0), (89, 1), (60, 1), (59, 2), (0, 3)):
            with self.subTest(limit=limit):
                d = sample()
                d = with_rows(d, Professors=(replace(d.tables['Professors'][0], max_hours=limit / 60),))
                result = evaluate_hard_constraints(scheduled(d), d)
                self.assert_counts(result, {'H09_TEACHING_OVERLOAD': blocks} if blocks else {})
                self.assertEqual(result.metrics.professor_minutes['P1'], 90)
                self.assertEqual(result.metrics.professor_minute_excess['P1'], max(0, 90 - limit))
                if blocks:
                    v, = result.violations
                    self.assertEqual((v.professor_id, v.measured_value, v.limit, v.unit), ('P1', 90, limit, 'minutes'))

    def test_minimum_teaching_hours_only_metrics(self):
        d = sample()
        d = with_rows(d, Professors=(replace(d.tables['Professors'][0], min_hours=3), Professor('P2', 'M', 'Unused', 5, 100)))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {})
        self.assertEqual(result.metrics.professor_minute_shortfall, {'P1': 90, 'P2': 300})
        self.assertEqual(result.metrics.professor_minutes['P2'], 0)

    def test_forbidden_slot_counts_one_meeting_not_attendees(self):
        d = sample(sessions=2)
        d = with_rows(d, TimeSlots=tuple(replace(s, is_allowed=False) if s.slot_id == 'SAT-T1' else s for s in d.tables['TimeSlots']))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H02_DISALLOWED_SLOT': 1})
        self.assertEqual(result.violations[0].slot_keys, (('Saturday', 'T1'),))
        self.assertEqual(result.violations[0].section_ids, ('C1-01',))

    def test_unavailable_counts_one_presence_per_person_and_meeting(self):
        d = overlap_second_slot(sample())
        d = with_rows(d, ProfessorAvailability=(Availability('P1', 'Saturday', 'T1', 'Unavailable', None),
                                               Availability('P1', 'Saturday', 'T2', 'Unavailable', None)),
                      StudentAvailability=(Availability('S1', 'Saturday', 'T2', 'Unavailable', None),))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H05_PROFESSOR_UNAVAILABLE': 1, 'H05_STUDENT_UNAVAILABLE': 1})
        self.assertEqual({v.professor_id for v in result.violations if v.professor_id}, {'P1'})
        self.assertEqual({v.student_id for v in result.violations if v.student_id}, {'S1'})
        self.assertTrue(all(v.section_ids == ('C1-01',) for v in result.violations))

    def test_absent_availability_and_preferred_are_not_forbidden(self):
        d = sample()
        for rows in ((), (Availability('P1', 'Saturday', 'T1', 'Preferred', 10),)):
            d = with_rows(d, ProfessorAvailability=rows)
            self.assert_counts(evaluate_hard_constraints(scheduled(d), d), {})

    def test_structurally_invalid_chromosome_raises_with_original_issues(self):
        d = sample()
        valid = scheduled(d)
        cases = [Chromosome({}, valid.enrollments),
                 Chromosome(valid.sections, {**valid.enrollments, ('S1', 'C1'): 'missing'})]
        for slots in ([('Saturday', 'T1')] * 2, [('Never', 'T1')], []):
            c = scheduled(d)
            c.replace_section(replace(c.sections['C1-01'], meeting_slots=slots))
            cases.append(c)
        for c in cases:
            with self.subTest(candidate=c):
                c.record_evaluation(123, .7)
                expected = validate_structure(c, d)
                with self.assertRaises(StructuralValidationError) as context:
                    evaluate_hard_constraints(c, d)
                self.assertEqual(context.exception.issues, expected)
                self.assertIn(expected[0].location, str(context.exception))
                self.assertEqual((c.hard_score, c.soft_score), (123, .7))

    def test_evaluation_does_not_mutate_dataset_chromosome_or_cache(self):
        d = overlap_second_slot(sample(2))
        c = scheduled(d)
        c.record_evaluation(123, .7)
        before_dataset = deepcopy(dict(d.tables))
        before_c = (dict(c.sections), dict(c.enrollments), c.hard_score, c.soft_score)
        result = evaluate_hard_constraints(c, d)
        self.assertEqual(before_c, (dict(c.sections), dict(c.enrollments), c.hard_score, c.soft_score))
        self.assertEqual(before_dataset, dict(d.tables))
        self.assertEqual((d.sha256, d.warnings), ('hand-calculated-fixture', ()))
        self.assertEqual(result, evaluate_hard_constraints(c, d))

    def test_touching_intervals_and_different_days_do_not_overlap(self):
        for day, start, end in (('Saturday', 570, 660), ('Monday', 480, 570)):
            with self.subTest(day=day):
                d = sample(2)
                # Keep the slot key Saturday/T2 for touching case; for Monday use Monday/T1.
                if day == 'Saturday':
                    d = with_rows(d, TimeSlots=tuple(replace(s, start_time=start, end_time=end) if s.slot_id == 'SAT-T2' else s for s in d.tables['TimeSlots']))
                c = scheduled(d)
                c.replace_section(replace(c.sections['C2-01'], professor_id='P1', room_id='R1',
                                          meeting_slots=[(day, 'T2' if day == 'Saturday' else 'T1')]))
                self.assert_counts(evaluate_hard_constraints(c, d), {})

    def test_three_meetings_have_three_pairs_per_shared_resource(self):
        d = sample(3)
        c = scheduled(d)
        for sid in c.sections:
            c.replace_section(replace(c.sections[sid], professor_id='P1', room_id='R1', meeting_slots=[('Saturday', 'T1')]))
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H04_PROFESSOR_OVERLAP': 3, 'H04_ROOM_OVERLAP': 3})
        expected_pairs = set(combinations(('C1-01', 'C2-01', 'C3-01'), 2))
        for kind in ('H04_PROFESSOR_OVERLAP', 'H04_ROOM_OVERLAP'):
            rows = [v for v in result.violations if v.constraint_type == kind]
            self.assertEqual(len(rows), 3)
            self.assertEqual({v.section_ids for v in rows}, expected_pairs)
        self.assertEqual(result.metrics.professor_minutes['P1'], 270)

    def test_two_overlapping_meetings_of_same_group_not_duplicate_attendance(self):
        d = overlap_second_slot(sample(sessions=2))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C1-01'], meeting_slots=[('Saturday', 'T1'), ('Saturday', 'T2')]))
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H04_PROFESSOR_OVERLAP': 1, 'H04_ROOM_OVERLAP': 1, 'H04_STUDENT_OVERLAP': 8})
        self.assertEqual({v.student_id for v in result.violations if v.student_id}, {f'S{i}' for i in range(1, 9)})
        self.assertEqual(result.metrics.student_credits['S1'], 3)
        self.assertEqual(result.metrics.professor_minutes['P1'], 180)

    def test_passed_course_assignment_is_h10_but_unassigned_request_is_not(self):
        # Loader rejects this input in normal operation; exercise H10 defensively
        # on a manually constructed Dataset without weakening loader validation.
        d = with_rows(sample(), PassedCourses=(PassedCourse('S1', 'C1'),))
        c = scheduled(d)
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H10_PASSED_COURSE': 1})
        v, = result.violations
        self.assertEqual((v.student_id, v.course_ids, v.section_ids), ('S1', ('C1',), ('C1-01',)))
        empty = build_empty_chromosome(d)
        self.assert_counts(evaluate_hard_constraints(empty, d), {})

    def test_low_demand_diagnostics_do_not_remove_requests_or_score(self):
        d = sample(demand=3)
        c = build_empty_chromosome(d)
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {})
        self.assertEqual(len(result.metrics.unassigned_requests), 3)
        self.assertEqual(len(c.enrollments), 3)
        self.assertEqual(result.diagnostics[0].kind, 'DATASET_LOW_DEMAND')
        self.assertEqual(result.diagnostics[0].related_ids, ('C1',))

    def test_result_and_nested_metrics_are_immutable(self):
        d = sample()
        c = scheduled(d)
        c.assign(('S1', 'C1'), None)
        result = evaluate_hard_constraints(c, d)
        with self.assertRaises(FrozenInstanceError):
            result.violations[0].severity = 5
        with self.assertRaises(TypeError):
            result.counts_by_type['H07_SECTION_UNDERFLOW'] = 9
        with self.assertRaises(TypeError):
            result.metrics.section_sizes['C1-01'] = 9
        with self.assertRaises(FrozenInstanceError):
            result.metrics.unassigned_requests = ()

    def test_raw_severity_counts_are_separate_from_event_counts(self):
        d = sample()
        c = scheduled(d)
        for i in range(1, 4):
            c.assign((f'S{i}', 'C1'), None)
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H07_SECTION_UNDERFLOW': 3})
        self.assertEqual(result.events_by_type['H07_SECTION_UNDERFLOW'], 1)

    def test_violation_does_not_retain_mutable_input_sequences(self):
        sections = ['C1-01']
        slots = [['Saturday', 'T1']]
        v = Violation('H02_DISALLOWED_SLOT', 1, section_ids=sections, slot_keys=slots)
        sections.append('C2-01')
        slots[0][0] = 'Monday'
        self.assertEqual(v.section_ids, ('C1-01',))
        self.assertEqual(v.slot_keys, (('Saturday', 'T1'),))
        for severity in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                Violation('H02_DISALLOWED_SLOT', severity)

    def test_student_with_no_input_requests_is_still_checked(self):
        d = sample()
        d = with_rows(d, Students=d.tables['Students'] + (Student('NoRequests', 'M', 1, 4, 20),))
        result = evaluate_hard_constraints(scheduled(d), d)
        self.assert_counts(result, {'H08_CREDIT_UNDERFLOW': 4})
        v, = result.violations
        self.assertEqual((v.student_id, v.measured_value, v.limit), ('NoRequests', 0, 4))

    def test_student_three_way_overlap_has_three_pairs_not_six(self):
        d = sample(3)
        d = with_rows(d, StudentRequests=d.tables['StudentRequests'] +
                      (Request('S1', 'C2', 5), Request('S1', 'C3', 5)))
        c = scheduled(d)
        for sid in c.sections:
            c.replace_section(replace(c.sections[sid], meeting_slots=[('Saturday', 'T1')]))
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {'H04_STUDENT_OVERLAP': 3})
        self.assertEqual({v.student_id for v in result.violations}, {'S1'})
        self.assertEqual({v.section_ids for v in result.violations}, set(combinations(('C1-01', 'C2-01', 'C3-01'), 2)))

    def test_zero_penalty_retains_shortfall_diagnostics_without_inventing_groups(self):
        d = sample(demand=3)
        d = with_rows(d, ProfessorCourses=(), Classrooms=())
        c = build_empty_chromosome(d)
        result = evaluate_hard_constraints(c, d)
        self.assert_counts(result, {})
        self.assertEqual(set(c.sections), set())
        self.assertEqual(result.metrics.professor_minutes['P1'], 0)
        self.assertEqual({x.kind for x in result.diagnostics}, {
            'DATASET_LOW_DEMAND', 'DATASET_NO_QUALIFIED_PROFESSOR', 'DATASET_NO_COMPATIBLE_ROOM'})


if __name__ == '__main__':
    unittest.main()
