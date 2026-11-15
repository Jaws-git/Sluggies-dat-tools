"""``compact_channel``: the split of donor slots that loops ask different values of. The lookups it needs are
built once now; the result has to be that of the per-pair scan it replaced, and the time no longer quadratic."""
import pathlib
import random
import sys
import time
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from compact_channel import compact_channel  # noqa: E402

STRIDE = 4


def _reference(original, original_indices, expanded, expanded_indices):
    """The conflict branch as it was: both lookups rebuilt for every new (slot, value) pair."""
    donor_count = len(original) // STRIDE
    edited_records = [expanded[i * STRIDE:(i + 1) * STRIDE] for i in expanded_indices]
    compact_records = [original[i * STRIDE:(i + 1) * STRIDE] for i in range(donor_count)]
    split_indices, new_indices = {}, []
    for donor_index, record in zip(original_indices, edited_records):
        donor_record = compact_records[donor_index]
        if record == donor_record:
            new_indices.append(donor_index)
            continue
        split_key = (donor_index, record)
        if split_key not in split_indices:
            slot_records = {c for i, c in zip(original_indices, edited_records) if i == donor_index}
            if donor_record not in slot_records and not any(k[0] == donor_index for k in split_indices):
                compact_records[donor_index] = record
                split_indices[split_key] = donor_index
            else:
                split_indices[split_key] = len(compact_records)
                compact_records.append(record)
        new_indices.append(split_indices[split_key])
    return b''.join(compact_records), new_indices, new_indices == list(original_indices)


def _record(value):
    return value.to_bytes(STRIDE, 'big')


class CompactChannelTests(unittest.TestCase):
    def test_conflicting_slots_split_as_before(self):
        rng = random.Random(1215)
        for case in range(200):
            slots = rng.randrange(1, 12)
            loops = rng.randrange(1, 60)
            values = rng.randrange(2, 8)            # few values: many repeats, reuses and conflicts
            original = b''.join(_record(rng.randrange(values)) for _ in range(slots))
            original_indices = [rng.randrange(slots) for _ in range(loops)]
            expanded = b''.join(_record(rng.randrange(values)) for _ in range(loops))
            expanded_indices = list(range(loops))
            got = compact_channel('test', STRIDE, original, original_indices, expanded, expanded_indices, loops)
            donor_slots = {}
            conflict = any(donor_slots.setdefault(slot, expanded[i * STRIDE:(i + 1) * STRIDE])
                           != expanded[i * STRIDE:(i + 1) * STRIDE] for i, slot in enumerate(original_indices))
            if conflict:
                self.assertEqual(got, _reference(original, original_indices, expanded, expanded_indices), case)
            # either way every loop reads the value it asked for
            compact, indices, _preserved = got
            for loop, index in enumerate(indices):
                self.assertEqual(compact[index * STRIDE:(index + 1) * STRIDE],
                                 expanded[loop * STRIDE:(loop + 1) * STRIDE], (case, loop))

    def test_every_loop_with_its_own_value_is_not_quadratic(self):
        """6,000 slots and 36,000 loops took 42 s (measured 2026-11-12); now well under a second."""
        slots, loops = 6000, 36000
        original = b''.join(_record(0xFFFF0000 + i) for i in range(slots))
        original_indices = [i % slots for i in range(loops)]
        expanded = b''.join(_record(i) for i in range(loops))
        started = time.perf_counter()
        compact, indices, preserved = compact_channel(
            'test', STRIDE, original, original_indices, expanded, list(range(loops)), loops)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0)
        self.assertFalse(preserved)
        self.assertEqual(len(compact) // STRIDE, loops)           # the first value reuses its slot, the rest append
        self.assertEqual(indices[:slots], list(range(slots)))


if __name__ == '__main__':
    unittest.main()
