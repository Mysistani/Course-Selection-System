"""Pure hard-constraint evaluation. Scores and input objects are never mutated."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from types import MappingProxyType
from typing import Mapping

from .chromosome import Chromosome, RequestKey, SlotKey, StructureIssue, validate_structure
from .domain import Dataset


CONSTRAINT_TYPES = (
    'H02_DISALLOWED_SLOT', 'H03_UNQUALIFIED_PROFESSOR',
    'H04_PROFESSOR_OVERLAP', 'H04_ROOM_OVERLAP', 'H04_STUDENT_OVERLAP',
    'H05_PROFESSOR_UNAVAILABLE', 'H05_STUDENT_UNAVAILABLE', 'H06_ROOM_TYPE',
    'H07_SECTION_UNDERFLOW', 'H07_SECTION_OVERFLOW',
    'H08_CREDIT_UNDERFLOW', 'H08_CREDIT_OVERFLOW',
    'H09_TEACHING_OVERLOAD', 'H10_PASSED_COURSE',
)


class StructuralValidationError(ValueError):
    """Invalid structure has no hard score; preserve all original issues."""

    def __init__(self, issues: tuple[StructureIssue, ...]):
        self.issues = tuple(issues)
        super().__init__('Cannot evaluate a structurally invalid chromosome:\n' +
                         '\n'.join(f'{i.location}: {i.message}' for i in self.issues))


@dataclass(frozen=True)
class Violation:
    constraint_type: str
    severity: int
    section_ids: tuple[str, ...] = ()
    course_ids: tuple[str, ...] = ()
    professor_id: str | None = None
    room_id: str | None = None
    student_id: str | None = None
    day: str | None = None
    slot_keys: tuple[SlotKey, ...] = ()
    start_minute: int | None = None
    end_minute: int | None = None
    measured_value: int | None = None
    limit: int | None = None
    unit: str | None = None

    def __post_init__(self):
        if self.constraint_type not in CONSTRAINT_TYPES:
            raise ValueError('unknown hard constraint type')
        if type(self.severity) is not int or self.severity <= 0:
            raise ValueError('violation severity must be a positive integer')
        # Do not retain caller-owned mutable sequences in this public record.
        for name in ('section_ids', 'course_ids'):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, str) for value in values):
                raise ValueError(f'{name} must contain strings')
            object.__setattr__(self, name, values)
        slots = tuple(tuple(slot) for slot in self.slot_keys)
        if any(len(slot) != 2 or any(not isinstance(part, str) for part in slot) for slot in slots):
            raise ValueError('slot_keys must contain day/time string pairs')
        object.__setattr__(self, 'slot_keys', slots)


@dataclass(frozen=True)
class Diagnostic:
    kind: str
    related_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class HardMetrics:
    section_sizes: Mapping[str, int]
    section_capacities: Mapping[str, int]  # Active sections only.
    student_credits: Mapping[str, int]     # Includes students with no enrollment.
    professor_minutes: Mapping[str, int]  # Includes professors with no teaching.
    professor_minute_shortfall: Mapping[str, int]
    professor_minute_excess: Mapping[str, int]
    unassigned_requests: tuple[RequestKey, ...]


@dataclass(frozen=True)
class HardEvaluation:
    violations: tuple[Violation, ...]
    counts_by_type: Mapping[str, int]  # Sum of severity, not number of records.
    metrics: HardMetrics
    diagnostics: tuple[Diagnostic, ...]

    @property
    def total_penalty(self) -> int:
        return sum(self.counts_by_type.values())

    @property
    def is_feasible(self) -> bool:
        return self.total_penalty == 0

    @property
    def events_by_type(self) -> Mapping[str, int]:
        """Record counts, separately from weighted/raw-unit penalty counts."""
        return MappingProxyType(dict(Counter(v.constraint_type for v in self.violations)))


@dataclass(frozen=True)
class _Meeting:
    section_id: str
    course_id: str
    professor_id: str
    room_id: str
    slot_key: SlotKey
    start: int
    end: int

    @property
    def day(self) -> str:
        return self.slot_key[0]


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def evaluate_hard_constraints(chromosome: Chromosome, dataset: Dataset) -> HardEvaluation:
    """Evaluate H01-H10 after structural validation, using raw baseline counts.

    Dataset must already satisfy the loader's integrity contract. Known but
    forbidden slots are hard violations; unknown slots are structural errors.
    MinHours is measured only. No soft score, cache writes or repair occur.
    """
    issues = validate_structure(chromosome, dataset)
    if issues:
        raise StructuralValidationError(issues)

    courses, students, professors = dataset.courses, dataset.students, dataset.professors
    rooms = {r.room_id: r for r in dataset.tables['Classrooms']}
    slots = {(s.day, s.time_slot): s for s in dataset.tables['TimeSlots']}
    active = {sid: s for sid, s in sorted(chromosome.sections.items()) if s.active}
    members = {sid: set() for sid in chromosome.sections}
    credits = dict.fromkeys(students, 0)
    teaching = dict.fromkeys(professors, 0)
    capacities, violations = {}, []
    unassigned = []
    passed = {(r.student_id, r.course_id) for r in dataset.tables.get('PassedCourses', ())}
    qualified = {(q.professor_id, q.course_id) for q in dataset.tables.get('ProfessorCourses', ()) if q.can_teach}

    def add(kind, severity=1, **details):
        violations.append(Violation(kind, severity, **details))

    for request, sid in sorted(chromosome.enrollments.items()):
        if sid is None:
            unassigned.append(request)
            continue
        student_id, course_id = request
        members[sid].add(student_id)
        credits[student_id] += courses[course_id].credits
        if request in passed:
            add('H10_PASSED_COURSE', student_id=student_id,
                course_ids=(course_id,), section_ids=(sid,))

    meetings = []
    for sid, section in active.items():
        course, room = courses[section.course_id], rooms[section.room_id]
        common = dict(section_ids=(sid,), course_ids=(section.course_id,))
        capacities[sid] = min(course.max_section_size, room.capacity)
        if (section.professor_id, section.course_id) not in qualified:
            add('H03_UNQUALIFIED_PROFESSOR', professor_id=section.professor_id, **common)
        if course.required_room_type != room.room_type:
            add('H06_ROOM_TYPE', room_id=section.room_id, **common)
        size = len(members[sid])
        if size < course.min_section_size:
            add('H07_SECTION_UNDERFLOW', course.min_section_size - size,
                measured_value=size, limit=course.min_section_size, unit='students', **common)
        if size > capacities[sid]:
            add('H07_SECTION_OVERFLOW', size - capacities[sid], measured_value=size,
                limit=capacities[sid], unit='students', **common)
        for slot_key in sorted(section.meeting_slots):
            slot = slots[slot_key]
            meeting = _Meeting(sid, section.course_id, section.professor_id,
                               section.room_id, slot_key, slot.start_time, slot.end_time)
            meetings.append(meeting)
            teaching[section.professor_id] += slot.end_time - slot.start_time
            if not slot.is_allowed:
                # One violation per scheduled meeting, not per attendee.
                add('H02_DISALLOWED_SLOT', day=slot.day, slot_keys=(slot_key,),
                    start_minute=slot.start_time, end_minute=slot.end_time, **common)

    # Unavailable rows denote real time intervals. Multiple overlapping blocked
    # rows still count only one unavailable presence per meeting and person.
    unavailable = {}
    for table in ('ProfessorAvailability', 'StudentAvailability'):
        blocked = defaultdict(list)
        for row in dataset.tables.get(table, ()):
            if row.status == 'Unavailable':
                slot = slots[(row.day, row.time_slot)]
                blocked[(row.person_id, row.day)].append((slot.start_time, slot.end_time))
        unavailable[table] = blocked

    professor_meetings, room_meetings, student_meetings = (defaultdict(list) for _ in range(3))
    for meeting in meetings:
        common = dict(section_ids=(meeting.section_id,), course_ids=(meeting.course_id,),
                      day=meeting.day, slot_keys=(meeting.slot_key,),
                      start_minute=meeting.start, end_minute=meeting.end)
        professor_meetings[(meeting.professor_id, meeting.day)].append(meeting)
        room_meetings[(meeting.room_id, meeting.day)].append(meeting)
        if any(_overlap(meeting.start, meeting.end, start, end)
               for start, end in unavailable['ProfessorAvailability'][(meeting.professor_id, meeting.day)]):
            add('H05_PROFESSOR_UNAVAILABLE', professor_id=meeting.professor_id, **common)
        for student_id in sorted(members[meeting.section_id]):
            student_meetings[(student_id, meeting.day)].append(meeting)
            if any(_overlap(meeting.start, meeting.end, start, end)
                   for start, end in unavailable['StudentAvailability'][(student_id, meeting.day)]):
                add('H05_STUDENT_UNAVAILABLE', student_id=student_id, **common)

    for kind, resource_field, groups in (
        ('H04_PROFESSOR_OVERLAP', 'professor_id', professor_meetings),
        ('H04_ROOM_OVERLAP', 'room_id', room_meetings),
        ('H04_STUDENT_OVERLAP', 'student_id', student_meetings),
    ):
        for (resource_id, day), group in sorted(groups.items()):
            for first, second in combinations(group, 2):
                if _overlap(first.start, first.end, second.start, second.end):
                    add(kind, section_ids=(first.section_id, second.section_id),
                        course_ids=(first.course_id, second.course_id), day=day,
                        slot_keys=(first.slot_key, second.slot_key),
                        start_minute=max(first.start, second.start),
                        end_minute=min(first.end, second.end), **{resource_field: resource_id})

    for student_id, student in sorted(students.items()):
        if credits[student_id] < student.min_credits:
            add('H08_CREDIT_UNDERFLOW', student.min_credits - credits[student_id],
                student_id=student_id, measured_value=credits[student_id],
                limit=student.min_credits, unit='credits')
        if credits[student_id] > student.max_credits:
            add('H08_CREDIT_OVERFLOW', credits[student_id] - student.max_credits,
                student_id=student_id, measured_value=credits[student_id],
                limit=student.max_credits, unit='credits')

    shortfall, excess = {}, {}
    for professor_id, professor in sorted(professors.items()):
        shortfall[professor_id] = max(0, professor.min_minutes - teaching[professor_id])
        excess[professor_id] = max(0, teaching[professor_id] - professor.max_minutes)
        if excess[professor_id]:
            add('H09_TEACHING_OVERLOAD', (excess[professor_id] + 29) // 30,
                professor_id=professor_id, measured_value=teaching[professor_id],
                limit=professor.max_minutes, unit='minutes')

    diagnostics = []
    demand = Counter(r.course_id for r in dataset.tables['StudentRequests'])
    eligible_credits = Counter()
    for request in dataset.tables['StudentRequests']:
        course = courses[request.course_id]
        if demand[request.course_id] >= course.min_section_size and (request.student_id, request.course_id) not in passed:
            eligible_credits[request.student_id] += course.credits
    for course_id, course in sorted(courses.items()):
        if demand[course_id] < course.min_section_size:
            diagnostics.append(Diagnostic('DATASET_LOW_DEMAND', (course_id,),
                                          f'{demand[course_id]} requests; section minimum {course.min_section_size}'))
        if not any(cid == course_id for _, cid in qualified):
            diagnostics.append(Diagnostic('DATASET_NO_QUALIFIED_PROFESSOR', (course_id,), 'No qualified professor in sample'))
        if not any(r.room_type == course.required_room_type and min(r.capacity, course.max_section_size) >= course.min_section_size for r in rooms.values()):
            diagnostics.append(Diagnostic('DATASET_NO_COMPATIBLE_ROOM', (course_id,), 'No sufficiently large compatible room in sample'))
    for student_id, student in sorted(students.items()):
        if eligible_credits[student_id] < student.min_credits:
            diagnostics.append(Diagnostic('DATASET_CREDIT_UPPER_BOUND_BELOW_MINIMUM', (student_id,),
                                          f'At most {eligible_credits[student_id]} credits before resource/time constraints; minimum {student.min_credits}'))

    counts = dict.fromkeys(CONSTRAINT_TYPES, 0)
    for violation in violations:
        counts[violation.constraint_type] += violation.severity
    metrics = HardMetrics(
        MappingProxyType({sid: len(members[sid]) for sid in sorted(members)}),
        MappingProxyType(capacities), MappingProxyType(credits), MappingProxyType(teaching),
        MappingProxyType(shortfall), MappingProxyType(excess), tuple(unassigned),
    )
    return HardEvaluation(tuple(violations), MappingProxyType(counts), metrics, tuple(diagnostics))
