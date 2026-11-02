"""Immutable input records. Units are explicit; IDs remain strings."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Faculty:
    faculty_id: str
    faculty_name: str


@dataclass(frozen=True)
class Major:
    major_id: str
    faculty_id: str
    major_name: str


@dataclass(frozen=True)
class Course:
    course_id: str
    major_id: str
    course_name: str
    credits: int
    suggested_semester: int
    course_type: str
    sessions_per_week: int
    session_duration: int
    required_room_type: str
    min_section_size: int
    max_section_size: int
    difficulty_level: int


@dataclass(frozen=True)
class Room:
    room_id: str
    faculty_id: str
    room_type: str
    capacity: int
    equipment: str


@dataclass(frozen=True)
class Slot:
    slot_id: str
    day: str
    time_slot: str
    start_time: int  # Minutes after midnight.
    end_time: int
    is_allowed: bool


@dataclass(frozen=True)
class Professor:
    professor_id: str
    major_id: str
    professor_name: str
    min_hours: float
    max_hours: float

    @property
    def min_minutes(self) -> int:
        return round(self.min_hours * 60)

    @property
    def max_minutes(self) -> int:
        return round(self.max_hours * 60)


@dataclass(frozen=True)
class Qualification:
    professor_id: str
    course_id: str
    can_teach: bool
    willingness: int


@dataclass(frozen=True)
class Student:
    student_id: str
    major_id: str
    current_semester: int
    min_credits: int
    max_credits: int


@dataclass(frozen=True)
class PassedCourse:
    student_id: str
    course_id: str


@dataclass(frozen=True)
class Request:
    student_id: str
    course_id: str
    priority: int


@dataclass(frozen=True)
class Availability:
    person_id: str
    day: str
    time_slot: str
    status: str
    weight: int | None


@dataclass(frozen=True)
class Preference:
    person_id: str
    preference_type: str
    target: int | None
    weight: int


@dataclass(frozen=True)
class InputIssue:
    sheet: str
    cell: str
    message: str

    def __str__(self) -> str:
        return f'{self.sheet}!{self.cell}: {self.message}'


@dataclass(frozen=True)
class Dataset:
    tables: Mapping[str, tuple]
    sha256: str
    warnings: tuple[InputIssue, ...]

    def index(self, sheet: str, field: str) -> Mapping:
        return MappingProxyType({getattr(row, field): row for row in self.tables[sheet]})

    @property
    def courses(self) -> Mapping[str, Course]:
        return self.index('Courses', 'course_id')

    @property
    def students(self) -> Mapping[str, Student]:
        return self.index('Students', 'student_id')

    @property
    def professors(self) -> Mapping[str, Professor]:
        return self.index('Professors', 'professor_id')
