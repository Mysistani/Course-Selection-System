"""Strict, read-only loader for the project's 14-sheet OOXML contract."""
from __future__ import annotations

from collections import Counter
from dataclasses import fields
import hashlib
import math
from pathlib import Path
import posixpath
import re
from types import MappingProxyType
import xml.etree.ElementTree as ET
import zipfile

from . import domain as D

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MODELS = {
    'Faculties': D.Faculty, 'Majors': D.Major, 'Courses': D.Course,
    'Classrooms': D.Room, 'TimeSlots': D.Slot, 'Professors': D.Professor,
    'ProfessorCourses': D.Qualification, 'Students': D.Student,
    'PassedCourses': D.PassedCourse, 'StudentRequests': D.Request,
    'ProfessorAvailability': D.Availability, 'StudentAvailability': D.Availability,
    'ProfessorPreferences': D.Preference, 'StudentPreferences': D.Preference,
}
KEYS = {
    'Faculties': ('faculty_id',), 'Majors': ('major_id',), 'Courses': ('course_id',),
    'Classrooms': ('room_id',), 'TimeSlots': ('slot_id',), 'Professors': ('professor_id',),
    'Students': ('student_id',), 'ProfessorCourses': ('professor_id', 'course_id'),
    'PassedCourses': ('student_id', 'course_id'), 'StudentRequests': ('student_id', 'course_id'),
    'ProfessorAvailability': ('person_id', 'day', 'time_slot'),
    'StudentAvailability': ('person_id', 'day', 'time_slot'),
    'ProfessorPreferences': ('person_id', 'preference_type'),
    'StudentPreferences': ('person_id', 'preference_type'),
}
DAYS = ('Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday')


class DatasetError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__('\n'.join(map(str, self.issues)))


def column_name(index):
    text = ''
    while index:
        index, digit = divmod(index - 1, 26)
        text = chr(65 + digit) + text
    return text


def header_name(field, sheet):
    if field == 'person_id':
        return 'ProfessorID' if sheet.startswith('Professor') else 'StudentID'
    return ''.join('ID' if word == 'id' else word.title() for word in field.split('_'))


def convert(value, typ, field):
    if typ == int | None:
        return None if value is None else convert(value, int, field)
    if value is None:
        raise ValueError('required value is blank')
    if field in ('start_time', 'end_time'):
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('expected HH:MM in 24-hour time')
        h, m = map(int, value.split(':'))
        return h * 60 + m
    if typ is str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError('expected non-empty text without outer whitespace')
    elif typ is bool:
        if type(value) is not bool:
            raise ValueError('expected an Excel Boolean cell')
    elif typ in (int, float):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('expected a finite number (not text/Boolean)')
        if typ is int and value != int(value):
            raise ValueError('expected an integer')
        value = typ(value)
    return value


def _read_xml(path):
    """Return cells with their actual coordinates; reject formulas, not cached values."""
    tables, issues = {}, []
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f'corrupt archive member: {corrupt}')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in item.findall('.//s:t', NS))
                       for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        rels = {r.get('Id'): r for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        book = ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in book.find('s:sheets', NS):
            name = sheet.get('name')
            if name in tables:
                issues.append(D.InputIssue(name, 'A1', 'duplicate sheet name'))
                continue
            rel = rels[sheet.get(RID)]
            if rel.get('TargetMode') == 'External':
                issues.append(D.InputIssue(name, 'A1', 'external worksheet is not supported'))
                continue
            target = rel.get('Target')
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            root = ET.fromstring(archive.read(target))
            cells = {}
            for cell in root.findall('s:sheetData/s:row/s:c', NS):
                ref = cell.get('r', '?')
                try:
                    if cell.find('s:f', NS) is not None:
                        raise ValueError('formulas are not accepted in input data')
                    value = cell.find('s:v', NS)
                    value = value.text if value is not None else None
                    typ = cell.get('t')
                    if typ == 's' and value is not None:
                        value = strings[int(value)]
                    elif typ == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    elif typ == 'b' and value is not None:
                        if value not in ('0', '1'):
                            raise ValueError('invalid Boolean encoding')
                        value = value == '1'
                    elif typ in (None, 'n') and value is not None:
                        value = float(value)
                    elif typ == 'e':
                        raise ValueError(f'Excel error: {value}')
                    elif typ not in ('s', 'inlineStr', 'b', 'str', None, 'n'):
                        raise ValueError(f'unsupported cell type {typ}')
                    if value is not None:
                        cells[ref] = value
                except (ValueError, IndexError) as exc:
                    issues.append(D.InputIssue(name, ref, str(exc)))
            tables[name] = cells
    if issues:
        raise DatasetError(issues)
    return tables


def load_dataset(path: str | Path, *, include_feasibility_diagnostics: bool = False) -> D.Dataset:
    path = Path(path)
    try:
        raw = _read_xml(path)
    except DatasetError:
        raise
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, TypeError, AttributeError) as exc:
        raise DatasetError([D.InputIssue('Workbook', '-', f'cannot read XLSX: {exc}')]) from exc
    issues, tables, locations = [], {}, {}
    for name in sorted(set(raw) - set(MODELS)):
        issues.append(D.InputIssue(name, 'A1', 'unexpected sheet'))
    for name, model in MODELS.items():
        if name not in raw:
            issues.append(D.InputIssue(name, 'A1', 'required sheet is missing'))
            continue
        cells = raw[name]
        headers = {ref[:-1]: value for ref, value in cells.items() if re.fullmatch(r'[A-Z]+1', ref)}
        expected = {header_name(f.name, name): f for f in fields(model)}
        if len(set(headers.values())) != len(headers):
            issues.append(D.InputIssue(name, 'A1', 'duplicate column header'))
        missing = set(expected) - set(headers.values())
        extra = set(headers.values()) - set(expected)
        if missing or extra:
            issues.append(D.InputIssue(name, 'A1', f'columns: missing={sorted(missing)}, unexpected={sorted(extra, key=str)}'))
            continue
        columns = {value: col for col, value in headers.items()}
        for ref in cells:
            if re.sub(r'\d+$', '', ref) not in headers:
                issues.append(D.InputIssue(name, ref, 'value in column without header'))
        rows = sorted({int(re.search(r'\d+$', ref).group()) for ref in cells if re.fullmatch(r'[A-Z]+[1-9]\d*', ref)} - {1})
        records = []
        for row in rows:
            values, refs = {}, {}
            for header, field in expected.items():
                ref = f'{columns[header]}{row}'
                refs[field.name] = ref
                try:
                    values[field.name] = convert(cells.get(ref), field.type, field.name)
                except (ValueError, TypeError) as exc:
                    issues.append(D.InputIssue(name, ref, str(exc)))
            if len(values) == len(expected):
                records.append(model(**values))
                locations[(name, len(records) - 1)] = refs
        tables[name] = tuple(records)
    if issues:
        raise DatasetError(issues)
    warnings = _validate(tables, locations, include_feasibility_diagnostics)
    return D.Dataset(MappingProxyType(tables), hashlib.sha256(path.read_bytes()).hexdigest(), tuple(warnings))


def _validate(tables, locations, include_feasibility_diagnostics):
    errors, warnings = [], []

    def issue(sheet, index, field, message, warning=False):
        (warnings if warning else errors).append(D.InputIssue(sheet, locations[(sheet, index)][field], message))

    targets = {'faculty_id': ('Faculties', 'faculty_id'), 'major_id': ('Majors', 'major_id'),
               'course_id': ('Courses', 'course_id'), 'professor_id': ('Professors', 'professor_id'),
               'student_id': ('Students', 'student_id')}
    ids = {field: {getattr(r, field) for r in tables[sheet]} for field, (sheet, _) in targets.items()}
    for sheet, records in tables.items():
        seen = {}
        for i, r in enumerate(records):
            key = tuple(getattr(r, f) for f in KEYS[sheet])
            if key in seen:
                label = 'duplicate student request' if sheet == 'StudentRequests' else 'duplicate key'
                issue(sheet, i, KEYS[sheet][0], f'{label} {key}; first occurrence at {seen[key]}')
            else:
                seen[key] = locations[(sheet, i)][KEYS[sheet][0]]
            for field, (parent, _) in targets.items():
                if sheet != parent and hasattr(r, field) and getattr(r, field) not in ids[field]:
                    issue(sheet, i, field, f'unknown reference to {parent}: {getattr(r, field)}')
            if hasattr(r, 'person_id'):
                field = 'professor_id' if sheet.startswith('Professor') else 'student_id'
                if r.person_id not in ids[field]:
                    issue(sheet, i, 'person_id', 'unknown person reference')

    slots = {(r.day, r.time_slot): r for r in tables['TimeSlots']}
    seen_times = set()
    for i, r in enumerate(tables['TimeSlots']):
        if (r.day, r.time_slot) in seen_times:
            issue('TimeSlots', i, 'time_slot', 'duplicate day/time pair')
        seen_times.add((r.day, r.time_slot))
        if r.day not in DAYS or r.time_slot not in ('T1', 'T2', 'T3', 'T4', 'T5'):
            issue('TimeSlots', i, 'day', 'invalid day/time label')
        if r.end_time - r.start_time != 90:
            issue('TimeSlots', i, 'end_time', 'session must last 90 minutes')
    if set(slots) != {(day, f'T{n}') for day in DAYS for n in range(1, 6)}:
        errors.append(D.InputIssue('TimeSlots', 'A1', 'expected all 25 day/time combinations'))
    for i, r in enumerate(tables['Courses']):
        bounds = {'credits': 1 <= r.credits <= 4, 'suggested_semester': r.suggested_semester >= 1,
                  'course_type': r.course_type in ('Theory', 'Lab', 'Workshop'),
                  'sessions_per_week': 1 <= r.sessions_per_week <= 25, 'session_duration': r.session_duration == 90,
                  'min_section_size': r.min_section_size == 8, 'max_section_size': r.max_section_size >= r.min_section_size,
                  'difficulty_level': 1 <= r.difficulty_level <= 5}
        for field, valid in bounds.items():
            if not valid:
                issue('Courses', i, field, 'value outside project contract')
    for i, r in enumerate(tables['Classrooms']):
        if r.capacity <= 0:
            issue('Classrooms', i, 'capacity', 'room capacity must be positive')
    for sheet in ('Students', 'Professors'):
        for i, r in enumerate(tables[sheet]):
            low, high = (r.min_credits, r.max_credits) if sheet == 'Students' else (r.min_hours, r.max_hours)
            field = 'min_credits' if sheet == 'Students' else 'min_hours'
            if not 0 <= low <= high:
                issue(sheet, i, field, 'invalid minimum/maximum bounds')
            if sheet == 'Students' and r.current_semester < 1:
                issue(sheet, i, 'current_semester', 'semester must be positive')
            if sheet == 'Professors' and any(abs(v * 60 - round(v * 60)) > 1e-8 for v in (low, high)):
                issue(sheet, i, field, 'hours must resolve to whole minutes')
    for sheet in ('ProfessorAvailability', 'StudentAvailability'):
        for i, r in enumerate(tables[sheet]):
            if (r.day, r.time_slot) not in slots:
                issue(sheet, i, 'time_slot', 'unknown day/time pair')
            if r.status not in ('Unavailable', 'Preferred'):
                issue(sheet, i, 'status', 'expected Unavailable or Preferred')
            if r.status == 'Preferred' and (r.weight is None or not 0 <= r.weight <= 10):
                issue(sheet, i, 'weight', 'Preferred requires a weight from 0 to 10')
            if r.status == 'Unavailable' and r.weight is not None:
                issue(sheet, i, 'weight', 'Unavailable must have a blank weight')
    for sheet in ('ProfessorPreferences', 'StudentPreferences'):
        types = {'PreferredTeachingDays', 'MinimizeGaps', 'PreferredTimeSlots'}
        types.add('PreferredCourses' if sheet.startswith('Professor') else 'SpreadDifficultCourses')
        for i, r in enumerate(tables[sheet]):
            if r.preference_type not in types:
                issue(sheet, i, 'preference_type', 'unknown preference type')
            if not 0 <= r.weight <= 10:
                issue(sheet, i, 'weight', 'weight must be 0..10')
            if r.preference_type in ('PreferredTimeSlots', 'PreferredCourses'):
                valid = r.target is None
            elif r.preference_type == 'PreferredTeachingDays':
                valid = r.target is not None and 1 <= r.target <= 5
            elif r.preference_type == 'MinimizeGaps':
                valid = r.target == 30
            else:
                valid = r.target is not None and r.target >= 1
            if not valid:
                issue(sheet, i, 'target', 'Target does not match preference contract')
    for sheet, field in (('ProfessorCourses', 'willingness'), ('StudentRequests', 'priority')):
        for i, r in enumerate(tables[sheet]):
            if not 1 <= getattr(r, field) <= 10:
                issue(sheet, i, field, 'value must be 1..10')
    if errors:
        raise DatasetError(errors)
    courses = {r.course_id: r for r in tables['Courses']}
    passed = {(r.student_id, r.course_id) for r in tables['PassedCourses']}
    demand = Counter(r.course_id for r in tables['StudentRequests'])
    for i, r in enumerate(tables['StudentRequests']):
        if (r.student_id, r.course_id) in passed:
            issue('StudentRequests', i, 'course_id', 'requested course was already passed')
    if errors:
        raise DatasetError(errors)
    if not include_feasibility_diagnostics:
        return warnings
    for i, r in enumerate(tables['Courses']):
        if not any(q.course_id == r.course_id and q.can_teach for q in tables['ProfessorCourses']):
            issue('Courses', i, 'course_id', 'no qualified professor in this sample', True)
        if not any(room.room_type == r.required_room_type and min(room.capacity, r.max_section_size) >= r.min_section_size for room in tables['Classrooms']):
            issue('Courses', i, 'required_room_type', 'no compatible room with sufficient capacity in this sample', True)
        if demand[r.course_id] < r.min_section_size:
            issue('Courses', i, 'course_id', f'demand {demand[r.course_id]} below section minimum {r.min_section_size}', True)
    for i, student in enumerate(tables['Students']):
        total = sum(courses[r.course_id].credits for r in tables['StudentRequests'] if r.student_id == student.student_id and demand[r.course_id] >= courses[r.course_id].min_section_size)
        if total < student.min_credits:
            issue('Students', i, 'min_credits', f'eligible requested credits {total} below minimum', True)
    if errors:
        raise DatasetError(errors)
    return warnings
