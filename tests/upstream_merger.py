"""Regression coverage for the selectively integrated upstream token fix."""

import unittest

from core.server.merger.token_merger import (
    _cut_at_char,
    merge_tokens_by_sequence_matcher,
)


class TokenMergeTests(unittest.TestCase):
    def test_split_preserves_characters_and_timestamp_ownership(self):
        tokens = ["history", "hello", "? ", "next"]
        timestamps = [0, 1, 2, 3]
        for position in range(len("hello? next") + 1):
            with self.subTest(position=position):
                head, head_ts, tail, tail_ts = _cut_at_char(
                    tokens, timestamps, 1, position)
                self.assertEqual("".join(head + tail), "".join(tokens))
                self.assertEqual("".join(head), "history" + "hello? next"[:position])
                self.assertEqual(len(head), len(head_ts))
                self.assertEqual(len(tail), len(tail_ts))
        self.assertEqual(_cut_at_char(tokens, timestamps, 1, 6),
                         (["history", "hello", "?"], [0, 1, 2],
                          [" ", "next"], [2, 3]))
        self.assertEqual(tokens, ["history", "hello", "? ", "next"])

    def test_overlap_does_not_drop_space_inside_new_token(self):
        tail = ["hairpiece", "?", " ", "Wait", ",", " ", "does", " ",
                "he", " ", "eat", " ", "chalk", "?"]
        new = ["Wait", ",", " ", "does", " ", "he", " ", "eat", " ",
               "chalk", "? ", "Just", " ", "continue"]
        for filler in ([], ["prior"] * 70):
            with self.subTest(history=len(filler)):
                previous = filler + tail
                tokens, times = merge_tokens_by_sequence_matcher(
                    previous, list(range(len(previous))), new,
                    [i * .1 for i in range(len(new))], offset=100, overlap=4)
                self.assertEqual("".join(tokens),
                                 "".join(previous) + " Just continue")
                self.assertEqual(tokens[:len(filler)], filler)
                self.assertEqual(tokens.count("chalk"), 1)
                self.assertEqual(len(tokens), len(times))
                self.assertAlmostEqual(times[-1], 101.3)

    def test_cut_inside_previous_token_does_not_duplicate_suffix(self):
        tokens, times = merge_tokens_by_sequence_matcher(
            ["prefix ", "hello? OLD"], [0, 1],
            ["hello", "? NEW"], [0, 1], offset=2, overlap=4)
        self.assertEqual("".join(tokens), "prefix hello? NEW")
        self.assertEqual(len(tokens), len(times))

    def test_first_and_empty_segments_preserve_global_timestamps(self):
        self.assertEqual(merge_tokens_by_sequence_matcher(
            [], [], ["hello"], [.5], 10, 4), (["hello"], [10.5]))
        self.assertEqual(merge_tokens_by_sequence_matcher(
            ["hello"], [1], [], [], 10, 4), (["hello"], [1]))

    def test_no_overlap_uses_timestamp_fallback(self):
        self.assertEqual(merge_tokens_by_sequence_matcher(
            ["old"], [10], ["new", "tail"], [0, 1], 10, 4),
            (["old", "tail"], [10, 11]))
