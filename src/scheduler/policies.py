"""Provisional engineering defaults, not historical user-approved rules."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DesignPolicy:
    version: str = 'phase3-soft-v1'
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
    soft_aggregation: str = 'coverage_product'
    neutral_time_score: float = 0.5
    availability_weight_scale: int = 10
    willingness_scale: int = 10
    gap_normalizer_minutes: int | None = None  # None derives a bound from the calendar.
    difficult_normalization: str = 'requested_meeting_count'
    empty_satisfaction_group: str = 'exclude'

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError('policy version must be non-empty text')
        object.__setattr__(self, 'soft_components', tuple(self.soft_components))
        supported = {
            'missing_availability': ('available_neutral',),
            'teaching_days_target': ('exact',),
            'minimum_professor_hours': ('report_only',),
            'difficult_target': ('max_distinct_courses_per_day',),
            'hard_comparison': ('sum_raw_then_soft',),
            'soft_aggregation': ('coverage_product', 'equal_components'),
            'difficult_normalization': ('requested_meeting_count',),
            'empty_satisfaction_group': ('exclude',),
        }
        for name, values in supported.items():
            if getattr(self, name) not in values:
                raise ValueError(f'unsupported policy {name}: {getattr(self, name)}')
        if self.soft_components != ('requests', 'students', 'professors'):
            raise ValueError('this version reports requests, students and professors')
        if self.requests_only is not True or self.require_different_meeting_days is not False:
            raise ValueError('unsupported hard policy for this evaluator version')
        if type(self.difficult_course_threshold) is not int or self.difficult_course_threshold < 4:
            raise ValueError('difficult_course_threshold must be an integer >= 4')
        if type(self.free_gap_minutes) is not int or self.free_gap_minutes != 30:
            raise ValueError('this version exempts exactly 30 minutes of each gap')
        if type(self.neutral_time_score) not in (int, float) or not 0 <= self.neutral_time_score <= 1:
            raise ValueError('neutral_time_score must be in [0,1]')
        if self.availability_weight_scale != 10 or self.willingness_scale != 10:
            raise ValueError('input availability/willingness scales are 0..10')
        if self.gap_normalizer_minutes is not None and (
            type(self.gap_normalizer_minutes) is not int or self.gap_normalizer_minutes <= 0
        ):
            raise ValueError('explicit gap normalizer must be a positive integer')


DEFAULT_POLICY = DesignPolicy()
