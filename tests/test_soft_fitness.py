from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import unittest

from scheduler.chromosome import Chromosome, build_empty_chromosome
from scheduler.constraints import StructuralValidationError
from scheduler.domain import Availability, Preference, Qualification, Request, Slot
from scheduler.evaluation_context import EvaluationContext
from scheduler.policies import DEFAULT_POLICY
from scheduler.soft_fitness import aggregate_soft_scores, evaluate_soft_fitness
from test_constraints import sample, scheduled, with_rows


def full_calendar(d):
    times = ((480, 570), (600, 690), (810, 900), (930, 1020), (1050, 1140))
    slots = tuple(Slot(f'{day}-T{i}', day, f'T{i}', start, end, True)
                  for day in ('Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday')
                  for i, (start, end) in enumerate(times, 1))
    return with_rows(d, TimeSlots=slots)


def add_shared_requests(d, courses=('C2',)):
    return with_rows(d, StudentRequests=d.tables['StudentRequests'] + tuple(Request('S1', cid, 5) for cid in courses))


class SoftFitnessTests(unittest.TestCase):
    def component(self, result, person, kind, professor=False):
        return (result.professors if professor else result.students)[person].components[kind]

    def test_priority_coverage_exactly_seven_tenths(self):
        d = sample()
        d = with_rows(d, StudentRequests=tuple(replace(r, priority=3 if r.student_id == 'S1' else 1) for r in d.tables['StudentRequests']))
        c = scheduled(d)
        c.assign(('S1', 'C1'), None)
        result = evaluate_soft_fitness(c, d)
        self.assertEqual((result.served_priority, result.total_priority), (7, 10))
        self.assertAlmostEqual(result.request_coverage, .7)
        self.assertAlmostEqual(result.total_score, .7)  # No active preference group.

    def test_no_served_requests_cannot_receive_full_score(self):
        d = with_rows(full_calendar(sample()), StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),))
        c = build_empty_chromosome(d)
        result = evaluate_soft_fitness(c, d)
        self.assertEqual((result.total_score, result.request_coverage, result.assigned_request_count), (0, 0, 0))
        self.assertEqual(result.students['S1'].score, 0)
        self.assertEqual(result.students['S1'].reason, 'inactive_student')

    def test_no_input_requests_is_explicit_and_has_no_division_by_zero(self):
        d = with_rows(sample(), StudentRequests=())
        result = evaluate_soft_fitness(build_empty_chromosome(d), d)
        self.assertEqual(result.coverage_status, 'no_input_requests')
        self.assertEqual((result.total_score, result.request_coverage, result.total_priority, result.request_count), (0, 0, 0, 0))

    def test_exact_three_days_vs_target_two_is_point_eight(self):
        d = add_shared_requests(full_calendar(sample(3)), ('C2', 'C3'))
        d = with_rows(d, StudentPreferences=(Preference('S1', 'PreferredTeachingDays', 2, 10),))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], meeting_slots=[('Monday', 'T1')]))
        c.replace_section(replace(c.sections['C3-01'], meeting_slots=[('Tuesday', 'T1')]))
        result = evaluate_soft_fitness(c, d)
        self.assertEqual(result.students['S1'].actual_days, 3)
        self.assertEqual(self.component(result, 'S1', 'PreferredTeachingDays').normalizer, 5)
        self.assertAlmostEqual(result.students['S1'].score, .8)
        self.assertAlmostEqual(result.total_score, .8)

    def test_calendar_day_count_is_not_hardcoded(self):
        d = sample()  # Saturday and Monday only.
        d = with_rows(d, StudentPreferences=(Preference('S1', 'PreferredTeachingDays', 2, 10),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertEqual(self.component(result, 'S1', 'PreferredTeachingDays').normalizer, 2)
        self.assertAlmostEqual(result.students['S1'].score, .5)

    def test_gap_thirty_vs_thirty_one_minutes_exact(self):
        for gap, excess in ((30, 0), (31, 1)):
            with self.subTest(gap=gap):
                d = add_shared_requests(full_calendar(sample(2)))
                d = with_rows(d, StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),),
                              TimeSlots=tuple(replace(s, start_time=570 + gap, end_time=660 + gap)
                                              if s.day == 'Saturday' and s.time_slot == 'T2' else s for s in d.tables['TimeSlots']))
                context = EvaluationContext.from_dataset(d)
                self.assertEqual(context.gap_normalizer_minutes, 2250)
                result = evaluate_soft_fitness(scheduled(d), context)
                part = self.component(result, 'S1', 'MinimizeGaps')
                self.assertEqual(part.raw_value, excess)
                self.assertEqual(result.students['S1'].excess_gap_minutes, excess)
                self.assertAlmostEqual(part.score, 1 - excess / 2250, places=14)

    def test_gap_ignores_outer_time_and_other_days(self):
        d = with_rows(full_calendar(sample(sessions=2)), StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertEqual(self.component(result, 'S1', 'MinimizeGaps').score, 1)
        self.assertEqual(result.students['S1'].excess_gap_minutes, 0)

    def test_explicit_gap_normalizer_in_policy(self):
        d = add_shared_requests(full_calendar(sample(2)))
        d = with_rows(d, StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], meeting_slots=[('Saturday', 'T3')]))
        context = EvaluationContext.from_dataset(d, replace(DEFAULT_POLICY, gap_normalizer_minutes=300))
        part = self.component(evaluate_soft_fitness(c, context), 'S1', 'MinimizeGaps')
        self.assertEqual(part.raw_value, 210)
        self.assertAlmostEqual(part.score, .3)

    def test_nested_overlapping_activity_does_not_create_false_free_gap(self):
        d = add_shared_requests(full_calendar(sample(3)), ('C2', 'C3'))
        # Deliberately overlapping hard-infeasible timetable: still defined Soft.
        slots = (Slot('A', 'Saturday', 'T1', 480, 720, True),
                 Slot('B', 'Saturday', 'T2', 540, 630, True),
                 Slot('C', 'Saturday', 'T3', 720, 810, True))
        d = with_rows(d, TimeSlots=slots, StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertEqual(self.component(result, 'S1', 'MinimizeGaps').raw_value, 0)

    def test_difficult_courses_distinct_per_day_and_exact_normalization(self):
        d = add_shared_requests(full_calendar(sample(2)))
        d = with_rows(d, Courses=tuple(replace(c, difficulty_level=4) for c in d.tables['Courses']),
                      StudentPreferences=(Preference('S1', 'SpreadDifficultCourses', 1, 10),))
        result = evaluate_soft_fitness(scheduled(d), d)
        part = self.component(result, 'S1', 'SpreadDifficultCourses')
        self.assertEqual((part.raw_value, part.normalizer, part.score), (1, 2, .5))

    def test_same_difficult_course_twice_in_day_counts_once(self):
        d = full_calendar(sample(sessions=2))
        d = with_rows(d, Courses=(replace(d.tables['Courses'][0], difficulty_level=4),),
                      StudentPreferences=(Preference('S1', 'SpreadDifficultCourses', 1, 10),))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C1-01'], meeting_slots=[('Saturday', 'T1'), ('Saturday', 'T2')]))
        part = self.component(evaluate_soft_fitness(c, d), 'S1', 'SpreadDifficultCourses')
        self.assertEqual((part.raw_value, part.normalizer, part.score), (0, 2, 1))

    def test_easy_courses_have_no_difficulty_penalty_and_zero_normalizer_is_safe(self):
        d = with_rows(sample(), StudentPreferences=(Preference('S1', 'SpreadDifficultCourses', 1, 10),))
        part = self.component(evaluate_soft_fitness(scheduled(d), d), 'S1', 'SpreadDifficultCourses')
        self.assertEqual((part.raw_value, part.normalizer, part.score), (0, 0, 1))

    def test_time_preference_neutral_and_zero_are_not_confused(self):
        d = sample()
        d = with_rows(d, StudentPreferences=(Preference('S1', 'PreferredTimeSlots', None, 10),))
        for availability, expected in (((), .5), ((Availability('S1', 'Saturday', 'T1', 'Preferred', 8),), .8),
                                       ((Availability('S1', 'Saturday', 'T1', 'Preferred', 0),), 0),
                                       ((Availability('S1', 'Saturday', 'T1', 'Unavailable', None),), .5)):
            current = with_rows(d, StudentAvailability=availability)
            self.assertAlmostEqual(self.component(evaluate_soft_fitness(scheduled(current), current), 'S1', 'PreferredTimeSlots').score, expected)

    def test_component_weight_is_not_local_desirability(self):
        d = full_calendar(sample())
        d = with_rows(d, StudentPreferences=(Preference('S1', 'PreferredTeachingDays', 1, 7),
                                             Preference('S1', 'PreferredTimeSlots', None, 3)),
                      StudentAvailability=(Availability('S1', 'Saturday', 'T1', 'Preferred', 5),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertAlmostEqual(result.students['S1'].score, .85)
        self.assertAlmostEqual(self.component(result, 'S1', 'PreferredTimeSlots').score, .5)

    def test_professor_time_preferences_use_professor_rows_and_personal_weights(self):
        d = full_calendar(sample())
        d = with_rows(d, ProfessorPreferences=(Preference('P1', 'PreferredTeachingDays', 2, 7),
                                               Preference('P1', 'PreferredTimeSlots', None, 3)),
                      ProfessorAvailability=(Availability('P1', 'Saturday', 'T1', 'Preferred', 9),),
                      StudentAvailability=(Availability('S1', 'Saturday', 'T1', 'Preferred', 1),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertAlmostEqual(self.component(result, 'P1', 'PreferredTimeSlots', professor=True).score, .9)
        self.assertAlmostEqual(result.professors['P1'].score, .83)  # (.8*7 + .9*3)/10.

    def test_professor_gap_and_custom_neutral_policy_have_exact_scores(self):
        d = full_calendar(sample(sessions=2))
        d = with_rows(d, ProfessorPreferences=(Preference('P1', 'MinimizeGaps', 30, 1),
                                               Preference('P1', 'PreferredTimeSlots', None, 1)),
                      TimeSlots=tuple(replace(s, start_time=601, end_time=691)
                                      if s.day == 'Saturday' and s.time_slot == 'T2' else s for s in d.tables['TimeSlots']))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C1-01'], meeting_slots=[('Saturday', 'T1'), ('Saturday', 'T2')]))
        ctx = EvaluationContext.from_dataset(d, replace(DEFAULT_POLICY, neutral_time_score=.25))
        result = evaluate_soft_fitness(c, ctx)
        self.assertAlmostEqual(self.component(result, 'P1', 'MinimizeGaps', professor=True).score, 1 - 1/2250)
        self.assertAlmostEqual(self.component(result, 'P1', 'PreferredTimeSlots', professor=True).score, .25)
        self.assertAlmostEqual(result.professor_satisfaction, (1 - 1/2250 + .25)/2)

    def test_score_bounds_hold_when_gap_normalizer_saturates(self):
        d = add_shared_requests(full_calendar(sample(2)))
        d = with_rows(d, StudentPreferences=(Preference('S1', 'MinimizeGaps', 30, 10),))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], meeting_slots=[('Saturday', 'T5')]))
        ctx = EvaluationContext.from_dataset(d, replace(DEFAULT_POLICY, gap_normalizer_minutes=1))
        result = evaluate_soft_fitness(c, ctx)
        self.assertEqual(self.component(result, 'S1', 'MinimizeGaps').score, 0)
        for person in (*result.students.values(), *result.professors.values()):
            if person.score is not None:
                self.assertTrue(0 <= person.score <= 1)
            for component in person.components.values():
                self.assertTrue(0 <= component.score <= 1)
        self.assertTrue(0 <= result.total_score <= 1)

    def test_willingness_is_weighted_by_teaching_minutes(self):
        d = full_calendar(sample(2))
        d = with_rows(d, Courses=(replace(d.tables['Courses'][0], sessions_per_week=2), d.tables['Courses'][1]),
                      ProfessorPreferences=(Preference('P1', 'PreferredCourses', None, 10),),
                      ProfessorCourses=(Qualification('P1', 'C1', True, 10), Qualification('P1', 'C2', True, 4)))
        c = scheduled(d)
        c.replace_section(replace(c.sections['C2-01'], professor_id='P1'))
        part = self.component(evaluate_soft_fitness(c, d), 'P1', 'PreferredCourses', professor=True)
        self.assertEqual((part.raw_value, part.normalizer), (216, 270))
        self.assertAlmostEqual(part.score, .8)

    def test_inactive_professor_is_excluded_but_inactive_student_is_zero(self):
        d = sample(2)
        d = with_rows(d, StudentPreferences=(Preference('S9', 'MinimizeGaps', 30, 10),),
                      ProfessorPreferences=(Preference('P1', 'PreferredCourses', None, 10),
                                            Preference('P2', 'PreferredCourses', None, 10)))
        c = scheduled(d)
        for request in c.enrollments:
            if request[1] == 'C2':
                c.assign(request, None)
        c.replace_section(replace(c.sections['C2-01'], active=False, professor_id=None, room_id=None, meeting_slots=()))
        result = evaluate_soft_fitness(c, d)
        self.assertEqual(result.students['S9'].score, 0)
        self.assertTrue(result.students['S9'].included)
        self.assertFalse(result.professors['P2'].included)
        self.assertIsNone(result.professors['P2'].score)
        self.assertAlmostEqual(result.professor_satisfaction, .5)  # P1 Willingness=5 only.
        self.assertAlmostEqual(result.total_score, .125)  # .5 coverage * mean(0,.5).

    def test_all_zero_weight_people_are_excluded_not_dissatisfied(self):
        d = with_rows(sample(), StudentPreferences=(Preference('S1', 'PreferredTimeSlots', None, 0),),
                      ProfessorPreferences=(Preference('P1', 'PreferredCourses', None, 0),))
        result = evaluate_soft_fitness(scheduled(d), d)
        self.assertIsNone(result.students['S1'].score)
        self.assertFalse(result.students['S1'].included)
        self.assertIsNone(result.student_satisfaction)
        self.assertIsNone(result.professor_satisfaction)
        self.assertEqual(result.total_score, 1)  # Coverage-only, no invented dissatisfaction.

    def test_aggregation_latest_product_and_named_old_alternative(self):
        self.assertAlmostEqual(aggregate_soft_scores(.5, .8, .9, assigned_count=1), .425)
        policy = replace(DEFAULT_POLICY, soft_aggregation='equal_components')
        self.assertAlmostEqual(aggregate_soft_scores(.5, .8, .9, assigned_count=1, policy=policy), float(Fraction(11, 15)))
        self.assertEqual(aggregate_soft_scores(0, 1, 1, assigned_count=0, policy=policy), 0)

    def test_soft_does_not_write_input_or_cached_scores(self):
        d = with_rows(sample(), StudentPreferences=(Preference('S1', 'PreferredTimeSlots', None, 10),))
        c = scheduled(d)
        c.record_evaluation(42, .123)
        before = (dict(c.sections), dict(c.enrollments), c.hard_score, c.soft_score, dict(d.tables))
        first = evaluate_soft_fitness(c, d)
        self.assertEqual(first, evaluate_soft_fitness(c, d))
        self.assertEqual(before, (dict(c.sections), dict(c.enrollments), c.hard_score, c.soft_score, dict(d.tables)))
        with self.assertRaises(TypeError):
            first.students['S1'].components['X'] = None
        with self.assertRaises(FrozenInstanceError):
            first.total_score = .3

    def test_structural_error_is_shared_with_hard_evaluator(self):
        d = sample()
        with self.assertRaises(StructuralValidationError) as error:
            evaluate_soft_fitness(Chromosome({}, {}), d)
        self.assertTrue(error.exception.issues)


if __name__ == '__main__':
    unittest.main()
