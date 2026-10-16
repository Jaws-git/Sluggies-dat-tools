import os
import tempfile
import unittest
from unittest import mock

from SluggiesTools import gecko_compat
from SluggiesTools.Dol import dolfile
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.tests.test_game_options import synthetic_dol

STOCK = synthetic_dol()


def with_sections(stock: bytes = STOCK) -> bytes:
    image = dolfile.DolImage(stock)
    dhs.DolHammerspace.create(image)
    return image.to_bytes()


def with_options() -> bytes:
    image = dolfile.DolImage(STOCK)
    go.apply(image, ['cpu_vs_cpu'])
    return image.to_bytes()


class StatusTests(unittest.TestCase):
    def test_stock_is_compatible(self):
        status = gecko_compat.status(STOCK)
        self.assertTrue(status.compatible)
        self.assertIn('stock', status.detail)

    def test_sections_are_not(self):
        status = gecko_compat.status(with_options())
        self.assertFalse(status.compatible)
        self.assertIn('game option code', status.detail)
        self.assertIn('arena start 0x807C0020', status.detail)    # raised past the sections

    def test_moved_arena_without_sections(self):
        image = dolfile.DolImage(STOCK)
        dhs.set_arena_low(image, 0x807C1000)
        status = gecko_compat.status(image.to_bytes())
        self.assertFalse(status.compatible)
        self.assertIn('another DOL extension', status.detail)

    def test_unreadable(self):
        self.assertFalse(gecko_compat.status(b'\0' * 16).compatible)

    def test_breaks(self):
        broken = with_sections()
        self.assertTrue(gecko_compat.breaks(STOCK, broken))
        self.assertFalse(gecko_compat.breaks(STOCK, STOCK))
        self.assertFalse(gecko_compat.breaks(broken, broken))   # already incompatible: nothing left to break
        self.assertFalse(gecko_compat.breaks(broken, STOCK))

    def test_status_of_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'main.dol')
            self.assertIsNone(gecko_compat.status_of_file(path))
            with open(path, 'wb') as f:
                f.write(STOCK)
            self.assertTrue(gecko_compat.status_of_file(path).compatible)


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(gecko_compat.ACCEPT_ENV, None)

    def ask(self, **kwargs):
        return self.enterContext(mock.patch.object(gecko_compat.slogger, 'ask', **kwargs))

    def test_yes_accepts_for_the_children(self):
        ask = self.ask(return_value='Y')
        self.assertTrue(gecko_compat.confirm('x'))
        self.assertEqual(ask.call_args.kwargs['kind'], 'danger')
        self.assertTrue(gecko_compat.accepted())
        self.assertTrue(gecko_compat.confirm('x'))
        ask.assert_called_once()                         # accepted: not asked again

    def test_no_and_closed_input(self):
        self.ask(return_value='n')
        self.assertFalse(gecko_compat.confirm('x'))
        self.ask(side_effect=EOFError)
        self.assertFalse(gecko_compat.confirm('x'))
        self.assertFalse(gecko_compat.accepted())


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(gecko_compat.ACCEPT_ENV, None)
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        os.makedirs(os.path.join(self.root, gecko_compat.OUTPUT_FOLDER))
        self.write(STOCK)
        self.after = {}                                  # command -> bytes it would leave
        self.enterContext(mock.patch.object(gecko_compat, '_after',
                                            side_effect=lambda args, _root: self.after.get(args)))
        self.ask = self.enterContext(mock.patch.object(gecko_compat.slogger, 'ask', return_value='n'))

    def write(self, data):
        with open(os.path.join(self.root, gecko_compat.OUTPUT_FOLDER, 'main.dol'), 'wb') as f:
            f.write(data)

    def test_chain_breaks(self):
        commands = [('--apply-stat-edits', 'a.json'), ('--roster', '--state', 's.json'), ('--roster-state',)]
        self.assertFalse(gecko_compat.chain_breaks(commands, self.root))
        self.after[commands[1]] = with_sections()
        self.assertTrue(gecko_compat.chain_breaks(commands, self.root))
        self.write(with_sections())
        self.assertFalse(gecko_compat.chain_breaks(commands, self.root))   # incompatible already

    def test_failed_build_does_not_count(self):
        gecko_compat._after.side_effect = dolfile.DolError('boom')
        self.assertFalse(gecko_compat.chain_breaks([('--roster', '--state', 's.json')], self.root))

    def test_guard_chain(self):
        commands = [('--roster', '--state', 's.json')]
        self.after[commands[0]] = with_sections()
        self.assertTrue(gecko_compat.guard_chain(commands, 'the slot edits', self.root, dry_run=True))
        self.ask.assert_not_called()
        self.assertTrue(gecko_compat.read_check(self.root))
        gecko_compat.clear_check(self.root)
        self.assertFalse(gecko_compat.read_check(self.root))
        self.assertFalse(gecko_compat.guard_chain(commands, 'the slot edits', self.root))
        self.ask.assert_called_once()
        gecko_compat.accept()
        self.assertTrue(gecko_compat.guard_chain(commands, 'the slot edits', self.root))
        self.ask.assert_called_once()

    def test_option_values(self):
        args = ('--game-options', '--on', 'cpu_vs_cpu', 'cpu_management', '--off', 'player_heap_32')
        self.assertEqual(gecko_compat._option(args, '--on'), ['cpu_vs_cpu', 'cpu_management'])
        self.assertEqual(gecko_compat._option(args, '--off'), ['player_heap_32'])
        self.assertEqual(gecko_compat._option(args, '--state'), [])


class SlotProbeTests(unittest.TestCase):
    """``slot_cli.gecko_check``: the dry run's record, and the new edits planned alone."""

    def setUp(self):
        from SluggiesTools.Roster import slot_cli, slot_plan
        self.slot_cli, self.slot_plan = slot_cli, slot_plan
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.out = os.path.join(self.root, gecko_compat.OUTPUT_FOLDER)
        self.breaks = {}                                 # first command -> chain_breaks answer
        self.enterContext(mock.patch.object(gecko_compat, 'chain_breaks',
                                            side_effect=lambda commands, _root: self.breaks.get(commands[0], False)))
        self.enterContext(mock.patch.object(gecko_compat.slogger, 'warning'))
        self.probe = self.enterContext(mock.patch.object(slot_cli, '_plan_into'))

    def batch(self, first, refused=()):
        return mock.Mock(commands=[first], refused=list(refused))

    def edit(self, checked):
        return self.slot_plan.Edit('stats', 0x01, index=1, checked=checked)

    def check(self, edits, batch):
        self.slot_cli.gecko_check(edits, batch, (), self.out)
        return gecko_compat.read_details(self.root)

    def test_no_break_clears_the_record(self):
        gecko_compat.record(self.root, 'old')
        self.assertEqual(self.check([self.edit(False)], self.batch(('a',))), {})
        self.probe.assert_not_called()

    def test_only_new_edits(self):
        self.breaks[('a',)] = True
        self.assertTrue(self.check([self.edit(False)], self.batch(('a',)))['new_breaks'])
        self.probe.assert_not_called()

    def test_new_edit_planned_alone(self):
        self.breaks[('a',)] = True
        self.probe.return_value = self.batch(('b',))
        edits = [self.edit(True), self.edit(False)]
        self.assertIs(self.check(edits, self.batch(('a',)))['new_breaks'], False)
        self.assertEqual(self.probe.call_args.args[1], edits[1:])
        self.assertTrue(self.probe.call_args.args[3].endswith(self.slot_cli.PROBE_DIR))
        self.breaks[('b',)] = True
        self.assertIs(self.check(edits, self.batch(('a',)))['new_breaks'], True)
        self.probe.return_value = self.batch(('b',), refused=['x'])
        self.assertIsNone(self.check(edits, self.batch(('a',)))['new_breaks'])
        self.probe.side_effect = self.slot_plan.PlanError('no')
        self.assertIsNone(self.check(edits, self.batch(('a',)))['new_breaks'])


class AfterTests(unittest.TestCase):
    """``_after`` builds game option commands for real (the roster runner has its own tests)."""

    def test_game_options_and_others(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, gecko_compat.OUTPUT_FOLDER))
            with open(os.path.join(root, gecko_compat.OUTPUT_FOLDER, 'main.dol'), 'wb') as f:
                f.write(STOCK)
            after = gecko_compat._after(('--game-options', '--on', 'cpu_vs_cpu'), root)
            self.assertFalse(gecko_compat.status(after).compatible)
            self.assertIsNone(gecko_compat._after(('--game-options', '--off', 'cpu_vs_cpu'), root))
            self.assertIsNone(gecko_compat._after(('--roster', '--remove'), root))
            self.assertIsNone(gecko_compat._after(('--patch', 'x.sluggie'), root))


if __name__ == '__main__':
    unittest.main()
