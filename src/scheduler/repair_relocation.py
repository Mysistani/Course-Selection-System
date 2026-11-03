"""Bounded room-conflict proposals and conservative prefilters, never acceptance.

The canonical evaluator owns constraint semantics and all scored decisions.
These indexes only screen the resources/intervals changed by one relocation.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import combinations, islice

from .constraints import _overlap


@dataclass(frozen=True)
class MeetingRelocation:
    section_id: str
    meeting_index: int
    slot: tuple[str, str]
    room_id: str


class RelocationIndex:
    def __init__(self, state):
        self.state = state
        self.rooms, self.professors, self.students = (defaultdict(list) for _ in range(3))
        context = state.context
        for sid in state.active:
            section = state.chromosome.sections[sid]
            for key in sorted(section.meeting_slots):
                slot = context.slots[key]
                record = (sid, key, slot.start_time, slot.end_time)
                self.rooms[(section.room_id,slot.day)].append(record)
                self.professors[(section.professor_id,slot.day)].append(record)
                for student, _ in state.members[sid]:
                    self.students[(student,slot.day)].append(record)

    def admissible(self, target):
        state, context = self.state, self.state.context
        section = state.chromosome.sections[target.section_id]
        old = section.meeting_slots[target.meeting_index]
        destination = context.slots[target.slot]
        source = context.slots[old]
        if target.slot != old and target.slot in section.meeting_slots:
            return False
        if target.slot == old and target.room_id == section.room_id:
            return False
        if not destination.is_allowed or destination.end_time-destination.start_time != source.end_time-source.start_time:
            return False
        if target.room_id not in context.compatible_rooms[section.course_id] or len(state.members[target.section_id]) > context.effective_capacities[(section.course_id,target.room_id)]:
            return False
        keys = list(section.meeting_slots)
        keys[target.meeting_index] = target.slot
        if len(set(keys)) != len(keys):
            return False
        # Room is a section-level gene: a replacement must fit ALL its meetings.
        proposed = [context.slots[key] for key in keys]
        for a,b in combinations(proposed,2):
            if a.day == b.day and _overlap(a.start_time,a.end_time,b.start_time,b.end_time):
                return False
        for meeting in proposed:
            if any(sid != target.section_id and _overlap(meeting.start_time,meeting.end_time,start,end)
                   for sid,_,start,end in self.rooms.get((target.room_id,meeting.day),())):
                return False
        # Only the selected meeting changes time; remove exactly its old presence.
        def occupied(index, person):
            return any((sid,key) != (target.section_id,old) and
                       _overlap(destination.start_time,destination.end_time,start,end)
                       for sid,key,start,end in index.get((person,destination.day),()))
        def blocked(index, person):
            return any(_overlap(destination.start_time,destination.end_time,start,end)
                       for start,end in index.get((person,destination.day),()))
        if occupied(self.professors,section.professor_id) or blocked(context.professor_unavailable,section.professor_id):
            return False
        for student,_ in state.members[target.section_id]:
            if occupied(self.students,student) or blocked(context.student_unavailable,student):
                return False
        return True

    def draft(self, target):
        section = self.state.chromosome.sections[target.section_id]
        keys = list(section.meeting_slots)
        keys[target.meeting_index] = target.slot
        sections = dict(self.state.chromosome.sections)
        sections[target.section_id] = replace(section,meeting_slots=tuple(keys),room_id=target.room_id)
        return sections, dict(self.state.chromosome.enrollments)


def relocation_proposals(state, violation, preferred_attempts=10):
    """A bounded same-room prefix, room-only fallback, then remaining moves.

    Raw proposals (including filtered ones) consume the caller's attempt budget.
    No shuffled Cartesian product or unbounded retry is materialized.
    """
    endpoints = []
    for sid,key in sorted(zip(violation.section_ids,violation.slot_keys)):
        section = state.chromosome.sections.get(sid)
        if section is not None and section.active and key in section.meeting_slots:
            endpoint = (sid,section.meeting_slots.index(key))
            if endpoint not in endpoints:
                endpoints.append(endpoint)
    def options(sid, index, tier):
        section = state.chromosome.sections[sid]
        old = section.meeting_slots[index]
        source = state.context.slots[old]
        rooms = (section.room_id,) if tier == 'same' else sorted(state.context.compatible_rooms[section.course_id]-{section.room_id})
        keys = (old,) if tier == 'room_only' else sorted(state.allowed,key=lambda k:(k[0]!=source.day,abs(state.context.slots[k].start_time-source.start_time),k))
        for key in keys:
            if tier == 'other' and key == old:
                continue
            for room in rooms:
                yield MeetingRelocation(sid,index,key,room)
    def interleave(tier):
        generators = [iter(options(sid,index,tier)) for sid,index in endpoints]
        while generators:
            remaining = []
            for generator in generators:
                try:
                    yield next(generator)
                    remaining.append(generator)
                except StopIteration:
                    pass
            generators = remaining
    same = interleave('same')
    yield from islice(same,preferred_attempts)
    yield from interleave('room_only')
    yield from same
    yield from interleave('other')
