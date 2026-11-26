"""Construct a synthetic faculty from a feasible witness, never used by GA operators."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
from random import Random
import shutil
import subprocess

from . import domain as D
from .chromosome import Chromosome, SectionGene, build_empty_chromosome, validate_structure
from .constraints import evaluate_hard_constraints
from .dataset import DAYS, MODELS, header_name, load_dataset
from .evaluation_context import EvaluationContext
from .initialization import chromosome_fingerprint

VERSION = 'constructive-faculty-v1'
DEFAULT_SEED = 20261103


class GenerationError(ValueError):
    pass


@dataclass(frozen=True)
class ConstructedFaculty:
    seed: int
    dataset: D.Dataset
    witness: Chromosome


def assert_feasible(chromosome, context):
    issues = validate_structure(chromosome, context)
    if issues:
        raise GenerationError('constructive witness has structural errors: ' + str(issues))
    result = evaluate_hard_constraints(chromosome, context)
    if result.total_penalty != 0 or any(result.counts_by_type.values()) or result.violations:
        raise GenerationError(f'constructive witness is NOT feasible: {dict(result.counts_by_type)}')
    return result


def construct_faculty(seed: int = DEFAULT_SEED) -> ConstructedFaculty:
    """Fixed benchmark family: 4 majors x 50 students; seed randomizes identities/calendar.

    Eight cohorts each occupy 12 cyclic positions offset by three. Cohorts i and
    i+4 have disjoint 12-position windows in a 25-position cycle, so four shared
    theory rooms suffice. Lab positions 10 and 11 do not overlap across cohorts.
    Professor assignments are constructed before enrollment/availability rows.
    """
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    rng = Random(seed)
    majors = ('CE', 'EE', 'ME', 'CIV')
    times = (480, 600, 810, 930, 1050)
    slots = tuple(D.Slot(f'{day}-T{i}', day, f'T{i}', start, start + 90, True)
                  for day in DAYS for i, start in enumerate(times, 1))
    calendar = rng.sample([(s.day, s.time_slot) for s in slots], 25)
    professors = rng.sample([f'B-P{i:02d}' for i in range(1, 17)], 16)
    rooms = rng.sample([f'B-R{i:02d}' for i in range(1, 5)], 4)
    labs = rng.sample(['B-L01', 'B-L02'], 2)
    tables = {name: [] for name in MODELS}
    tables['Faculties'] = [D.Faculty('B-F01', 'Constructive Engineering Faculty')]
    tables['Majors'] = [D.Major(m, 'B-F01', f'{m} Engineering') for m in majors]
    tables['TimeSlots'] = list(slots)
    tables['Classrooms'] = ([D.Room(r, 'B-F01', 'Theory', 30, 'Board and projector') for r in rooms] +
                            [D.Room(r, 'B-F01', 'Lab', 28, 'Shared engineering benches') for r in labs])
    for m, major in enumerate(majors):
        for j in range(4):
            pid = professors[4 * m + j]
            tables['Professors'].append(D.Professor(pid, major, f'Professor {pid}', 6.0, 12.0))
    positions = ((0, 1), (2, 3), (4, 5), (6, 7), (8,), (9,), (10,), (11,))
    credits = (3, 3, 3, 3, 2, 2, 1, 1)
    planned = {}
    cohort_sections = {}
    # First create all course groups, room/professor assignments and timetable.
    for m, major in enumerate(majors):
        for j in range(8):
            cid = f'B-{major}-{j+1:02d}'
            lab = j >= 6
            tables['Courses'].append(D.Course(cid, major, f'{major} Course {j+1}', credits[j], 1,
                'Lab' if lab else 'Theory', len(positions[j]), 90, 'Lab' if lab else 'Theory', 8, 30, rng.randint(2, 5)))
            qualified = set(professors[4*m:4*m+4]) | {professors[(4*((m+1)%4)+j%4)], professors[(4*((m+1)%4)+(j+1)%4)]}
            for pid in sorted(professors):
                tables['ProfessorCourses'].append(D.Qualification(pid, cid, pid in qualified, rng.randint(1, 10)))
            for group in range(2):
                cohort = 2*m + group
                sid = f'{cid}-{group+1:02d}'
                meeting_slots = tuple(calendar[(3 * cohort + k) % 25] for k in positions[j])
                planned[sid] = SectionGene(sid, cid, True, professors[4*m + j%4],
                    labs[(cohort+j)%2] if lab else rooms[cohort%4], meeting_slots)
                cohort_sections[(m, group, j)] = sid
    # Then allocate actual students to the pre-existing compatible groups.
    enrollments = {}
    student_slots = {}
    for m, major in enumerate(majors):
        identifiers = rng.sample([f'B-S{50*m+i:03d}' for i in range(1, 51)], 50)
        for ordinal, student_id in enumerate(identifiers):
            group = ordinal // 25
            tables['Students'].append(D.Student(student_id, major, 1, 15, 20))
            occupied = set()
            for j in range(8):
                sid = cohort_sections[(m, group, j)]
                cid = planned[sid].course_id
                tables['StudentRequests'].append(D.Request(student_id, cid, rng.randint(1, 10)))
                enrollments[(student_id, cid)] = sid
                occupied.update(planned[sid].meeting_slots)
            student_slots[student_id] = occupied
    professor_slots = {pid: set() for pid in professors}
    for section in planned.values():
        professor_slots[section.professor_id].update(section.meeting_slots)
    # Add constraints only from the complement of the already constructed plan.
    all_slots = set(calendar)
    for role, occupied_by_person in (('Student', student_slots), ('Professor', professor_slots)):
        for person_id, occupied in sorted(occupied_by_person.items()):
            forbidden = rng.sample(sorted(all_slots - occupied), 2)
            preferred = rng.sample(sorted(occupied), 2)
            for day, slot in forbidden:
                tables[role+'Availability'].append(D.Availability(person_id, day, slot, 'Unavailable', None))
            for day, slot in preferred:
                tables[role+'Availability'].append(D.Availability(person_id, day, slot, 'Preferred', rng.randint(1, 10)))
            prefs = [('PreferredTeachingDays', rng.randint(2, 4)), ('MinimizeGaps', 30), ('PreferredTimeSlots', None),
                     ('SpreadDifficultCourses', 1) if role == 'Student' else ('PreferredCourses', None)]
            for kind, target in prefs:
                tables[role+'Preferences'].append(D.Preference(person_id, kind, target, rng.randint(0, 10)))
    dataset = D.Dataset(tables, 'constructed-in-memory', ())
    empty = build_empty_chromosome(dataset)
    witness = Chromosome({**empty.sections, **planned}, enrollments)
    assert_feasible(witness, EvaluationContext.from_dataset(dataset))
    return ConstructedFaculty(seed, dataset, witness)


def workbook_tables(dataset):
    result = {}
    for name, model in MODELS.items():
        columns = fields(model)
        rows = [[header_name(f.name, name) for f in columns]]
        for record in dataset.tables[name]:
            values = []
            for f in columns:
                value = getattr(record, f.name)
                if f.name in ('start_time', 'end_time'):
                    value = f'{value//60:02d}:{value%60:02d}'  # loader explicitly expects HH:MM text
                values.append(value)
            rows.append(values)
        result[name] = rows
    return result


def witness_payload(chromosome, context):
    return {'schema_version': 'scheduler-solution-v1', 'context_id': list(context.context_id),
        'fingerprint': chromosome_fingerprint(chromosome),
        'sections': [asdict(s) for _, s in sorted(chromosome.sections.items())],
        'enrollments': [{'student_id': key[0], 'course_id': key[1], 'section_id': sid}
                        for key, sid in sorted(chromosome.enrollments.items())]}


def export_with_artifact(tables, path, scratch, *, preview=False):
    """Author through the desktop's bundled artifact-tool; no repo-local packages."""
    import os
    bundle = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = Path(os.environ.get('SCHEDULER_NODE', bundle / 'bin/node.exe'))
    modules = Path(os.environ.get('SCHEDULER_ARTIFACT_NODE_MODULES', bundle / 'node_modules'))
    if not node.is_file() or not (modules / '@oai/artifact-tool').exists():
        raise GenerationError('bundled artifact-tool runtime unavailable; set SCHEDULER_NODE and SCHEDULER_ARTIFACT_NODE_MODULES')
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    junction = scratch / 'node_modules'
    if not junction.exists():
        # Windows junction target is a read-only dependency directory; never modify it.
        if os.name == 'nt':
            process = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(modules)], capture_output=True, text=True)
            if process.returncode:
                raise GenerationError('cannot link bundled node_modules: ' + process.stderr)
        else:
            junction.symlink_to(modules, target_is_directory=True)
    builder = Path(__file__).resolve().parents[2] / 'scripts/export_controlled_workbook.mjs'
    shutil.copyfile(builder, scratch / builder.name)
    payload = scratch / 'tables.json'
    payload.write_text(json.dumps(tables, ensure_ascii=False), encoding='utf-8')
    command = [str(node), str(scratch / builder.name), str(payload), str(path.resolve())]
    if preview:
        command.append(str(scratch / 'previews'))
    process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if process.returncode:
        raise GenerationError('artifact-tool export failed: ' + process.stderr[-3000:])


def create_dataset_b(output_dir, seed=DEFAULT_SEED, *, preview=False, exporter=export_with_artifact):
    """Publish only after in-memory AND reloaded feasibility checks succeed."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f'Dataset_B_Constructive_seed{seed}'
    workbook = output_dir / (stem + '.xlsx')
    certificate = output_dir / (stem + '_certificate.json')
    if workbook.exists() or certificate.exists():
        raise FileExistsError('refusing to overwrite an existing dataset/certificate; use a separate directory')
    generated = construct_faculty(seed)
    before = assert_feasible(generated.witness, EvaluationContext.from_dataset(generated.dataset))
    # Keep authoring scratch in outputs, never inside the GA input folder.
    root = Path(__file__).resolve().parents[2]
    scratch = root / 'outputs' / f'controlled_dataset_seed{seed}'
    staged = scratch / (stem + '.xlsx')
    scratch.mkdir(parents=True, exist_ok=True)
    exporter(workbook_tables(generated.dataset), staged, scratch, preview=preview)
    reloaded = load_dataset(staged)
    context = EvaluationContext.from_dataset(reloaded)
    if reloaded.tables != generated.dataset.tables:
        raise GenerationError('XLSX roundtrip changed dataset rows')
    after = assert_feasible(generated.witness, context)
    document = {'schema_version': 'constructive-feasibility-certificate-v1', 'generator_version': VERSION,
        'generation_seed': seed, 'dataset_sha256': reloaded.sha256, 'policy_version': context.policy.version,
        'hard_before_export': dict(before.counts_by_type), 'hard_after_reload': dict(after.counts_by_type),
        'hard_penalty': after.total_penalty, 'structure_issues': [],
        'scope': 'synthetic witness only; not an input to Initialization or GA',
        'solution': witness_payload(generated.witness, context)}
    staged_certificate = scratch / (stem + '_certificate.json')
    staged_certificate.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Exclusive publication avoids silently replacing the old workbook or any prior B.
    with workbook.open('xb') as target:
        target.write(staged.read_bytes())
    try:
        with certificate.open('x', encoding='utf-8') as target:
            target.write(staged_certificate.read_text(encoding='utf-8'))
    except Exception:
        workbook.unlink()  # only the new, explicitly named file created above
        raise
    return workbook, certificate
