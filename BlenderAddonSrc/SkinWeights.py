"""Deterministic conversion of logical skin weights to uint8 records."""

from __future__ import annotations

import math


DEFAULT_WEIGHT_TARGET_SUM = 256
SUPPORTED_WEIGHT_TARGET_SUMS = (255, 256)


def quantize_skin_weights(
    influences: list[tuple[int, float]],
    target_sum: int = DEFAULT_WEIGHT_TARGET_SUM,
) -> list[tuple[int, int]]:
    """Normalize positive influences and allocate exactly target_sum byte units.

    Output preserves stable influence order. A 256-unit influence is emitted as
    duplicate physical records ``255 + 1`` because one record is uint8.
    """
    if target_sum not in SUPPORTED_WEIGHT_TARGET_SUMS:
        raise ValueError(f'weight target sum must be 255 or 256, got {target_sum}')

    positive = []
    for order, (bone_id, weight) in enumerate(influences):
        value = float(weight)
        if not math.isfinite(value):
            raise ValueError(f'bone {bone_id} has non-finite skin weight {value}')
        if value > 0:
            positive.append((int(bone_id), value, order))
    if not positive:
        raise ValueError('vertex has no positive skin influences')

    total = sum(weight for _bone_id, weight, _order in positive)
    scaled = [weight * target_sum / total for _bone_id, weight, _order in positive]
    units = [math.floor(value) for value in scaled]
    missing = target_sum - sum(units)
    remainder_order = sorted(
        range(len(positive)),
        key=lambda index: (
            -(scaled[index] - units[index]),
            positive[index][0],
            positive[index][2],
        ),
    )
    for index in remainder_order[:missing]:
        units[index] += 1

    result = []
    for (bone_id, _weight, _order), allocated in zip(positive, units):
        while allocated > 255:
            result.append((bone_id, 255))
            allocated -= 255
        if allocated:
            result.append((bone_id, allocated))
    return result