"""Hand state, melds (副露), kuikae (喰い替え) check, and tenpai/wait calculation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from server.tile import (
    SUITS,
    YAOCHUU_KINDS,
    kind34_to_token,
    sort_tokens,
    token_to_kind34,
    tokens_to_mpsz,
)


@dataclass
class Meld:
  """Represents a called meld or closed/added kan."""

  meld_type: str  # "chi", "pon", "daiminkan", "ankan", "kakan"
  tiles: list[str]  # MPSZ single-tile tokens in this meld
  called_tile: str | None = None
  from_seat: int | None = None

  @property
  def is_open(self) -> bool:
    return self.meld_type != "ankan"

  @property
  def is_kan(self) -> bool:
    return self.meld_type in ("daiminkan", "ankan", "kakan")

  @property
  def kinds34(self) -> tuple[int, ...]:
    return tuple(sorted(token_to_kind34(t) for t in self.tiles))

  def to_mpsz(self) -> str:
    return tokens_to_mpsz(self.tiles)

  def to_dict(self) -> dict[str, object]:
    return {
        "meld_type": self.meld_type,
        "tiles": list(self.tiles),
        "called_tile": self.called_tile,
        "from_seat": self.from_seat,
        "mpsz": self.to_mpsz(),
    }


def get_kuikae_forbidden_kinds(
    meld_type: str, called_token: str, hand_tokens: Sequence[str]
) -> set[int]:
  """Return the set of kind34 indices forbidden for immediate discard due to kuikae (喰い替え).

  - Both 'chi' and 'pon' forbid discarding the same kind34 as called_token (現物喰い替え).
  - 'chi' also forbids discarding the opposite suji tile (筋喰い替え) when the two hand
    tiles form a two-sided sequence (ryanmen).
  """
  called_kind = token_to_kind34(called_token)
  forbidden: set[int] = {called_kind}
  if meld_type == "chi" and len(hand_tokens) == 2:
    k1, k2 = sorted(token_to_kind34(t) for t in hand_tokens)
    suit_base = (called_kind // 9) * 9
    if k2 - k1 == 1:
      if called_kind == k1 - 1 and (k2 + 1) < suit_base + 9:
        forbidden.add(k2 + 1)
      elif called_kind == k2 + 1 and (k1 - 1) >= suit_base:
        forbidden.add(k1 - 1)
  return forbidden


def kinds_to_all_tokens(kinds: Iterable[int]) -> list[str]:
  """Convert a set of kind34 indices into all matching MPSZ tokens (including red fives)."""
  tokens: list[str] = []
  for k in sorted(kinds):
    tok = kind34_to_token(k)
    if k in (4, 13, 22):
      suit = SUITS[k // 9]
      tokens.append(f"0{suit}")
    tokens.append(tok)
  return sort_tokens(tokens)


def tokens_to_counts34(tokens: Sequence[str]) -> list[int]:
  """Convert a sequence of MPSZ tokens into a 34-element count array."""
  counts = [0] * 34
  for t in tokens:
    counts[token_to_kind34(t)] += 1
  return counts


def _can_form_mentsu(counts: list[int], idx: int, remaining_mentsu: int) -> bool:
  """Recursively check if `counts` can be partitioned into `remaining_mentsu` melds."""
  while idx < 34 and counts[idx] == 0:
    idx += 1
  if idx >= 34:
    return remaining_mentsu == 0
  if remaining_mentsu <= 0:
    return False

  # Try triplet (koutsu)
  if counts[idx] >= 3:
    counts[idx] -= 3
    if _can_form_mentsu(counts, idx, remaining_mentsu - 1):
      counts[idx] += 3
      return True
    counts[idx] += 3

  # Try sequence (shuntsu) if number tile and not 8 or 9 of suit
  if idx < 27 and (idx % 9) <= 6:
    if counts[idx + 1] > 0 and counts[idx + 2] > 0:
      counts[idx] -= 1
      counts[idx + 1] -= 1
      counts[idx + 2] -= 1
      if _can_form_mentsu(counts, idx, remaining_mentsu - 1):
        counts[idx] += 1
        counts[idx + 1] += 1
        counts[idx + 2] += 1
        return True
      counts[idx] += 1
      counts[idx + 1] += 1
      counts[idx + 2] += 1

  return False


def is_agari_counts34(counts34: Sequence[int], num_melds: int = 0) -> bool:
  """Return True if 34-tile counts + `num_melds` form a complete winning hand shape."""
  total_closed = sum(counts34)
  expected_closed = 14 - 3 * num_melds
  if total_closed != expected_closed:
    return False

  # 1. Kokushi Musou (only when closed hand has 14 tiles, num_melds == 0)
  if num_melds == 0:
    if all(counts34[k] >= 1 for k in YAOCHUU_KINDS) and (
        sum(counts34[k] for k in YAOCHUU_KINDS) == 14
    ):
      return True
    # 2. Chiitoitsu (7 distinct pairs, num_melds == 0)
    if sum(1 for c in counts34 if c == 2) == 7:
      return True

  # 3. Standard 4-mentsu 1-jantou
  work = list(counts34)
  for k in range(34):
    if work[k] >= 2:
      work[k] -= 2
      if _can_form_mentsu(work, 0, 4 - num_melds):
        work[k] += 2
        return True
      work[k] += 2
  return False


def get_tenpai_waits(
    closed_tokens_13: Sequence[str], melds: Sequence[Meld] = ()
) -> set[int]:
  """Return the set of winning tile kind34s (0..33) for a 13-tile hand.

  Per Tenhou rules:
  '形式聴牌あり。5枚目を待つ聴牌あり(手牌＝純手牌＋副露手牌として純手牌で４枚使用していなければ成立)'
  Thus a wait kind `w` is valid if and only if `closed_counts[w] < 4` and adding `w`
  forms a valid agari shape.
  """
  num_melds = len(melds)
  if len(closed_tokens_13) != 13 - 3 * num_melds:
    return set()
  counts = tokens_to_counts34(closed_tokens_13)
  waits: set[int] = set()
  for w in range(34):
    if counts[w] >= 4:
      continue
    counts[w] += 1
    if is_agari_counts34(counts, num_melds=num_melds):
      waits.add(w)
    counts[w] -= 1
  return waits


def is_tenpai(
    closed_tokens_13: Sequence[str], melds: Sequence[Meld] = ()
) -> bool:
  """Return True if the hand is in Tenpai (including valid keishiki tenpai)."""
  return len(get_tenpai_waits(closed_tokens_13, melds)) > 0


def can_ankan_in_riichi(
    closed_tokens_13_before_draw: Sequence[str],
    drawn_token: str,
    melds: Sequence[Meld],
    kan_token: str,
) -> bool:
  """Check if declaring Ankan on `kan_token` is legal while in Riichi.

  Per Tenhou rules:
  'リーチ後の暗槓は待ちが変わらない場合のみ。送り槓不可、牌姿や役の増減は不問。'
  '九蓮宝燈は暗槓不可'
  """
  drawn_kind = token_to_kind34(drawn_token)
  kan_kind = token_to_kind34(kan_token)
  # 送り槓不可: can only ankan the tile kind just drawn
  if kan_kind != drawn_kind:
    return False

  counts_before = tokens_to_counts34(closed_tokens_13_before_draw)
  if counts_before[kan_kind] != 3:
    return False

  waits_before = get_tenpai_waits(closed_tokens_13_before_draw, melds)
  if not waits_before or kan_kind in waits_before:
    return False

  # Build hand after ankan
  remaining_tokens = [
      t
      for t in closed_tokens_13_before_draw
      if token_to_kind34(t) != kan_kind
  ]
  melds_after = list(melds) + [
      Meld(meld_type="ankan", tiles=[kind34_to_token(kan_kind)] * 4)
  ]
  waits_after = get_tenpai_waits(remaining_tokens, melds_after)
  return waits_before == waits_after
