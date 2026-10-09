"""Shared compaction helper for per-loop coordinate arrays (normals, UVs, colors).

Used by both the hammerspace geometry rebuild and the in-place patcher to
deduplicate expanded per-loop arrays back to compact indexed form against the
donor layout.
"""


def compact_channel(what: str, stride: int, original: bytes,
                    original_indices: list[int], expanded: bytes,
                    expanded_indices: list[int], loop_count: int):
    """Deduplicate a per-loop-expanded coordinate array against the donor layout.

    ``expanded`` holds one record per loop of the edited topology and
    ``expanded_indices`` maps each loop to that record.  ``original`` is the
    donor's compact array and ``original_indices`` maps each loop to its slot.

    Returns ``(compact, new_indices, preserve_indices)``.  Donor slot order
    is preserved: a loop whose edited record matches its donor slot's record
    keeps that slot, and a donor slot shared by loops with different edited
    values is reassigned to the first such value when no loop still needs
    the original record while further values are appended after the donor
    slots.
    """
    if len(original) % stride or len(expanded) % stride:
        raise ValueError(
            f'{what}: payload length is not divisible by stride {stride}')
    if len(original_indices) != loop_count or len(expanded_indices) != loop_count:
        raise ValueError(
            f'{what}: expected {loop_count} loop indices, '
            f'got donor={len(original_indices)}, edited={len(expanded_indices)}')
    expanded_count = len(expanded) // stride
    donor_count = len(original) // stride
    if any(index >= expanded_count for index in expanded_indices):
        raise ValueError(
            f'{what}: edited loop index exceeds coordinate count {expanded_count}')
    if any(index >= donor_count for index in original_indices):
        raise ValueError(
            f'{what}: donor loop index {max(original_indices)} '
            f'exceeds coordinate count {donor_count}')
    if not donor_count:
        return b'', list(expanded_indices), False

    edited_records = [
        expanded[index * stride:(index + 1) * stride]
        for index in expanded_indices
    ]

    donor_slots = [None] * donor_count
    conflict = False
    for donor_index, record in zip(original_indices, edited_records):
        if donor_slots[donor_index] is None:
            donor_slots[donor_index] = record
        elif donor_slots[donor_index] != record:
            conflict = True
            break

    if not conflict:
        for index, record in enumerate(donor_slots):
            if record is None:
                donor_slots[index] = original[index * stride:(index + 1) * stride]
        compact = b''.join(donor_slots)
        return compact, list(original_indices), True

    compact_records = [
        original[index * stride:(index + 1) * stride]
        for index in range(donor_count)
    ]
    split_indices = {}
    new_indices = []
    for donor_index, record in zip(original_indices, edited_records):
        donor_record = compact_records[donor_index]
        if record == donor_record:
            new_indices.append(donor_index)
            continue
        split_key = (donor_index, record)
        if split_key not in split_indices:
            # Reuse the donor slot for the first edited value only when no
            # loop still needs its original value.
            slot_records = {
                candidate
                for index, candidate in zip(original_indices, edited_records)
                if index == donor_index
            }
            if donor_record not in slot_records and not any(
                key[0] == donor_index for key in split_indices
            ):
                compact_records[donor_index] = record
                split_indices[split_key] = donor_index
            else:
                split_indices[split_key] = len(compact_records)
                compact_records.append(record)
        new_indices.append(split_indices[split_key])
    if len(compact_records) > 0xFFFF:
        raise ValueError(
            f'{what}: compact coordinate count {len(compact_records)} exceeds uint16')
    return (
        b''.join(compact_records),
        new_indices,
        new_indices == list(original_indices),
    )


def compact_channel_fixed_slots(what: str, stride: int, original: bytes,
                                original_indices: list[int], expanded: bytes,
                                expanded_indices: list[int], loop_count: int):
    """Like ``compact_channel`` but never adds, drops or re-maps a slot.

    For arrays whose slots are pinned to another array's slots -- the
    interleaved normals of a skinned (CompCount 6) submesh live inside the
    position buffer at ``pos + 6`` and are indexed by the position index --
    the compact array must keep exactly the donor's slot count and every loop
    must keep its donor slot.  Each slot takes the edited record most of its
    loops agree on (first seen wins a tie); slots no loop references keep
    their donor record.

    Returns ``(compact, indices, conflict_slots)``: ``compact`` has the
    donor's length, ``indices`` is a copy of ``original_indices`` and
    ``conflict_slots`` is the number of slots whose loops asked for more than
    one value (those lost a split the layout cannot express).
    """
    if len(original) % stride or len(expanded) % stride:
        raise ValueError(
            f'{what}: payload length is not divisible by stride {stride}')
    if len(original_indices) != loop_count or len(expanded_indices) != loop_count:
        raise ValueError(
            f'{what}: expected {loop_count} loop indices, '
            f'got donor={len(original_indices)}, edited={len(expanded_indices)}')
    expanded_count = len(expanded) // stride
    donor_count = len(original) // stride
    if any(index >= expanded_count for index in expanded_indices):
        raise ValueError(
            f'{what}: edited loop index exceeds coordinate count {expanded_count}')
    if any(index >= donor_count for index in original_indices):
        raise ValueError(
            f'{what}: donor loop index {max(original_indices)} '
            f'exceeds coordinate count {donor_count}')

    # slot -> {record: [first_seen_order, votes]}
    votes_by_slot: list[dict | None] = [None] * donor_count
    for donor_index, expanded_index in zip(original_indices, expanded_indices):
        record = expanded[expanded_index * stride:(expanded_index + 1) * stride]
        votes = votes_by_slot[donor_index]
        if votes is None:
            votes = votes_by_slot[donor_index] = {}
        entry = votes.get(record)
        if entry is None:
            votes[record] = [len(votes), 1]
        else:
            entry[1] += 1

    compact_records = []
    conflict_slots = 0
    for slot in range(donor_count):
        votes = votes_by_slot[slot]
        if not votes:
            compact_records.append(original[slot * stride:(slot + 1) * stride])
            continue
        if len(votes) > 1:
            conflict_slots += 1
        winner = min(votes.items(), key=lambda item: (-item[1][1], item[1][0]))[0]
        compact_records.append(winner)
    return b''.join(compact_records), list(original_indices), conflict_slots
