"""Provisional engineering defaults, not historical user-approved rules."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DesignPolicy:
    version: str = 'draft-2026-11-02-v1'
    missing_availability: str = 'available_neutral'
    teaching_days_target: str = 'exact'
    minimum_professor_hours: str = 'report_only'
    difficult_course_threshold: int = 4
    difficult_target: str = 'max_distinct_courses_per_day'
    free_gap_minutes: int = 30
    requests_only: bool = True
    require_different_meeting_days: bool = False
    hard_comparison: str = 'sum_raw_then_soft'
    soft_components: tuple[str, ...] = ('requests', 'students', 'professors')


DEFAULT_POLICY = DesignPolicy()
