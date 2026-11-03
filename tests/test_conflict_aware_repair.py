"""Room-relocation safety, canonical acceptance, budgets and the actual Hard=4 result."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from random import Random
import random
import unittest
from unittest.mock import patch

from scheduler import load_dataset
from scheduler.chromosome import validate_structure
from scheduler.domain import Availability, Request, Slot
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution
from scheduler.evolution_reporting import restore_solution
from scheduler.fitness import evaluate_fitness
from scheduler.initialization import chromosome_fingerprint
from scheduler.mutation import _State
from scheduler.operator_support import evaluate_snapshot
from scheduler.repair import RepairSettings, repair_conflicts
from scheduler.repair_relocation import MeetingRelocation, RelocationIndex, relocation_proposals
from test_constraints import sample, scheduled, with_rows

ROOT=Path(__file__).resolve().parents[1]


def conflict_fixture(sessions=1):
    d=sample(2,sessions=sessions)
    if sessions==2:
        d=with_rows(d,TimeSlots=d.tables['TimeSlots']+(Slot('MON-T2','Monday','T2',600,690,True),))
    c=scheduled(d)
    c.replace_section(replace(c.sections['C2-01'],room_id='R1',meeting_slots=(('Saturday','T1'),)+((('Monday','T2'),) if sessions==2 else ())))
    return c,EvaluationContext.from_dataset(d)


class RelocationTests(unittest.TestCase):
    def test_room_conflict_is_resolved_without_touching_enrollments_or_professors(self):
        c,ctx=conflict_fixture();fp=chromosome_fingerprint(c);c.record_evaluation(1,.3)
        result=repair_conflicts(c,ctx,Random(11))
        self.assertEqual(result.report.hard_before,1);self.assertEqual(result.report.hard_after,0)
        self.assertEqual(result.report.room_conflicts_resolved,1)
        self.assertEqual(dict(result.chromosome.enrollments),dict(c.enrollments))
        self.assertEqual(chromosome_fingerprint(c),fp);self.assertEqual(c.hard_score,1)
        self.assertIsNone(result.chromosome.hard_score)
        self.assertTrue(all(result.chromosome.sections[sid].professor_id==s.professor_id for sid,s in c.sections.items()))
        self.assertFalse(validate_structure(result.chromosome,ctx))

    def test_only_selected_meeting_changes_and_same_room_is_preferred(self):
        c,ctx=conflict_fixture(2)
        r=repair_conflicts(c,ctx,Random(11))
        self.assertEqual(r.report.hard_after,0)
        changed=[sid for sid in c.sections if c.sections[sid]!=r.chromosome.sections[sid]]
        self.assertEqual(len(changed),1)
        sid=changed[0];before,after=c.sections[sid],r.chromosome.sections[sid]
        self.assertEqual(before.room_id,after.room_id)
        self.assertEqual(before.meeting_slots[1],after.meeting_slots[1])
        self.assertEqual(len(after.meeting_slots),2);self.assertEqual(len(set(after.meeting_slots)),2)
        self.assertEqual(before.course_id,after.course_id)

    def test_professor_unavailable_is_prefiltered(self):
        c,ctx=conflict_fixture()
        d=with_rows(ctx.dataset,ProfessorAvailability=(Availability('P1','Saturday','T2','Unavailable',None),))
        ctx=EvaluationContext.from_dataset(d)
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T2'),'R1')))

    def test_student_unavailable_is_prefiltered(self):
        c,ctx=conflict_fixture()
        d=with_rows(ctx.dataset,StudentAvailability=(Availability('S1','Saturday','T2','Unavailable',None),))
        ctx=EvaluationContext.from_dataset(d)
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T2'),'R1')))

    def test_student_overlap_is_prefiltered(self):
        d=sample(3);d=with_rows(d,StudentRequests=d.tables['StudentRequests']+(Request('S1','C3',5),))
        ctx=EvaluationContext.from_dataset(d);c=scheduled(d)
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T3'),'R1')))

    def test_professor_overlap_is_prefiltered(self):
        d=sample(3);ctx=EvaluationContext.from_dataset(d);c=scheduled(d)
        c.replace_section(replace(c.sections['C3-01'],professor_id='P1'))
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T3'),'R1')))

    def test_room_overlap_is_prefiltered(self):
        d=sample(3);ctx=EvaluationContext.from_dataset(d);c=scheduled(d)
        c.replace_section(replace(c.sections['C3-01'],room_id='R1'))
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T3'),'R1')))

    def test_alternate_room_checks_unchanged_weekly_meetings(self):
        d=sample(3,sessions=2);ctx=EvaluationContext.from_dataset(d);c=scheduled(d)
        c.replace_section(replace(c.sections['C3-01'],room_id='R2'))
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T1'),'R2')))

    def test_compatible_room_fallback_when_no_time_move_exists(self):
        c,ctx=conflict_fixture()
        ctx=EvaluationContext.from_dataset(with_rows(ctx.dataset,TimeSlots=ctx.dataset.tables['TimeSlots'][:1]))
        r=repair_conflicts(c,ctx,Random(11))
        self.assertEqual(r.report.hard_after,0)
        changed=[sid for sid in c.sections if c.sections[sid]!=r.chromosome.sections[sid]]
        self.assertEqual(len(changed),1)
        self.assertEqual(r.chromosome.sections[changed[0]].meeting_slots,c.sections[changed[0]].meeting_slots)
        self.assertNotEqual(r.chromosome.sections[changed[0]].room_id,c.sections[changed[0]].room_id)

    def test_moving_other_section_is_better(self):
        d=sample(3)
        times=(Slot('A','Saturday','T1',480,570,True),Slot('B','Saturday','T2',525,615,True),
               Slot('C','Saturday','T3',600,690,True),Slot('D','Monday','T1',480,570,True))
        d=with_rows(d,TimeSlots=times);ctx=EvaluationContext.from_dataset(d);c=scheduled(d)
        for sid in ('C2-01','C3-01'):c.replace_section(replace(c.sections[sid],room_id='R1'))
        r=repair_conflicts(c,ctx,Random(11))
        self.assertEqual(r.report.hard_before,2);self.assertEqual(r.report.hard_after,0)
        self.assertEqual(r.chromosome.sections['C1-01'],c.sections['C1-01'])
        self.assertNotEqual(r.chromosome.sections['C2-01'],c.sections['C2-01'])

    def test_no_valid_destination_uses_no_candidate_fitness(self):
        c,ctx=conflict_fixture()
        d=with_rows(ctx.dataset,TimeSlots=ctx.dataset.tables['TimeSlots'][:1],Classrooms=ctx.dataset.tables['Classrooms'][:1])
        ctx=EvaluationContext.from_dataset(d);base=evaluate_snapshot(c,ctx)
        with patch('scheduler.repair.evaluate_fitness',wraps=evaluate_fitness) as score:
            r=repair_conflicts(c,ctx,Random(11),evaluation=base)
        self.assertEqual(r.report.status,'unchanged');score.assert_not_called()
        self.assertEqual(r.report.relocation_evaluations,0)
        self.assertEqual(chromosome_fingerprint(r.chromosome),base.fingerprint)

    def test_duration_duplicate_and_disallowed_slots_are_filtered(self):
        c,ctx=conflict_fixture(2)
        times=ctx.dataset.tables['TimeSlots']+(Slot('SHORT','Tuesday','S',480,555,True),Slot('NO','Tuesday','N',600,690,False))
        ctx=EvaluationContext.from_dataset(with_rows(ctx.dataset,TimeSlots=times))
        index=RelocationIndex(_State(c,ctx))
        for key in [('Tuesday','S'),('Tuesday','N'),('Monday','T1')]:
            self.assertFalse(index.admissible(MeetingRelocation('C1-01',0,key,'R1')))

    def test_incompatible_or_small_room_is_rejected(self):
        c,ctx=conflict_fixture();rooms=tuple(replace(r,capacity=7) if r.room_id=='R2' else r for r in ctx.dataset.tables['Classrooms'])
        ctx=EvaluationContext.from_dataset(with_rows(ctx.dataset,Classrooms=rooms))
        self.assertFalse(RelocationIndex(_State(c,ctx)).admissible(MeetingRelocation('C1-01',0,('Saturday','T2'),'R2')))

    def test_determinism_mapping_order_and_global_rng(self):
        c,ctx=conflict_fixture();before=random.getstate()
        a=repair_conflicts(c,ctx,Random(19));b=repair_conflicts(c,ctx,Random(19))
        self.assertEqual(chromosome_fingerprint(a.chromosome),chromosome_fingerprint(b.chromosome))
        self.assertEqual(a.report.evaluations,b.report.evaluations)
        self.assertEqual(random.getstate(),before)
        from scheduler.chromosome import Chromosome
        reversed_c=Chromosome(dict(reversed(list(c.sections.items()))),dict(reversed(list(c.enrollments.items()))))
        self.assertEqual(chromosome_fingerprint(repair_conflicts(reversed_c,ctx,Random(19)).chromosome),chromosome_fingerprint(a.chromosome))

    def test_rejected_canonical_candidates_cannot_mutate_parent_or_increase_hard(self):
        c,ctx=conflict_fixture();baseline=evaluate_snapshot(c,ctx)
        counts=dict(baseline.fitness.hard.counts_by_type);counts['H04_ROOM_OVERLAP']=100
        worse=replace(baseline.fitness,hard=replace(baseline.fitness.hard,counts_by_type=counts))
        for fitness in (baseline.fitness,worse):
            with patch('scheduler.repair.evaluate_fitness',return_value=fitness):
                r=repair_conflicts(c,ctx,Random(11),evaluation=baseline)
            self.assertEqual(r.report.status,'unchanged');self.assertEqual(chromosome_fingerprint(c),baseline.fingerprint)
            self.assertEqual(chromosome_fingerprint(r.chromosome),baseline.fingerprint)
            self.assertGreater(r.report.relocation_evaluations,0)

    def test_exact_attempt_evaluation_and_local_caps(self):
        c,ctx=conflict_fixture()
        for attempts,calls,local in [(0,50,5),(3,50,5),(100,0,5),(100,1,1),(100,50,0)]:
            settings=RepairSettings(max_candidate_attempts=attempts,max_candidate_evaluations=calls,max_local_candidates=local)
            r=repair_conflicts(c,ctx,Random(11),settings,evaluation=evaluate_snapshot(c,ctx))
            self.assertLessEqual(r.report.attempts,attempts);self.assertLessEqual(r.report.evaluations.candidates,calls)
            self.assertTrue(all(n<=local for n in r.report.candidates_per_conflict))

    def test_both_endpoints_are_considered_within_budget(self):
        c,ctx=conflict_fixture();v=evaluate_fitness(c,ctx).hard.violations[0]
        proposals=list(relocation_proposals(_State(c,ctx),v))
        self.assertEqual({t.section_id for t in proposals[:2]},{'C1-01','C2-01'})

    def test_evolution_loop_accepts_extended_settings_and_report(self):
        c,ctx=conflict_fixture()
        result=run_evolution(ctx,EvolutionSettings(population_size=6,elite_count=1,max_generations=2,random_seed=11))
        self.assertEqual(len(result.generations),3)
        self.assertTrue(all(not validate_structure(i.chromosome,ctx) for i in result.population))


class ActualRegressionTests(unittest.TestCase):
    def test_real_dataset_b_hard4_snapshot(self):
        path=ROOT/'data/Dataset_B_Constructive_seed20261103.xlsx'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),'6b90c1c6784cbb81527dfc196e1a5e4347cd56ff988f6ce703a1452093576391')
        ctx=EvaluationContext.from_dataset(load_dataset(path))
        fixture=ROOT/'tests/fixtures/phase8_dataset_b_hard4.json'
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),'2b824515e073d78967605f4db7a2cddead50db3263fb0b4aeac87acae380b893')
        payload=json.loads(fixture.read_text(encoding='utf-8'))
        c=restore_solution(payload,ctx);before=evaluate_snapshot(c,ctx)
        self.assertEqual(before.fingerprint,'83711cb7b56c735009c8278c8a6f57c77661242bc66c7ae5ce27a3878d8520a4')
        self.assertEqual(before.fitness.hard.total_penalty,4)
        self.assertEqual(before.fitness.hard.counts_by_type['H04_ROOM_OVERLAP'],2)
        self.assertEqual(before.fitness.hard.counts_by_type['H05_STUDENT_UNAVAILABLE'],2)
        baseline_fp=chromosome_fingerprint(c)
        r=repair_conflicts(c,ctx,Random(11),evaluation=before)
        self.assertLessEqual(r.evaluation.fitness.hard.total_penalty,4)
        self.assertEqual(chromosome_fingerprint(c),baseline_fp)
        self.assertEqual(r.evaluation.fitness,evaluate_fitness(r.chromosome,ctx))
        self.assertLessEqual(r.report.evaluations.candidates,50);self.assertLessEqual(r.report.attempts,100)
        self.assertLessEqual(r.report.relocation_index_builds,r.report.relocation_accepted+1)
