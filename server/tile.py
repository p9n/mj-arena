"""Tile representation, 136-tile / 34-kind utilities, and MPSZ notation."""

from __future__ import annotations

from typing import Iterable

SUITS = ("m", "p", "s", "z")

# Red five physical tile IDs (one per number suit: copy 0 of 5m, 5p, 5s)
AKA_TILE_IDS = frozenset({16, 52, 88})  # 4*4=16 (5m), 13*4=52 (5p), 22*4=88 (5s)

TERMINAL_KINDS = frozenset({0, 8, 9, 17, 18, 26})
WIND_KINDS = frozenset({27, 28, 29, 30})  # 東, 南, 西, 北
DRAGON_KINDS = frozenset({31, 32, 33})  # 白, 發, 中
HONOR_KINDS = WIND_KINDS | DRAGON_KINDS
YAOCHUU_KINDS = TERMINAL_KINDS | HONOR_KINDS
# 2s(19), 3s(20), 4s(21), 6s(23), 8s(25), 發(32)
GREEN_KINDS = frozenset({19, 20, 21, 23, 25, 32})

WIND_NAMES = {0: "E", 1: "S", 2: "W", 3: "N"}
WIND_TO_KIND34 = {"E": 27, "S": 28, "W": 29, "N": 30}


def is_aka_tile_id(tile_id: int, aka_dora: bool = True) -> bool:
  """Return True if the 136-tile ID is a red five under current rules."""
  return aka_dora and (tile_id in AKA_TILE_IDS)


def tile_id_to_kind34(tile_id: int) -> int:
  """Convert a physical tile ID (0..135) to a 34-tile kind index (0..33)."""
  if not (0 <= tile_id < 136):
    raise ValueError(f"Invalid tile_id: {tile_id}")
  return tile_id // 4


def kind34_to_token(kind34: int) -> str:
  """Convert a 34-tile kind index (0..33) to a non-red MPSZ token (e.g. '5m', '1z')."""
  if not (0 <= kind34 < 34):
    raise ValueError(f"Invalid kind34: {kind34}")
  suit_idx = kind34 // 9
  num = (kind34 % 9) + 1
  return f"{num}{SUITS[suit_idx]}"


def tile_id_to_token(tile_id: int, aka_dora: bool = True) -> str:
  """Convert a physical tile ID (0..135) to an MPSZ token ('0m' for red five)."""
  if is_aka_tile_id(tile_id, aka_dora=aka_dora):
    suit_idx = (tile_id // 4) // 9
    return f"0{SUITS[suit_idx]}"
  return kind34_to_token(tile_id // 4)


def is_valid_token(token: str) -> bool:
  """Return True if token is a valid single-tile MPSZ token."""
  if not isinstance(token, str) or len(token) != 2:
    return False
  num_ch, suit_ch = token[0], token[1]
  if suit_ch in ("m", "p", "s"):
    return num_ch in "0123456789"
  if suit_ch == "z":
    return num_ch in "1234567"
  return False


def is_aka_token(token: str) -> bool:
  """Return True if token represents a red five ('0m', '0p', '0s')."""
  return token in ("0m", "0p", "0s")


def token_to_kind34(token: str) -> int:
  """Convert an MPSZ single-tile token (e.g. '0m', '5m', '7z') to kind34 (0..33)."""
  if not is_valid_token(token):
    raise ValueError(f"Invalid MPSZ token: {token!r}")
  num_ch, suit_ch = token[0], token[1]
  suit_idx = SUITS.index(suit_ch)
  num = 5 if num_ch == "0" else int(num_ch)
  return suit_idx * 9 + (num - 1)


def normalize_token(token: str) -> str:
  """Normalize red-five token ('0m' -> '5m') while keeping other tokens unchanged."""
  return kind34_to_token(token_to_kind34(token))


def token_sort_key(token: str) -> tuple[int, int, int]:
  """Sort key for MPSZ tokens: suit (m,p,s,z), number (1..9), red (0 before 5)."""
  kind = token_to_kind34(token)
  suit_idx = kind // 9
  num = (kind % 9) + 1
  is_non_red = 0 if is_aka_token(token) else 1
  return (suit_idx, num, is_non_red)


def sort_tokens(tokens: Iterable[str]) -> list[str]:
  """Return sorted list of MPSZ single-tile tokens."""
  return sorted(tokens, key=token_sort_key)


def tokens_to_mpsz(tokens: Iterable[str]) -> str:
  """Format a collection of MPSZ single-tile tokens into a compact MPSZ string."""
  sorted_toks = sort_tokens(tokens)
  by_suit: dict[str, list[str]] = {"m": [], "p": [], "s": [], "z": []}
  for tok in sorted_toks:
    by_suit[tok[1]].append(tok[0])
  parts: list[str] = []
  for suit in SUITS:
    if by_suit[suit]:
      parts.append("".join(by_suit[suit]) + suit)
  return "".join(parts)


def tile_ids_to_mpsz(tile_ids: Iterable[int], aka_dora: bool = True) -> str:
  """Format a collection of physical tile IDs into a compact MPSZ string."""
  return tokens_to_mpsz(
      tile_id_to_token(tid, aka_dora=aka_dora) for tid in tile_ids
  )


def parse_mpsz(mpsz: str) -> list[str]:
  """Parse a compact MPSZ string (e.g. '123m406p789s1122z') into single-tile tokens."""
  tokens: list[str] = []
  pending_digits: list[str] = []
  for ch in mpsz.strip():
    if ch.isdigit():
      pending_digits.append(ch)
    elif ch in SUITS:
      if not pending_digits:
        raise ValueError(f"Suit {ch!r} without preceding digits in {mpsz!r}")
      for d in pending_digits:
        tok = f"{d}{ch}"
        if not is_valid_token(tok):
          raise ValueError(f"Invalid tile {tok!r} in {mpsz!r}")
        tokens.append(tok)
      pending_digits.clear()
    else:
      raise ValueError(f"Unexpected character {ch!r} in MPSZ string {mpsz!r}")
  if pending_digits:
    raise ValueError(f"Trailing digits without suit in MPSZ string {mpsz!r}")
  return tokens


def dora_from_indicator_kind34(indicator_kind34: int) -> int:
  """Return the dora tile kind34 indicated by a dora indicator kind34."""
  if not (0 <= indicator_kind34 < 34):
    raise ValueError(f"Invalid indicator_kind34: {indicator_kind34}")
  if indicator_kind34 < 27:
    suit_base = (indicator_kind34 // 9) * 9
    offset = (indicator_kind34 - suit_base + 1) % 9
    return suit_base + offset
  if indicator_kind34 in WIND_KINDS:
    return 27 + ((indicator_kind34 - 27 + 1) % 4)
  # Dragons: 白(31) -> 發(32) -> 中(33) -> 白(31)
  return 31 + ((indicator_kind34 - 31 + 1) % 3)


def dora_from_indicator_token(indicator_token: str) -> str:
  """Return the non-red MPSZ token of the dora indicated by indicator_token."""
  return kind34_to_token(
      dora_from_indicator_kind34(token_to_kind34(indicator_token))
  )
