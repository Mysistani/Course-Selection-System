"""Dataset C generation/validation only. No search-stage module imports this file."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from random import Random

from . import domain as D
from .chromosome import Chromosome, SectionGene, build_empty_chromosome
from .controlled_dataset import (GenerationError, assert_feasible, export_with_artifact,
                                 workbook_tables, witness_payload)
from .dataset import DAYS, MODELS, load_dataset
from .evaluation_context import EvaluationContext
from .university_catalog import FACULTIES, PROGRAMS, room_type

VERSION = 'constructive-university-c-v1'
DEFAULT_SEED = 20261104


@dataclass(frozen=True)
class ConstructedUniversity:
    seed: int
    dataset: D.Dataset
    witness: Chromosome
    profiles: tuple[dict, ...]  # Audit-only history absent from the 14-sheet contract.


def canonical_content(dataset):
    return json.dumps(workbook_tables(dataset), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _rooms(rng):
    # Organizational ownership does not restrict shared booking.
    practical = {
        'ENG': ['ComputingLab']*2 + ['ElectronicsLab']*3 + ['MechanicalWorkshop']*2 + ['CivilLab']*2,
        'SCI': ['ComputingLab'] + ['PhysicsLab']*3 + ['ChemistryLab']*3,
        'MGT': ['ComputingLab']*2 + ['BusinessWorkshop'],
        'HUM': ['PsychologyLab'],
    }
    rooms = []
    for faculty, _, theory, count in FACULTIES:
        for i in range(theory):
            rooms.append(D.Room(f'C-{faculty}-T{i+1:02}', f'C-{faculty}', 'Theory',
                                rng.choice((30, 35, 40, 45)), 'Board and projector'))
        types = practical[faculty]
        # Law uses a specialist workshop. One MGT space is shared with Humanities.
        if faculty == 'MGT':
            types = ['ComputingLab', 'BusinessWorkshop', 'LegalWorkshop']
        for i, kind in enumerate(types):
            rooms.append(D.Room(f'C-{faculty}-P{i+1:02}', f'C-{faculty}', kind,
                                rng.choice((16, 18, 20)), kind + ' equipment'))
        if len(types) != count:
            raise GenerationError('room allocation configuration mismatch')
    return rooms


def construct_university(seed=DEFAULT_SEED):
    if type(seed) is not int:
        raise ValueError('dataset seed must be an integer')
    rng = Random(seed)
    tables = {sheet: [] for sheet in MODELS}
    tables['Faculties'] = [D.Faculty('C-'+code, name) for code, name, _, _ in FACULTIES]
    tables['Classrooms'] = _rooms(rng)
    times = (480, 600, 810, 930, 1050)
    tables['TimeSlots'] = [D.Slot(f'{day}-T{i}', day, f'T{i}', t, t+90, True)
                           for day in DAYS for i, t in enumerate(times, 1)]
    course_by_program, professors_by_program, profiles = {}, {}, []
    qualifications = defaultdict(list)
    for program in PROGRAMS:
        major = 'C-'+program.code
        tables['Majors'].append(D.Major(major, 'C-'+program.faculty, program.name))
        pids = [f'{major}-P{i+1:02}' for i in range(program.professors)]
        professors_by_program[program.code] = pids
        tables['Professors'].extend(D.Professor(pid, major, f'استاد {program.name} {i+1}', 3., 12.)
                                    for i, pid in enumerate(pids))
        courses = []
        for j, title in enumerate(program.titles):
            kind = room_type(program, j)
            practical = kind != 'Theory'
            credits = 1 if practical else (4 if j == 2 else (2 if j % 5 == 0 else 3))
            course = D.Course(f'{major}-{j+1:02}', major, title, credits,
                min(8, max(1, j-4)), 'Workshop' if 'Workshop' in kind else ('Lab' if practical else 'Theory'),
                1 if practical or credits == 2 else 2, 90, kind, 8,
                20 if practical else rng.choice((35, 40, 45)), 2 if practical else rng.randint(2, 5))
            courses.append(course)
            # Adjacent subjects have overlapping specialists. Last specialty has one.
            owners = {pids[(2*j) % len(pids)]}
            if j != len(program.titles)-1:
                owners.update(pids[(2*j+k) % len(pids)] for k in (1, 2))
            for pid in sorted(owners):
                qualifications[course.course_id].append(pid)
                tables['ProfessorCourses'].append(D.Qualification(pid, course.course_id, True, rng.randint(4, 10)))
        tables['Courses'].extend(courses)
        course_by_program[program.code] = courses
        # Uniform intake cohorts ensure every semester (including final-year) exists.
        for i in range(program.students):
            semester = i % 8 + 1
            student_id = f'{major}-S{i+1:03}'
            start = round((semester-1)*(len(courses)-6)/7)
            selected = list(range(start, start+6))
            draw = rng.random()
            load = 5 if draw < .15 else (7 if draw > .85 else 6)
            if load == 5:
                selected.remove(start+2)  # keep edge subjects supported by whole cohorts
            elif load == 7:
                selected.append(start-1 if start else start+6)
            past = set(range(start)) - set(selected)
            failed, delayed = [], []
            if past and rng.random() < .22:
                old = rng.choice(sorted(past))
                past.remove(old)
                failed.append(old)
                selected[selected.index(start+3)] = old
            if past and rng.random() < .12:
                old = rng.choice(sorted(past))
                past.remove(old)
                delayed.append(old)
            # Fixed benchmark load bands, chosen before construction (not relaxed
            # on a failed witness); lighter loads use the explicit 8-credit band.
            tables['Students'].append(D.Student(student_id, major, semester, 8 if load == 5 else 12, 24))
            tables['PassedCourses'].extend(D.PassedCourse(student_id, courses[j].course_id) for j in sorted(past))
            for j in sorted(selected):
                priority = 10 if j in failed or (semester >= 7 and j < start) else (9 if j < start else rng.randint(5, 8))
                tables['StudentRequests'].append(D.Request(student_id, courses[j].course_id, priority))
            profiles.append({'student_id': student_id, 'semester': semester, 'program': major,
                'passed': [courses[j].course_id for j in sorted(past)],
                'failed_retake': [courses[j].course_id for j in failed],
                'delayed_not_requested': [courses[j].course_id for j in delayed],
                'catalog_window_start': start, 'requested_count': len(selected)})

    courses = {c.course_id: c for c in tables['Courses']}
    requestors = defaultdict(list)
    student_map = {s.student_id: s for s in tables['Students']}
    for r in tables['StudentRequests']:
        requestors[r.course_id].append(r.student_id)
    rooms_by_type = defaultdict(list)
    for room in tables['Classrooms']:
        rooms_by_type[room.room_type].append(room)
    # Choose sections first; balance sizes so the last section never has <8.
    groups = []
    for cid, course in sorted(courses.items()):
        members = sorted(requestors[cid], key=lambda sid: (student_map[sid].current_semester, sid))
        compatible = rooms_by_type[course.required_room_type]
        cap = min(course.max_section_size, max(r.capacity for r in compatible))
        count = math.ceil(len(members)/cap)
        if not count or len(members)//count < 8:
            raise GenerationError(f'{cid}: cannot form nonempty sections of at least 8')
        sizes = [len(members)//count + (i < len(members)%count) for i in range(count)]
        offset = 0
        for i, size in enumerate(sizes):
            groups.append((f'{cid}-{i+1:02}', course, tuple(members[offset:offset+size])))
            offset += size

    slots = [(s.day, s.time_slot) for s in tables['TimeSlots']]
    anchors = {}
    for program in PROGRAMS:
        catalog = course_by_program[program.code]
        for _ in range(1000):
            calendar = rng.sample(slots, len(slots))
            offset, trial = 0, {}
            for course in catalog:
                keys = calendar[offset:offset+course.sessions_per_week]
                trial[course.course_id] = tuple(keys)
                offset += course.sessions_per_week
            if offset <= len(slots) and all(len({d for d, _ in keys}) == len(keys) for keys in trial.values()):
                anchors.update(trial)
                break
        else:
            raise GenerationError('bounded anchor calendar generation failed')
    student_busy, professor_busy, room_busy = defaultdict(set), defaultdict(set), defaultdict(set)
    student_reserved, professor_reserved = defaultdict(set), defaultdict(set)
    for request in tables['StudentRequests']:
        student_reserved[request.student_id].update(anchors[request.course_id])
    for cid, pids in qualifications.items():
        for pid in pids:
            professor_reserved[pid].update(anchors[cid])
    teaching = Counter()
    planned, enrollments = {}, {}
    # Most constrained sections first. Bounded constructive greedy search,
    # deliberately isolated from Initialization/Repair and never used by GA.
    groups.sort(key=lambda g: (len(qualifications[g[1].course_id]),
        sum(r.capacity >= len(g[2]) for r in rooms_by_type[g[1].required_room_type]),
        -len(g[2]), g[0]))
    for sid, course, members in groups:
        occupied = set().union(*(student_busy[s] for s in members))
        reserved = set().union(*(student_reserved[s] for s in members)) - set(anchors[course.course_id])
        pids = list(qualifications[course.course_id])
        rng.shuffle(pids)
        pids.sort(key=lambda pid: teaching[pid])
        options = list(rooms_by_type[course.required_room_type])
        rng.shuffle(options)
        chosen = None
        for pid in pids:
            if teaching[pid] + 90*course.sessions_per_week > 720:
                continue
            for room in options:
                if room.capacity < len(members):
                    continue
                future_professor = professor_reserved[pid] - set(anchors[course.course_id])
                available = [key for key in slots if key not in occupied and key not in reserved and key not in future_professor
                             and key not in professor_busy[pid] and key not in room_busy[room.room_id]]
                rng.shuffle(available)
                # Whole-program anchor subjects are disjoint, while parallel
                # sections may use another free period if a specialist is busy.
                available.sort(key=lambda key: key not in anchors[course.course_id])
                if len(available) >= course.sessions_per_week:
                    selected = []
                    # Different days preferred for a multi-meeting theory course.
                    for key in available:
                        if key[0] not in {d for d, _ in selected}:
                            selected.append(key)
                        if len(selected) == course.sessions_per_week:
                            break
                    if len(selected) != course.sessions_per_week:
                        continue
                    chosen = pid, room.room_id, tuple(selected)
                    break
            if chosen:
                break
        if chosen is None:
            raise GenerationError(f'bounded constructive scheduling failed at {sid}; no feasible label published')
        pid, rid, meetings = chosen
        planned[sid] = SectionGene(sid, course.course_id, True, pid, rid, meetings)
        professor_busy[pid].update(meetings)
        room_busy[rid].update(meetings)
        teaching[pid] += 90*len(meetings)
        for student in members:
            student_busy[student].update(meetings)
            enrollments[(student, course.course_id)] = sid

    for role, people, busy in (('Student', tables['Students'], student_busy),
                              ('Professor', tables['Professors'], professor_busy)):
        for person in people:
            person_id = person.student_id if role == 'Student' else person.professor_id
            occupied = busy[person_id]
            # One recurring free block is genuinely hard-unavailable, chosen as
            # a contiguous day/profile pattern rather than unrelated cell flips.
            free = [key for key in slots if key not in occupied]
            rng.shuffle(free)
            if not free:
                raise GenerationError(f'{person_id}: no flexibility for an unavailable block')
            block = free[0]
            forbidden = [key for key in free if key[1] == block[1]][:2]
            for day, time in forbidden:
                tables[role+'Availability'].append(D.Availability(person_id, day, time, 'Unavailable', None))
            morning = rng.choice((True, False))
            for day, time in slots:
                if (day, time) not in forbidden:
                    desirability = (9 if time in ('T1', 'T2') else 4) if morning else (9 if time in ('T4', 'T5') else 4)
                    tables[role+'Availability'].append(D.Availability(person_id, day, time, 'Preferred', desirability))
            for kind, target in [('PreferredTeachingDays', rng.choice((3, 4))), ('MinimizeGaps', 30),
                                  ('PreferredTimeSlots', None),
                                  ('SpreadDifficultCourses', 1) if role == 'Student' else ('PreferredCourses', None)]:
                tables[role+'Preferences'].append(D.Preference(person_id, kind, target, rng.randint(0, 10)))
    dataset = D.Dataset(tables, 'constructed-c-in-memory', ())
    scaffold = build_empty_chromosome(dataset)
    witness = Chromosome({**scaffold.sections, **planned}, enrollments)
    assert_feasible(witness, EvaluationContext.from_dataset(dataset))
    return ConstructedUniversity(seed, dataset, witness, tuple(profiles))


def quality_profile(generated, dataset=None):
    dataset = dataset or generated.dataset
    context = EvaluationContext.from_dataset(dataset)
    hard = assert_feasible(generated.witness, context)
    major_faculty = {m.major_id: m.faculty_id for m in dataset.tables['Majors']}
    requests = Counter(s for s, _ in context.requests)
    requested_credits = Counter()
    for s, cid in context.requests:
        requested_credits[s] += context.courses[cid].credits
    qualified = Counter(cid for _, cid in context.qualified)
    professor_qualified = Counter(pid for pid, _ in context.qualified)
    room_meetings = Counter()
    professor_meetings = Counter()
    sections = [s for s in generated.witness.sections.values() if s.active]
    for section in sections:
        room_meetings[section.room_id] += len(section.meeting_slots)
        professor_meetings[section.professor_id] += len(section.meeting_slots)
    return {'generator_version': VERSION, 'dataset_seed': generated.seed, 'dataset_sha256': dataset.sha256,
        'canonical_content_sha256': hashlib.sha256(canonical_content(dataset)).hexdigest(),
        'counts': {name: len(rows) for name, rows in dataset.tables.items()},
        'students_by_faculty': dict(Counter(major_faculty[s.major_id] for s in context.students.values())),
        'students_by_program': dict(Counter(s.major_id for s in context.students.values())),
        'professors_by_faculty': dict(Counter(major_faculty[p.major_id] for p in context.professors.values())),
        'students_by_semester': dict(Counter(s.current_semester for s in context.students.values())),
        'requests_per_student': dict(Counter(requests.values())),
        'requested_credits_distribution': dict(Counter(requested_credits.values())),
        'passed_counts_distribution': dict(Counter(len(p['passed']) for p in generated.profiles)),
        'failed_retake_counts_distribution': dict(Counter(len(p['failed_retake']) for p in generated.profiles)),
        'delayed_counts_distribution': dict(Counter(len(p['delayed_not_requested']) for p in generated.profiles)),
        'course_demand': dict(sorted(context.demand.items())),
        'qualified_professors_per_course': dict(sorted(qualified.items())),
        'qualified_courses_per_professor': {p: professor_qualified[p] for p in context.professors},
        'professor_unavailable_slots': dict(Counter(r.person_id for r in dataset.tables['ProfessorAvailability'] if r.status == 'Unavailable')),
        'room_types': dict(Counter(r.room_type for r in context.rooms.values())),
        'room_capacity_distribution': dict(Counter(r.capacity for r in context.rooms.values())),
        'active_sections': len(sections), 'preallocated_section_slots': len(generated.witness.sections),
        'section_size_distribution': dict(Counter(hard.metrics.section_sizes[s.section_id] for s in sections)),
        'room_utilization': {rid: {'meetings': room_meetings[rid], 'fraction_of_25_slots': room_meetings[rid]/25} for rid in context.rooms},
        'professor_utilization': {pid: {'minutes': hard.metrics.professor_minutes[pid], 'max_minutes': context.professor_max_minutes[pid]} for pid in context.professors},
        'total_requested_credits': sum(requested_credits.values()), 'total_assigned_credits': sum(hard.metrics.student_credits.values()),
        'unassigned_requests': len(hard.metrics.unassigned_requests), 'hard_penalty': hard.total_penalty,
        'hard_counts': dict(hard.counts_by_type), 'hard_violations': len(hard.violations),
        'rooms_with_no_bookings': sum(room_meetings[rid] == 0 for rid in context.rooms),
        'professors_with_no_bookings': sum(professor_meetings[pid] == 0 for pid in context.professors)}


def create_dataset_c(output_dir, seed=DEFAULT_SEED, *, preview=False, exporter=export_with_artifact):
    output = Path(output_dir).resolve()
    stem = f'Dataset_C_University_seed{seed}'
    names = [stem+'.xlsx', stem+'_certificate.json', stem+'_profiles.json', stem+'_quality.json']
    output.mkdir(parents=True, exist_ok=True)
    if any((output/name).exists() for name in names):
        raise FileExistsError('Dataset C artifacts already exist; choose a new output directory')
    generated = construct_university(seed)
    before = assert_feasible(generated.witness, EvaluationContext.from_dataset(generated.dataset))
    scratch = Path(__file__).resolve().parents[2] / 'outputs' / f'dataset_c_seed{seed}'
    scratch.mkdir(parents=True, exist_ok=True)
    staged = scratch / names[0]
    exporter(workbook_tables(generated.dataset), staged, scratch, preview=preview)
    dataset = load_dataset(staged)
    if dataset.tables != generated.dataset.tables:
        raise GenerationError('Dataset C Excel roundtrip changed canonical rows')
    context = EvaluationContext.from_dataset(dataset)
    after = assert_feasible(generated.witness, context)
    certificate = {'schema_version': 'constructive-feasibility-certificate-v1', 'generator_version': VERSION,
        'generation_seed': seed, 'dataset_sha256': dataset.sha256, 'policy_version': context.policy.version,
        'scope': 'Validation witness only. Never supply this to Initialization or GA.',
        'hard_before_export': dict(before.counts_by_type), 'hard_after_reload': dict(after.counts_by_type),
        'hard_penalty': after.total_penalty, 'structure_issues': [],
        'solution': witness_payload(generated.witness, context)}
    payloads = [staged.read_bytes(), json.dumps(certificate, ensure_ascii=False, indent=2).encode('utf-8'),
        json.dumps({'schema_version': VERSION, 'profiles': generated.profiles}, ensure_ascii=False, indent=2).encode('utf-8'),
        json.dumps(quality_profile(generated, dataset), ensure_ascii=False, indent=2).encode('utf-8')]
    published = []
    try:
        for name, payload in zip(names, payloads):
            target = output/name
            with target.open('xb') as stream:
                stream.write(payload)
            published.append(target)
    except Exception:
        for target in published:
            target.unlink()  # only files exclusively created in this invocation
        raise
    return tuple(published)
