"""Whole-course crossover: sections and enrollments always share one donor."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from random import Random

from .chromosome import Chromosome, validate_structure
from .constraints import StructuralValidationError
from .evaluation_context import EvaluationContext


def course_based_crossover(
    parent_a: Chromosome,
    parent_b: Chromosome,
    context: EvaluationContext,
    rng: Random,
    *,
    ensure_mixed: bool = True,
) -> Chromosome:
    """Copy one complete donor block per course into fresh temporary mappings.

    With >=2 genuinely differing blocks, ensure_mixed makes both donors
    contribute a differing block. If the initial donor mask is uniform, flip
    one randomly selected mask bit; no gene is edited and no repair is run.
    Zero/one differing block can legitimately produce a copy of a parent.
    Fitness is deliberately not evaluated or used to accept/reject the child.
    """
    if not isinstance(context, EvaluationContext):
        raise TypeError('context must be a shared EvaluationContext')
    if not isinstance(rng, Random):
        raise TypeError('rng must be a local random.Random instance')
    if type(ensure_mixed) is not bool:
        raise TypeError('ensure_mixed must be a Boolean')
    for name, parent in (('parent_a', parent_a), ('parent_b', parent_b)):
        if not isinstance(parent, Chromosome):
            raise TypeError(f'{name} must be a Chromosome')
        issues = validate_structure(parent, context)
        if issues:
            error = StructuralValidationError(issues)
            error.add_note(f'Invalid {name}; crossover cannot repair it')
            raise error

    courses = tuple(sorted(context.courses))
    section_keys, request_keys = defaultdict(list), defaultdict(list)
    for sid, cid in sorted(context.expected_sections.items()):
        section_keys[cid].append(sid)
    for request in sorted(context.requests):
        request_keys[request[1]].append(request)

    def section_value(section):
        # Meeting list order alone is not a difference in the weekly timetable.
        return (section.active, section.professor_id, section.room_id,
                tuple(sorted(section.meeting_slots)))

    differing = [cid for cid in courses if
                 any(section_value(parent_a.sections[sid]) != section_value(parent_b.sections[sid])
                     for sid in section_keys[cid]) or
                 any(parent_a.enrollments[key] != parent_b.enrollments[key] for key in request_keys[cid])]
    donors = {cid: rng.randrange(2) for cid in courses}
    if ensure_mixed and len(differing) >= 2 and len({donors[cid] for cid in differing}) == 1:
        cid = rng.choice(differing)
        donors[cid] = 1 - donors[cid]

    sections, enrollments = {}, {}
    for cid in courses:
        donor = parent_a if donors[cid] == 0 else parent_b
        sections.update((sid, deepcopy(donor.sections[sid])) for sid in section_keys[cid])
        enrollments.update((key, donor.enrollments[key]) for key in request_keys[cid])
    child = Chromosome(sections, enrollments)
    issues = validate_structure(child, context)
    if issues:
        raise StructuralValidationError(issues)
    return child
