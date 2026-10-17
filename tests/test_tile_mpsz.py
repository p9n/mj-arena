"""Unit tests for tile representation and MPSZ encoding/decoding."""

from __future__ import annotations

import unittest

from server.tile import (
    dora_from_indicator_token,
    is_aka_tile_id,
    is_aka_token,
    parse_mpsz,
    tile_id_to_token,
    token_to_kind34,
    tokens_to_mpsz,
)


class TileMpszTest(unittest.TestCase):

  def test_aka_dora_tile_ids(self) -> None:
    self.assertEqual(tile_id_to_token(16, aka_dora=True), "0m")
    self.assertEqual(tile_id_to_token(17, aka_dora=True), "5m")
    self.assertEqual(tile_id_to_token(52, aka_dora=True), "0p")
    self.assertEqual(tile_id_to_token(88, aka_dora=True), "0s")
    self.assertEqual(tile_id_to_token(16, aka_dora=False), "5m")
    self.assertTrue(is_aka_tile_id(16, aka_dora=True))
    self.assertFalse(is_aka_tile_id(16, aka_dora=False))
    self.assertTrue(is_aka_token("0m"))
    self.assertFalse(is_aka_token("5m"))

  def test_mpsz_roundtrip(self) -> None:
    mpsz = "12305m406p789s1234567z"
    tokens = parse_mpsz(mpsz)
    self.assertIn("0m", tokens)
    self.assertIn("5m", tokens)
    self.assertIn("0p", tokens)
    self.assertEqual(tokens_to_mpsz(tokens), mpsz)
    self.assertEqual(token_to_kind34("0m"), token_to_kind34("5m"))

  def test_dora_indicators(self) -> None:
    self.assertEqual(dora_from_indicator_token("1m"), "2m")
    self.assertEqual(dora_from_indicator_token("9m"), "1m")
    self.assertEqual(dora_from_indicator_token("0p"), "6p")
    self.assertEqual(dora_from_indicator_token("9s"), "1s")
    # Winds: 東(1z)->南(2z)->西(3z)->北(4z)->東(1z)
    self.assertEqual(dora_from_indicator_token("1z"), "2z")
    self.assertEqual(dora_from_indicator_token("4z"), "1z")
    # Dragons: 白(5z)->發(6z)->中(7z)->白(5z)
    self.assertEqual(dora_from_indicator_token("5z"), "6z")
    self.assertEqual(dora_from_indicator_token("6z"), "7z")
    self.assertEqual(dora_from_indicator_token("7z"), "5z")


if __name__ == "__main__":
  unittest.main()
