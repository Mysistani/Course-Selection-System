"""Dataset C contracts, independent witness checks and anti-leakage tests."""
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scheduler.chromosome import Chromosome, validate_structure
from scheduler.constraints import evaluate_hard_constraints
from scheduler.controlled_dataset import GenerationError, assert_feasible, workbook_tables
from scheduler.dataset import MODELS, load_dataset
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution
from scheduler.evolution_reporting import restore_solution
from scheduler.initialization import chromosome_fingerprint, initialize_population
from scheduler.university_catalog import PROGRAMS, room_type
from scheduler.university_dataset import (DEFAULT_SEED, canonical_content, construct_university,
                                           create_dataset_c, quality_profile)

ROOT = Path(__file__).resolve().parents[1]
STEM = 'Dataset_C_University_seed20261104'
BOOK = ROOT/'data'/(STEM+'.xlsx')


class UniversityDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = construct_university()
        cls.context = EvaluationContext.from_dataset(cls.g.dataset)
        cls.hard = assert_feasible(cls.g.witness, cls.context)
        (ROOT/'outputs').mkdir(exist_ok=True)

    def test_exact_counts_and_faculty_distributions(self):
        d = self.g.dataset
        self.assertEqual({s: len(d.tables[s]) for s in ('Students','Faculties','Majors','Professors','Courses','Classrooms')},
                         {'Students':1000,'Faculties':4,'Majors':12,'Professors':104,'Courses':160,'Classrooms':80})
        faculty = {m.major_id:m.faculty_id for m in d.tables['Majors']}
        self.assertEqual(Counter(faculty[s.major_id] for s in d.tables['Students']),
                         {'C-ENG':360,'C-SCI':260,'C-MGT':230,'C-HUM':150})
        self.assertEqual(Counter(faculty[p.major_id] for p in d.tables['Professors']),
                         {'C-ENG':38,'C-SCI':27,'C-MGT':23,'C-HUM':16})
        self.assertEqual(Counter(s.major_id for s in d.tables['Students']), {'C-'+p.code:p.students for p in PROGRAMS})

    def test_exact_room_types_capacities_and_organizational_distribution(self):
        rooms = self.g.dataset.tables['Classrooms']
        self.assertEqual(sum(r.room_type=='Theory' for r in rooms),60)
        self.assertEqual(sum(r.room_type!='Theory' for r in rooms),20)
        self.assertEqual(Counter((r.faculty_id,r.room_type=='Theory') for r in rooms),
            {('C-ENG',True):21,('C-ENG',False):9,('C-SCI',True):15,('C-SCI',False):7,
             ('C-MGT',True):13,('C-MGT',False):3,('C-HUM',True):11,('C-HUM',False):1})
        for r in rooms:
            self.assertIn(r.capacity, (30,35,40,45) if r.room_type=='Theory' else (16,18,20))
        self.assertGreater(len({r.capacity for r in rooms}),3)

    def test_ids_request_keys_and_section_scaffold_unique(self):
        d=self.g.dataset
        self.assertEqual(len(d.courses),len(d.tables['Courses']))
        self.assertEqual(len(self.context.requests),len(d.tables['StudentRequests']))
        self.assertEqual(set(self.g.witness.sections),set(self.context.expected_sections))
        demand=Counter(r.course_id for r in d.tables['StudentRequests'])
        self.assertEqual(len(self.g.witness.sections),sum(n//8 for n in demand.values()))

    def test_academic_histories_requests_and_credit_units(self):
        d=self.g.dataset
        passed={(p.student_id,p.course_id) for p in d.tables['PassedCourses']}
        self.assertFalse(passed & self.context.requests)
        self.assertTrue(all(type(c.credits) is int and c.credits in (1,2,3,4) for c in d.courses.values()))
        self.assertTrue(all(d.students[r.student_id].major_id==d.courses[r.course_id].major_id for r in d.tables['StudentRequests']))
        for profile in self.g.profiles:
            sid=profile['student_id']
            self.assertEqual(set(profile['passed']),{cid for s,cid in passed if s==sid})
            self.assertTrue(all((sid,cid) in self.context.requests for cid in profile['failed_retake']))
            self.assertFalse(set(profile['passed']) & set(profile['failed_retake']))
            self.assertTrue(all(d.courses[cid].major_id==profile['program'] for key in ('passed','failed_retake','delayed_not_requested') for cid in profile[key]))
            self.assertTrue(all(int(cid.rsplit('-',1)[1])-1 < profile['catalog_window_start'] for cid in profile['passed']))
        averages={sem:sum(len(p['passed']) for p in self.g.profiles if p['semester']==sem)/sum(p['semester']==sem for p in self.g.profiles) for sem in range(1,9)}
        self.assertEqual(averages[1],0)
        self.assertGreater(averages[8],averages[4])
        self.assertTrue(any(p['failed_retake'] for p in self.g.profiles))
        self.assertTrue(any(p['delayed_not_requested'] for p in self.g.profiles))

    def test_requests_load_priority_and_total(self):
        self.assertTrue(5900<=len(self.context.requests)<=6100)
        counts=Counter(s for s,_ in self.context.requests)
        self.assertEqual(set(counts.values()),{5,6,7})
        for p in self.g.profiles:
            for cid in p['failed_retake']:
                self.assertEqual(self.context.priorities[(p['student_id'],cid)],10)
        for s,credits in self.hard.metrics.student_credits.items():
            self.assertLessEqual(self.context.students[s].min_credits,credits)
            self.assertLessEqual(credits,self.context.students[s].max_credits)

    def test_professor_two_layer_model_and_subject_alignment(self):
        d=self.g.dataset
        for q in d.tables['ProfessorCourses']:
            self.assertTrue(q.can_teach)
            self.assertEqual(d.professors[q.professor_id].major_id,d.courses[q.course_id].major_id)
            self.assertGreaterEqual(q.willingness,1)
            self.assertLessEqual(q.willingness,10)
        coverage=Counter(cid for _,cid in self.context.qualified)
        self.assertEqual(set(coverage),set(self.context.courses))
        self.assertIn(1,coverage.values())
        self.assertGreater(len(set(q.willingness for q in d.tables['ProfessorCourses'])),1)
        # PreferredCourses uses Willingness on qualification records, no invented table.
        self.assertTrue(all(key in self.context.qualified for key in self.context.willingness))

    def test_section_minimum_capacity_and_room_specialization(self):
        w=self.g.witness
        self.assertTrue(200<=sum(s.active for s in w.sections.values())<=260)
        for sid,s in w.sections.items():
            if s.active:
                c=self.context.courses[s.course_id]
                room=self.context.rooms[s.room_id]
                self.assertEqual(c.required_room_type,room.room_type)
                self.assertGreaterEqual(self.hard.metrics.section_sizes[sid],8)
                self.assertLessEqual(self.hard.metrics.section_sizes[sid],min(room.capacity,c.max_section_size))
                self.assertEqual(len(s.meeting_slots),c.sessions_per_week)
            else:
                self.assertEqual((s.professor_id,s.room_id,s.meeting_slots),(None,None,()))

    def test_independent_interval_overlap_and_unavailability_checks(self):
        groups=defaultdict(list)
        for sid,section in self.g.witness.sections.items():
            if not section.active: continue
            members=[s for (s,_),assigned in self.g.witness.enrollments.items() if assigned==sid]
            for day,slot in section.meeting_slots:
                actual=self.context.slots[(day,slot)]
                for role,pid in [('room',section.room_id),('professor',section.professor_id)]+[('student',s) for s in members]:
                    groups[(role,pid,day)].append((actual.start_time,actual.end_time,sid))
        for key,meetings in groups.items():
            ordered=sorted(meetings)
            for a,b in zip(ordered,ordered[1:]):
                self.assertLessEqual(a[1],b[0],key)
        for role,sheet in [('professor','ProfessorAvailability'),('student','StudentAvailability')]:
            for row in self.g.dataset.tables[sheet]:
                if row.status!='Unavailable': continue
                slot=self.context.slots[(row.day,row.time_slot)]
                self.assertTrue(all(min(end,slot.end_time)<=max(start,slot.start_time) for start,end,_ in groups[(role,row.person_id,row.day)]))

    def test_canonical_evaluator_hard_zero_and_all_counters(self):
        self.assertEqual(validate_structure(self.g.witness,self.context),())
        self.assertEqual(self.hard.total_penalty,0)
        self.assertFalse(self.hard.violations)
        self.assertTrue(all(v==0 for v in self.hard.counts_by_type.values()))
        self.assertFalse(self.hard.metrics.unassigned_requests)

    def test_checked_in_excel_certificate_and_audit_roundtrip(self):
        dataset=load_dataset(BOOK,include_feasibility_diagnostics=True)
        self.assertEqual(dataset.tables,self.g.dataset.tables)
        self.assertEqual(dataset.warnings,())
        context=EvaluationContext.from_dataset(dataset)
        certificate=json.loads((ROOT/'data'/(STEM+'_certificate.json')).read_text(encoding='utf-8'))
        self.assertEqual(certificate['dataset_sha256'],dataset.sha256)
        restored=restore_solution(certificate['solution'],context)
        self.assertEqual(evaluate_hard_constraints(restored,context).total_penalty,0)
        profiles=json.loads((ROOT/'data'/(STEM+'_profiles.json')).read_text(encoding='utf-8'))
        self.assertEqual(profiles['profiles'],list(self.g.profiles))
        quality=json.loads((ROOT/'data'/(STEM+'_quality.json')).read_text(encoding='utf-8'))
        self.assertEqual(quality['canonical_content_sha256'],hashlib.sha256(canonical_content(dataset)).hexdigest())

    def test_same_seed_canonical_identity_and_rng_isolation(self):
        state=random.getstate()
        other=construct_university(DEFAULT_SEED)
        self.assertEqual(canonical_content(other.dataset),canonical_content(self.g.dataset))
        self.assertEqual(other.profiles,self.g.profiles)
        self.assertEqual(chromosome_fingerprint(other.witness),chromosome_fingerprint(self.g.witness))
        self.assertEqual(random.getstate(),state)

    def test_other_seed_family_remains_feasible_and_varied(self):
        for seed in (0,1,11,22,33,42):
            other=construct_university(seed)
            self.assertNotEqual(canonical_content(other.dataset),canonical_content(self.g.dataset))
            self.assertEqual(assert_feasible(other.witness,EvaluationContext.from_dataset(other.dataset)).total_penalty,0)

    def test_pythonhashseed_does_not_change_canonical_data_or_witness(self):
        code=('from scheduler.university_dataset import *; from scheduler.initialization import chromosome_fingerprint; '
              'import hashlib; g=construct_university(); '
              'print(hashlib.sha256(canonical_content(g.dataset)).hexdigest(),chromosome_fingerprint(g.witness))')
        results=[]
        for value in ('1','999'):
            env={**os.environ,'PYTHONHASHSEED':value,'PYTHONPATH':str(ROOT/'src'),'PYTHONUTF8':'1'}
            results.append(subprocess.check_output([sys.executable,'-c',code],cwd=ROOT,env=env,text=True))
        self.assertEqual(results[0],results[1])

    def test_corrupt_export_never_publishes_any_artifact(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as directory:
            def corrupt(tables,path,*args,**kwargs): path.write_bytes(b'not a workbook')
            from scheduler.dataset import DatasetError
            with self.assertRaises(DatasetError): create_dataset_c(directory,exporter=corrupt)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_workbook_has_only_current_contract_no_solution(self):
        tables=workbook_tables(self.g.dataset)
        self.assertEqual(set(tables),set(MODELS))
        headers={h for rows in tables.values() for h in rows[0]}
        self.assertFalse(headers & {'SectionID','MeetingSlots','Witness','FailedCourses'})

    def test_invalid_witness_never_published(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as directory:
            bad=self.g.witness.clone()
            sid=next(s for s,g in bad.sections.items() if g.active)
            section=bad.sections[sid]
            wrong=next(p for p in self.context.professors if (p,section.course_id) not in self.context.qualified)
            bad.replace_section(replace(section,professor_id=wrong))
            with patch('scheduler.university_dataset.construct_university',return_value=replace(self.g,witness=bad)):
                with self.assertRaises(GenerationError):
                    create_dataset_c(directory,exporter=lambda *a,**k:self.fail('bad witness exported'))
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_overwrite_refused_and_malformed_seed(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as directory:
            p=Path(directory)/(STEM+'.xlsx')
            p.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError): create_dataset_c(directory)
            self.assertEqual(p.read_bytes(),b'preserve')
        for seed in (True,None,1.5,'11'):
            with self.assertRaises(ValueError): construct_university(seed)

    def test_witness_absent_from_search_and_all_chromosomes_evaluated(self):
        dataset=load_dataset(BOOK)
        context=EvaluationContext.from_dataset(dataset)
        original=Path.open
        def guarded(path,*args,**kwargs):
            if path.suffix=='.json' and ('certificate' in path.name or 'profiles' in path.name):
                self.fail('validation artifact read by GA')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',new=guarded), patch('scheduler.university_dataset.construct_university',side_effect=AssertionError('generator invoked by GA')):
            population=initialize_population(context,3,11)
            result=run_evolution(context,EvolutionSettings(population_size=3,elite_count=1,max_generations=1,random_seed=11))
        self.assertEqual(len(result.generations),2)
        self.assertGreater(result.evaluations.total,3)
        self.assertTrue(all(validate_structure(i.chromosome,context)==() for i in result.population))
        witness_hash=chromosome_fingerprint(self.g.witness)
        self.assertTrue(all(i.fingerprint!=witness_hash for i in population.individuals))
        self.assertTrue(all(i.evaluation.hard.context_id==context.context_id for i in result.population))

    def test_quality_report_totals_match_independent_counts(self):
        profile=quality_profile(self.g)
        self.assertEqual(profile['total_requested_credits'],sum(self.context.courses[c].credits for _,c in self.context.requests))
        self.assertEqual(profile['total_requested_credits'],profile['total_assigned_credits'])
        self.assertEqual(sum(profile['requests_per_student'].values()),1000)
        self.assertEqual(sum(profile['course_demand'].values()),len(self.context.requests))
        self.assertEqual(profile['hard_penalty'],0)


if __name__=='__main__': unittest.main()
