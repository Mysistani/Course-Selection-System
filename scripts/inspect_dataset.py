"""Read-only XLSX audit using Python's standard library; does not run Excel."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
import zipfile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
KEYS = {
    'Faculties': ['FacultyID'], 'Majors': ['MajorID'], 'Courses': ['CourseID'],
    'Classrooms': ['RoomID'], 'TimeSlots': ['SlotID'], 'Professors': ['ProfessorID'],
    'ProfessorCourses': ['ProfessorID', 'CourseID'],
    'ProfessorAvailability': ['ProfessorID', 'Day', 'TimeSlot'],
    'ProfessorPreferences': ['ProfessorID', 'PreferenceType'], 'Students': ['StudentID'],
    'PassedCourses': ['StudentID', 'CourseID'], 'StudentRequests': ['StudentID', 'CourseID'],
    'StudentAvailability': ['StudentID', 'Day', 'TimeSlot'],
    'StudentPreferences': ['StudentID', 'PreferenceType'],
}

def read_book(path):
    result, formula_count, errors = {}, 0, []
    with zipfile.ZipFile(path) as z:
        corrupt = z.testzip()
        if corrupt:
            raise ValueError('Corrupt ZIP member: ' + corrupt)
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS):
                strings.append(''.join(t.text or '' for t in item.findall('.//s:t', NS)))
        rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets', NS):
            target = rels[sheet.get(RID)]
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            root = ET.fromstring(z.read(target))
            rows = []
            for row in root.findall('s:sheetData/s:row', NS):
                values = {}
                for cell in row.findall('s:c', NS):
                    ref = cell.get('r')
                    col = ''.join(c for c in ref if c.isalpha())
                    value = cell.find('s:v', NS)
                    value = value.text if value is not None else None
                    typ = cell.get('t')
                    if typ == 's' and value is not None:
                        value = strings[int(value)]
                    elif typ == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    elif typ == 'b' and value is not None:
                        value = bool(int(value))
                    elif typ in (None, 'n') and value is not None:
                        number = float(value)
                        value = int(number) if number.is_integer() else number
                    if cell.find('s:f', NS) is not None:
                        formula_count += 1
                    if typ == 'e':
                        errors.append([sheet.get('name'), ref, value])
                    values[col] = value
                if any(v is not None for v in values.values()):
                    rows.append(values)
            header = rows[0] if rows else {}
            result[sheet.get('name')] = {
                'headers': list(header.values()),
                'records': [{name: row.get(col) for col, name in header.items()} for row in rows[1:]],
            }
    return result, formula_count, errors

def audit(path):
    sheets, formulas, cell_errors = read_book(path)
    issues, checks = [], []
    def check(label, bad):
        checks.append({'check': label, 'violations': len(bad), 'examples': bad[:8]})
        if bad:
            issues.append(label)
    check('expected_sheet_names', sorted(set(KEYS) ^ set(sheets)))
    records = {s: v['records'] for s, v in sheets.items()}
    for sheet, keys in KEYS.items():
        if sheet not in records:
            continue
        counts = collections.Counter(tuple(row.get(k) for k in keys) for row in records[sheet])
        check(sheet + ': duplicate keys', [list(k) for k, n in counts.items() if n > 1])
        check(sheet + ': blank keys', [list(k) for k in counts if None in k or '' in k])
    targets = {'FacultyID': ('Faculties', 'FacultyID'), 'MajorID': ('Majors', 'MajorID'),
               'CourseID': ('Courses', 'CourseID'), 'ProfessorID': ('Professors', 'ProfessorID'),
               'StudentID': ('Students', 'StudentID')}
    for sheet, rows in records.items():
        for col, (parent, pk) in targets.items():
            if sheet == parent or col not in sheets[sheet]['headers']:
                continue
            valid = {r[pk] for r in records[parent]}
            check(sheet + ': FK ' + col, [i + 2 for i, r in enumerate(rows) if r[col] not in valid])
    slots = {(r['Day'], r['TimeSlot']) for r in records['TimeSlots']}
    for sheet in ('ProfessorAvailability', 'StudentAvailability'):
        check(sheet + ': unknown slot', [i + 2 for i, r in enumerate(records[sheet]) if (r['Day'], r['TimeSlot']) not in slots])
        check(sheet + ': invalid status', [i + 2 for i, r in enumerate(records[sheet]) if r['Status'] not in ('Unavailable', 'Preferred', 'Avoid', 'Available')])
    passed = {(r['StudentID'], r['CourseID']) for r in records['PassedCourses']}
    check('requested course already passed', [[r['StudentID'], r['CourseID']] for r in records['StudentRequests'] if (r['StudentID'], r['CourseID']) in passed])
    courses = {r['CourseID']: r for r in records['Courses']}
    prof_courses = records['ProfessorCourses']
    check('course has no qualified professor', [c for c in courses if not any(r['CourseID'] == c and r['CanTeach'] == 1 for r in prof_courses)])
    check('course has no compatible room', [c for c, r in courses.items() if not any(room['RoomType'] == r['RequiredRoomType'] and room['Capacity'] >= r['MinSectionSize'] for room in records['Classrooms'])])
    check('invalid course credits/sessions/duration/section bounds', [c for c, r in courses.items() if not (r['Credits'] in (1, 2, 3, 4) and r['SessionsPerWeek'] >= 1 and r['SessionDuration'] == 90 and 8 == r['MinSectionSize'] <= r['MaxSectionSize'])])
    for sheet, lo, hi in [('Students', 'MinCredits', 'MaxCredits'), ('Professors', 'MinHours', 'MaxHours')]:
        check(sheet + ': invalid bounds', [i + 2 for i, r in enumerate(records[sheet]) if not 0 <= r[lo] <= r[hi]])
    def minutes(t):
        h, m = map(int, t.split(':'))
        return 60 * h + m
    check('invalid slot duration/allowed', [r['SlotID'] for r in records['TimeSlots'] if minutes(r['EndTime']) - minutes(r['StartTime']) != 90 or r['IsAllowed'] not in (0, 1)])
    requests = collections.defaultdict(list)
    for r in records['StudentRequests']:
        requests[r['StudentID']].append(r['CourseID'])
    check('requested credits below minimum', [s['StudentID'] for s in records['Students'] if sum(courses[c]['Credits'] for c in requests[s['StudentID']]) < s['MinCredits']])
    check('stored Excel error cells', cell_errors)
    demand = collections.Counter(r['CourseID'] for r in records['StudentRequests'])
    demand_warnings = {'below_minimum': {c: demand[c] for c, r in courses.items() if 0 < demand[c] < r['MinSectionSize']},
                       'no_requests': [c for c in courses if not demand[c]]}
    for sheet in ('ProfessorPreferences', 'StudentPreferences'):
        for r in records[sheet]:
            if r['Weight'] is None or not 0 <= r['Weight'] <= 10:
                issues.append(sheet + ': invalid preference weight')
                break
    summary = {}
    for name, data in sheets.items():
        enums = {}
        for h in data['headers']:
            vals = collections.Counter(str(r[h]) for r in data['records'])
            if len(vals) <= 20:
                enums[h] = dict(vals)
        summary[name] = {'row_count': len(data['records']), 'headers': data['headers'], 'low_cardinality_values': enums}
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'sheet_count': len(sheets), 'formulas': formulas, 'sheets': summary,
            'checks': checks, 'checks_with_violations': issues, 'demand_warnings': demand_warnings,
            'limits': 'Static structural checks only; no Excel recalculation or proof of schedule feasibility.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', nargs='?', default='data/UniversityDataset_Corrected.xlsx')
    parser.add_argument('--output', default='docs/DATASET_AUDIT.json')
    args = parser.parse_args()
    report = audit(Path(args.workbook))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'sheets': report['sheet_count'], 'checks': len(report['checks']), 'issues': report['checks_with_violations'], 'report': args.output}, ensure_ascii=True))
