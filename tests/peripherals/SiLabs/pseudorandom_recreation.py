"""Verify the sibling RNG fix already present in the compiled 26q3 baseline."""
import unittest

import test_lib
from Antmicro.Renode.Core import PseudorandomNumberGenerator
from System import Random
from System.Threading import Thread, ThreadStart


class RandomRecreationRegression(unittest.TestCase):
    def setUp(self):
        self.rng = PseudorandomNumberGenerator()
        self.rng.ResetSeed(0x2603)

    def draw(self, name, count):
        values, errors = [], []
        def run():
            try:
                values.extend(int(self.rng.Next()) for _ in range(count))
            except Exception as error:
                errors.append(str(error))
        thread = Thread(ThreadStart(run))
        thread.Name = name
        thread.IsBackground = True
        thread.Start()
        self.assertTrue(thread.Join(5000), 'Named CLR worker did not finish')
        self.assertFalse(errors, errors)
        return values

    def test_recreated_thread_continues_same_stream(self):
        first = self.draw('sequencer-regression', 3)
        second = self.draw('sequencer-regression', 3)
        expected = Random(sum(map(ord, 'sequencer-regression')) ^ 0x2603)
        self.assertEqual(first + second, [int(expected.Next()) for _ in range(6)])

    def test_reset_seed_replays_stream(self):
        expected = self.draw('reset-worker', 5)
        self.draw('reset-worker', 5)
        self.rng.ResetSeed(0x2603)
        self.assertEqual(self.draw('reset-worker', 5), expected)

    def test_named_workers_have_independent_streams(self):
        for name in ('worker-alpha', 'worker-beta'):
            expected = Random(sum(map(ord, name)) ^ 0x2603)
            self.assertEqual(self.draw(name, 4), [int(expected.Next()) for _ in range(4)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
