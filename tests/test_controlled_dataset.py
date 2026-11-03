"""Constructive witness, Excel roundtrip and explicit separation from GA input."""
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

from scheduler import load_dataset, DatasetError
from scheduler.chromosome import Chromosome, validate_structure
from scheduler.controlled_dataset import (DEFAULT_SEED, GenerationError, assert_feasible,
    construct_faculty, create_dataset_b, workbook_tables)
from scheduler.dataset import MODELS
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution
from scheduler.evolution_reporting import restore_solution
from scheduler.initialization import chromosome_fingerprint, initialize_population
from test_constraints import sample, with_rows

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_dataset_comparison as comparison

BOOK = ROOT / 'data/Dataset_B_Constructive_seed20261103.xlsx'
CERTIFICATE = ROOT / 'data/Dataset_B_Constructive_seed20261103_certificate.json'


class ControlledDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = construct_faculty()
        cls.context = EvaluationContext.from_dataset(cls.generated.dataset)
        (ROOT / 'outputs').mkdir(exist_ok=True)

    def temp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / 'outputs')
        self.assertTrue(Path(temp.name).resolve().is_relative_to((ROOT/'outputs').resolve()))
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_exact_faculty_size_schema_and_unique_requests(self):
        d = self.generated.dataset
        self.assertEqual(set(d.tables), set(MODELS))
        self.assertEqual(len(d.tables), 14)
        self.assertEqual(len(d.tables['Faculties']), 1)
        self.assertEqual(len(d.tables['Majors']), 4)
        self.assertEqual(len(d.students), 200)
        self.assertEqual(Counter(s.major_id for s in d.students.values()), {m: 50 for m in ('CE','EE','ME','CIV')})
        self.assertEqual(len(self.context.requests), 1600)
        self.assertEqual(len(d.tables['StudentRequests']), 1600)

    def test_hard_zero_before_export(self):
        hard = assert_feasible(self.generated.witness, self.context)
        self.assertEqual(validate_structure(self.generated.witness, self.context), ())
        self.assertEqual(hard.total_penalty, 0)
        self.assertTrue(all(value == 0 for value in hard.counts_by_type.values()))
        self.assertEqual(hard.violations, ())

    def test_seed_reproducible(self):
        a, b = construct_faculty(42), construct_faculty(42)
        self.assertEqual(a.dataset.tables, b.dataset.tables)
        self.assertEqual(chromosome_fingerprint(a.witness), chromosome_fingerprint(b.witness))
        self.assertEqual(workbook_tables(a.dataset), workbook_tables(b.dataset))

    def test_distinct_seed_changes_data_and_witness(self):
        a, b = construct_faculty(11), construct_faculty(22)
        self.assertNotEqual(a.dataset.tables, b.dataset.tables)
        self.assertNotEqual(chromosome_fingerprint(a.witness), chromosome_fingerprint(b.witness))

    def test_multiple_seed_family_is_constructively_feasible(self):
        for seed in (0, 1, 11, 22, 33, 42):
            g = construct_faculty(seed)
            self.assertEqual(assert_feasible(g.witness, EvaluationContext.from_dataset(g.dataset)).total_penalty, 0)

    def test_global_rng_and_input_containers_not_changed(self):
        state = random.getstate()
        before = repr(self.generated.dataset.tables), chromosome_fingerprint(self.generated.witness)
        construct_faculty(11)
        self.assertEqual(random.getstate(), state)
        self.assertEqual(before, (repr(self.generated.dataset.tables), chromosome_fingerprint(self.generated.witness)))

    def test_groups_sizes_capacity_and_stable_scaffold(self):
        w = self.generated.witness
        self.assertEqual(len(w.sections), 192)
        self.assertEqual(sum(s.active for s in w.sections.values()), 64)
        counts = Counter()
        for sid, s in w.sections.items():
            if s.active:
                counts[s.course_id] += 1
                self.assertEqual(w.section_sizes()[sid], 25)
                self.assertLessEqual(25, self.context.effective_capacities[(s.course_id, s.room_id)])
            else:
                self.assertIsNone(s.professor_id)
                self.assertIsNone(s.room_id)
                self.assertEqual(s.meeting_slots, ())
        self.assertEqual(set(counts.values()), {2})

    def test_credit_and_teaching_hours_bounds(self):
        hard = assert_feasible(self.generated.witness, self.context)
        self.assertEqual(set(hard.metrics.student_credits.values()), {18})
        self.assertEqual(set(hard.metrics.professor_minutes.values()), {540})
        self.assertTrue(all(s.min_credits == 15 and s.max_credits == 20 for s in self.context.students.values()))
        self.assertEqual(hard.metrics.unassigned_requests, ())

    def test_real_shared_professors_and_room_competition(self):
        self.assertEqual({sum(cid == c for _, c in self.context.qualified) for cid in self.context.courses}, {6})
        rooms = defaultdict(set)
        for s in self.generated.witness.sections.values():
            if s.active:
                rooms[s.room_id].add(self.context.courses[s.course_id].major_id)
        self.assertEqual(len(rooms), 6)
        self.assertTrue(all(len(majors) >= 2 for majors in rooms.values()))

    def test_unavailability_never_blocks_witness(self):
        occupied = defaultdict(set)
        for key, sid in self.generated.witness.enrollments.items():
            occupied[('Student', key[0])].update(self.generated.witness.sections[sid].meeting_slots)
        for s in self.generated.witness.sections.values():
            if s.active:
                occupied[('Professor', s.professor_id)].update(s.meeting_slots)
        for role in ('Student', 'Professor'):
            for row in self.generated.dataset.tables[role+'Availability']:
                if row.status == 'Unavailable':
                    self.assertNotIn((row.day, row.time_slot), occupied[(role, row.person_id)])

    def test_input_workbook_has_no_solution_genes_or_extra_sheets(self):
        tables = workbook_tables(self.generated.dataset)
        self.assertEqual(set(tables), set(MODELS))
        headers = set(value for matrix in tables.values() for value in matrix[0])
        self.assertFalse({'SectionID', 'MeetingSlots', 'Enrollment', 'Witness', 'Solution'} & headers)
        self.assertEqual(tables['PassedCourses'], [['StudentID', 'CourseID']])

    def test_checked_in_excel_load_and_certificate_hard_zero(self):
        d = load_dataset(BOOK, include_feasibility_diagnostics=True)
        context = EvaluationContext.from_dataset(d)
        certificate = json.loads(CERTIFICATE.read_text(encoding='utf-8'))
        self.assertEqual(certificate['dataset_sha256'], d.sha256)
        self.assertEqual(certificate['generation_seed'], DEFAULT_SEED)
        self.assertEqual(d.tables, self.generated.dataset.tables)
        self.assertEqual(d.warnings, ())
        w = restore_solution(certificate['solution'], context)
        self.assertEqual(assert_feasible(w, context).total_penalty, 0)
        self.assertEqual(certificate['hard_before_export'], certificate['hard_after_reload'])
        self.assertTrue(all(v == 0 for v in certificate['hard_after_reload'].values()))

    @unittest.skipUnless(Path(os.environ.get('SCHEDULER_NODE', Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')).is_file(), 'optional bundled artifact-tool export runtime unavailable')
    def test_excel_generation_roundtrip_and_reproducible_content(self):
        # Both exports use the same bundled writer as the delivered workbook.
        output = self.temp()
        a, ac = create_dataset_b(output / 'a')
        b, bc = create_dataset_b(output / 'b')
        da, db = load_dataset(a), load_dataset(b)
        self.assertEqual(da.tables, db.tables)
        ca, cb = (json.loads(p.read_text(encoding='utf-8')) for p in (ac, bc))
        self.assertEqual(ca['solution'], cb['solution'])
        self.assertEqual(ca['hard_penalty'], 0)
        self.assertEqual(cb['hard_penalty'], 0)

    def test_bad_witness_never_published_as_feasible(self):
        output = self.temp()
        g = self.generated
        sections = dict(g.witness.sections)
        sid = next(sid for sid, s in sections.items() if s.active)
        wrong = next(p for p in self.context.professors if (p, sections[sid].course_id) not in self.context.qualified)
        sections[sid] = replace(sections[sid], professor_id=wrong)
        bad = replace(g, witness=Chromosome(sections, g.witness.enrollments))
        with patch('scheduler.controlled_dataset.construct_faculty', return_value=bad), self.assertRaises(GenerationError):
            create_dataset_b(output, exporter=lambda *args, **kwargs: self.fail('writer called for bad witness'))
        self.assertEqual(list(output.iterdir()), [])

    def test_corrupt_export_never_published(self):
        output = self.temp()
        def corrupt(tables, path, *args, **kwargs):
            path.write_bytes(b'not an xlsx')
        with self.assertRaises((DatasetError, GenerationError)):
            create_dataset_b(output, exporter=corrupt)
        self.assertEqual(list(output.iterdir()), [])

    def test_existing_workbook_never_overwritten(self):
        output = self.temp()
        path = output / f'Dataset_B_Constructive_seed{DEFAULT_SEED}.xlsx'
        path.write_bytes(b'keep this file')
        with self.assertRaises(FileExistsError):
            create_dataset_b(output)
        self.assertEqual(path.read_bytes(), b'keep this file')

    def test_original_dataset_sha_unchanged(self):
        self.assertEqual(hashlib.sha256((ROOT/'data/UniversityDataset_Corrected.xlsx').read_bytes()).hexdigest(),
                         'e5fc5786323ef43c8bb5c4ea0d5beea1e555a86b1b566da42f070e269526f664')

    def test_certificate_is_never_read_by_ga(self):
        d = load_dataset(BOOK)
        context = EvaluationContext.from_dataset(d)
        settings = EvolutionSettings(population_size=4, elite_count=1, max_generations=0, random_seed=11)
        real_read = Path.read_text
        def refuse_certificate(path, *args, **kwargs):
            if path.resolve() == CERTIFICATE.resolve():
                self.fail('certificate leaked into GA input')
            return real_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', new=refuse_certificate), \
             patch('scheduler.controlled_dataset.construct_faculty', side_effect=AssertionError('generator invoked by GA')):
            result, observation = comparison.observed_run(context, settings)
        witness_hash = chromosome_fingerprint(self.generated.witness)
        self.assertTrue(all(i.fingerprint != witness_hash for i in result.population))
        self.assertEqual(observation['total_calls'], 4)
        expected = initialize_population(context, 4, 11)
        self.assertEqual([i.fingerprint for i in result.population], [i.fingerprint for i in expected.individuals])

    def test_observation_does_not_change_engine_results_or_functions(self):
        context = EvaluationContext.from_dataset(sample(2, 16))
        settings = EvolutionSettings(population_size=4, elite_count=1, max_generations=2, random_seed=11)
        baseline = run_evolution(context, settings)
        before_hashes = comparison.engine_hashes()
        observed, telemetry = comparison.observed_run(context, settings)
        self.assertEqual([i.fingerprint for i in baseline.population], [i.fingerprint for i in observed.population])
        self.assertEqual(baseline.evaluations, observed.evaluations)
        self.assertEqual(comparison.engine_hashes(), before_hashes)
        self.assertEqual(telemetry['total_calls'], observed.evaluations.total)
        import scheduler.initialization as initialization
        from scheduler.fitness import evaluate_fitness
        self.assertIs(initialization.evaluate_fitness, evaluate_fitness)

    def test_first_feasible_observation_exact_call_or_none(self):
        settings = EvolutionSettings(population_size=4, elite_count=1, max_generations=0)
        context = EvaluationContext.from_dataset(sample())
        result, telemetry = comparison.observed_run(context, settings)
        self.assertTrue(result.feasible_found)
        self.assertEqual(telemetry['first_feasible']['fitness_evaluations'], 1)
        self.assertEqual(telemetry['first_feasible']['phase'], 'initialization')
        self.assertGreaterEqual(telemetry['first_feasible']['elapsed_seconds_including_initialization'], 0)
        d = sample(demand=7)
        d = with_rows(d, Students=tuple(replace(s, min_credits=3) for s in d.tables['Students']))
        result, telemetry = comparison.observed_run(EvaluationContext.from_dataset(d), settings)
        self.assertFalse(result.feasible_found)
        self.assertIsNone(telemetry['first_feasible'])

    def test_seed_type_rejected(self):
        for value in (True, None, 1.5, '11'):
            with self.assertRaises(ValueError):
                construct_faculty(value)

    def test_pinned_engine_uses_approved_git_source(self):
        import subprocess
        from run_pinned_dataset_comparison import prepare_engine, DEFAULT_COMMIT
        target = prepare_engine()
        for name in comparison.PROTECTED_MODULES:
            expected = subprocess.check_output(['git', 'show', DEFAULT_COMMIT+':src/scheduler/'+name+'.py'], cwd=ROOT)
            self.assertEqual((target/f'src/scheduler/{name}.py').read_bytes(), expected)


if __name__ == '__main__':
    unittest.main()
