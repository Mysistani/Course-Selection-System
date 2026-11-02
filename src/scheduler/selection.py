"""Hard-first tournament selection on evaluated Phase 4 population members."""
from __future__ import annotations

from collections.abc import Sequence
from random import Random

from .fitness import FitnessEvaluation
from .initialization import (InitializedIndividual, PopulationInitialization,
                             chromosome_fingerprint)


def tournament_selection(
    population: Sequence[InitializedIndividual] | PopulationInitialization,
    rng: Random,
    tournament_size: int = 3,
    *,
    allow_mixed_contexts: bool = False,
) -> InitializedIndividual:
    """Return an existing member without changing it or reevaluating its fitness.

    Sampling is without replacement by population index. Complete fitness ties
    are broken uniformly using the supplied local RNG. Two calls are independent
    and may select the same member. Context checks cover the entire population;
    sampled members must still match their evaluated genotype fingerprints.
    """
    if not isinstance(rng, Random):
        raise TypeError('rng must be a local random.Random instance')
    if isinstance(population, PopulationInitialization):
        members = population.individuals
    elif isinstance(population, Sequence) and not isinstance(population, (str, bytes)):
        members = population
    else:
        raise TypeError('population must be an evaluated sequence or PopulationInitialization')
    if not members:
        raise ValueError('tournament selection requires a non-empty population')
    if type(tournament_size) is not int or not 1 <= tournament_size <= len(members):
        raise ValueError('tournament_size must be an integer between 1 and population size')
    if type(allow_mixed_contexts) is not bool:
        raise TypeError('allow_mixed_contexts must be a Boolean')
    if any(not isinstance(member, InitializedIndividual) for member in members):
        raise TypeError('every population member must be an evaluated InitializedIndividual')
    if any(not isinstance(member.evaluation, FitnessEvaluation) for member in members):
        raise TypeError('every population member must have a FitnessEvaluation')
    contexts = {member.evaluation.hard.context_id for member in members}
    if not allow_mixed_contexts and len(contexts) != 1:
        raise ValueError('population contains fitness results from different EvaluationContexts')
    candidates = rng.sample(members, tournament_size)
    for member in candidates:
        if chromosome_fingerprint(member.chromosome) != member.fingerprint:
            raise ValueError('sampled chromosome changed since evaluation; reevaluate it before selection')
    best_key = min(member.evaluation.comparison_key for member in candidates)
    tied = [member for member in candidates if member.evaluation.comparison_key == best_key]
    return rng.choice(tied)
