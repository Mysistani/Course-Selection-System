"""Read-only projections and Excel export of one exact, evaluated snapshot."""
from collections import Counter
from io import BytesIO
import json

WARNING = 'برنامه پیشنهادی نامعتبر — دارای نقض محدودیت‌های سخت'


def clock(minutes):
    return f'{minutes//60:02d}:{minutes%60:02d}'


def related(violation, role, entity_id, section_ids):
    field = {'student': 'student_id', 'professor': 'professor_id', 'room': 'room_id'}[role]
    if violation.get(field) is not None:
        return violation[field] == entity_id
    # Other students' personal conflicts are not the selected person's violations.
    if violation.get('student_id') is not None:
        return False
    return bool(set(violation['section_ids']) & section_ids)


def project(snapshot, context, role, entity_id):
    mappings = {'student': context.students, 'professor': context.professors, 'room': context.rooms}
    if role not in mappings or entity_id not in mappings[role]:
        raise KeyError('entity not found')
    sections = {s['section_id']: s for s in snapshot['sections']}
    enrollments = snapshot['enrollments']
    sizes = Counter(r['section_id'] for r in enrollments if r['section_id'] is not None)
    own = [r for r in enrollments if r['student_id'] == entity_id] if role == 'student' else []
    selected = {r['section_id'] for r in own if r['section_id'] is not None} if role == 'student' else {
        sid for sid, s in sections.items() if s['active'] and s['professor_id' if role == 'professor' else 'room_id'] == entity_id}
    violations = [v for v in snapshot['hard_evaluation']['violations'] if related(v, role, entity_id, selected)]
    events = []
    for sid in sorted(selected):
        section = sections[sid]
        course = context.courses[section['course_id']]
        for day, slot_id in section['meeting_slots']:
            slot = context.slots[(day, slot_id)]
            relevant = [v for v in violations if (not v['section_ids'] or sid in v['section_ids']) and
                        (v['day'] is None or v['day'] == day) and
                        (v['start_minute'] is None or slot.start_time < v['end_minute'] and v['start_minute'] < slot.end_time)]
            events.append({'section_id': sid, 'course_id': course.course_id, 'course_name': course.course_name,
                'professor_id': section['professor_id'], 'professor_name': context.professors[section['professor_id']].professor_name,
                'room_id': section['room_id'], 'day': day, 'slot_id': slot_id,
                'start_minute': slot.start_time, 'end_minute': slot.end_time,
                'start': clock(slot.start_time), 'end': clock(slot.end_time),
                'enrollment_count': sizes[sid], 'capacity': context.effective_capacities[(course.course_id, section['room_id'])],
                'violations': relevant})
    # Explicit overlap partners; browser renders each overlapping event in a separate lane.
    for index, event in enumerate(events):
        event['overlaps'] = [j for j, other in enumerate(events) if j != index and other['day'] == event['day'] and
                             event['start_minute'] < other['end_minute'] and other['start_minute'] < event['end_minute']]
    entity = mappings[role][entity_id]
    detail = {}
    if role == 'student':
        detail = {'credits': sum(context.courses[r['course_id']].credits for r in own if r['section_id'] is not None),
                  'min_credits': entity.min_credits, 'max_credits': entity.max_credits,
                  'requests': [{**r,'course_name':context.courses[r['course_id']].course_name,
                                'credits':context.courses[r['course_id']].credits} for r in own],
                  'unassigned': [r['course_id'] for r in own if r['section_id'] is None]}
    elif role == 'professor':
        minutes = sum(e['end_minute']-e['start_minute'] for e in events)
        detail = {'teaching_minutes': minutes, 'teaching_hours': minutes/60, 'max_hours': entity.max_hours}
    else:
        detail = {'capacity': entity.capacity, 'room_type': entity.room_type, 'equipment': entity.equipment}
    week = ('Saturday','Sunday','Monday','Tuesday','Wednesday','Thursday','Friday')
    days = sorted(context.calendar_days,key=lambda d:(week.index(d) if d in week else len(week),d))
    return {'fingerprint': snapshot['fingerprint'], 'role': role, 'entity_id': entity_id,
            'days': days, 'events': events, 'violations': violations, 'detail': detail}


def export_excel(snapshot, context, run_id, generation):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    workbook.remove(workbook.active)
    faculty = [['CourseID','CourseName','SectionID','ProfessorID','ProfessorName','ClassroomID','Day','StartTime','EndTime','EnrollmentCount','Capacity']]
    for rid in context.rooms:
        for e in project(snapshot, context, 'room', rid)['events']:
            faculty.append([e[k] for k in ('course_id','course_name','section_id','professor_id','professor_name','room_id','day','start','end','enrollment_count','capacity')])
    enrollment = [['StudentID','CourseID','CourseName','SectionID','Credits','AssignmentStatus']]
    weekly = [['StudentID','Day','StartTime','EndTime','CourseID','CourseName','SectionID','ProfessorID','ClassroomID']]
    for r in snapshot['enrollments']:
        course = context.courses[r['course_id']]
        enrollment.append([r['student_id'], r['course_id'], course.course_name, r['section_id'], course.credits,
                           'Assigned' if r['section_id'] is not None else 'Unassigned'])
    for sid in context.students:
        for e in project(snapshot, context, 'student', sid)['events']:
            weekly.append([sid]+[e[k] for k in ('day','start','end','course_id','course_name','section_id','professor_id','room_id')])
    validation = [['DatasetID','RunID','Generation','Fingerprint','HardPenalty','SoftScore','Feasible','ConstraintType','Severity','AffectedEntities','Details']]
    prefix = [context.dataset.sha256,run_id,generation,snapshot['fingerprint'],snapshot['hard_penalty'],snapshot['soft_score'],snapshot['feasible']]
    validation.append(prefix+['SUMMARY',0,'', 'برنامه معتبر' if snapshot['feasible'] else WARNING])
    for v in snapshot['hard_evaluation']['violations']:
        affected = {k: v[k] for k in ('student_id','professor_id','room_id','section_ids','course_ids') if v[k]}
        validation.append(prefix+[v['constraint_type'],v['severity'],json.dumps(affected,ensure_ascii=False),json.dumps(v,ensure_ascii=False)])
    for name, rows in [('Faculty Timetable',faculty),('Student Enrollments',enrollment),('Student Weekly Timetables',weekly),('Validation Report',validation)]:
        sheet = workbook.create_sheet(name)
        sheet.sheet_view.rightToLeft = True
        # Force all strings to text, including user-controlled strings starting with '='.
        for row in rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = PatternFill('solid', fgColor='173C50'); cell.font = Font(color='FFFFFF',bold=True)
        for col in range(1, sheet.max_column+1):
            sheet.column_dimensions[get_column_letter(col)].width = min(48,max(16,len(str(rows[0][col-1]))+3))
        sheet.oddHeader.center.text = 'برنامه معتبر' if snapshot['feasible'] else WARNING
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    buffer = BytesIO(); workbook.save(buffer)
    return buffer.getvalue()
