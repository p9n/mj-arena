"""Verification client for the Japanese Mahjong Server with shanten/ukeire AI."""

from __future__ import annotations

import argparse
import functools
import json
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT))

from server.agari import WinContext, evaluate_agari
from server.config import RuleConfig
from server.hand import (
    Meld,
    can_ankan_in_riichi,
    get_kuikae_forbidden_kinds,
    get_tenpai_waits,
    is_tenpai,
    tokens_to_counts34,
)
from server.tile import (
    DRAGON_KINDS,
    WIND_TO_KIND34,
    YAOCHUU_KINDS,
    dora_from_indicator_kind34,
    is_aka_token,
    kind34_to_token,
    token_to_kind34,
)


@functools.lru_cache(maxsize=16384)
def _suit_no_pair_best_t(c: tuple[int, ...]) -> tuple[int, int, int, int, int]:
  """Return max taatsu count for m=0..4 melds within a 9-tile number suit."""
  idx = -1
  for i in range(9):
    if c[i] > 0:
      idx = i
      break
  if idx == -1:
    return (0, -1, -1, -1, -1)

  # Option 1: treat one copy at idx as isolated
  c_list = list(c)
  c_list[idx] -= 1
  best = list(_suit_no_pair_best_t(tuple(c_list)))

  # Option 2: koutsu (triplet)
  if c[idx] >= 3:
    c_list = list(c)
    c_list[idx] -= 3
    sub = _suit_no_pair_best_t(tuple(c_list))
    for m in range(4):
      if sub[m] >= 0 and sub[m] > best[m + 1]:
        best[m + 1] = sub[m]

  # Option 3: shuntsu (sequence)
  if idx <= 6 and c[idx + 1] > 0 and c[idx + 2] > 0:
    c_list = list(c)
    c_list[idx] -= 1
    c_list[idx + 1] -= 1
    c_list[idx + 2] -= 1
    sub = _suit_no_pair_best_t(tuple(c_list))
    for m in range(4):
      if sub[m] >= 0 and sub[m] > best[m + 1]:
        best[m + 1] = sub[m]

  # Option 4: pair taatsu
  if c[idx] >= 2:
    c_list = list(c)
    c_list[idx] -= 2
    sub = _suit_no_pair_best_t(tuple(c_list))
    for m in range(5):
      if sub[m] >= 0 and sub[m] + 1 > best[m]:
        best[m] = sub[m] + 1

  # Option 5: ryanmen / penchan taatsu
  if idx <= 7 and c[idx + 1] > 0:
    c_list = list(c)
    c_list[idx] -= 1
    c_list[idx + 1] -= 1
    sub = _suit_no_pair_best_t(tuple(c_list))
    for m in range(5):
      if sub[m] >= 0 and sub[m] + 1 > best[m]:
        best[m] = sub[m] + 1

  # Option 6: kanchan taatsu
  if idx <= 6 and c[idx + 2] > 0:
    c_list = list(c)
    c_list[idx] -= 1
    c_list[idx + 2] -= 1
    sub = _suit_no_pair_best_t(tuple(c_list))
    for m in range(5):
      if sub[m] >= 0 and sub[m] + 1 > best[m]:
        best[m] = sub[m] + 1

  return (best[0], best[1], best[2], best[3], best[4])


@functools.lru_cache(maxsize=16384)
def _suit_states(
    c: tuple[int, ...]
) -> tuple[tuple[int, int, int, int, int], tuple[int, int, int, int, int]]:
  """Return (no_pair_best_t, with_pair_best_t) for a 9-tile number suit."""
  no_pair = _suit_no_pair_best_t(c)
  with_pair = [-1, -1, -1, -1, -1]
  for i in range(9):
    if c[i] >= 2:
      c_list = list(c)
      c_list[i] -= 2
      sub = _suit_no_pair_best_t(tuple(c_list))
      for m in range(5):
        if sub[m] > with_pair[m]:
          with_pair[m] = sub[m]
  return no_pair, (with_pair[0], with_pair[1], with_pair[2], with_pair[3], with_pair[4])


@functools.lru_cache(maxsize=4096)
def _honor_states(
    c: tuple[int, ...]
) -> tuple[tuple[int, int, int, int, int], tuple[int, int, int, int, int]]:
  """Return (no_pair_best_t, with_pair_best_t) for the 7 honor tiles."""
  trips = sum(1 for x in c if x >= 3)
  pairs = sum(1 for x in c if x == 2)
  no_pair = [-1, -1, -1, -1, -1]
  # A triplet can also be used as a pair taatsu if needed, but using it as a meld is always optimal
  for used_m in range(trips + 1):
    leftover_pairs = pairs + (trips - used_m)
    if used_m <= 4:
      no_pair[used_m] = leftover_pairs

  with_pair = [-1, -1, -1, -1, -1]
  for i in range(7):
    if c[i] >= 2:
      c_list = list(c)
      c_list[i] -= 2
      t2 = sum(1 for x in c_list if x >= 3)
      p2 = sum(1 for x in c_list if x == 2)
      for used_m in range(t2 + 1):
        if used_m <= 4:
          rem_p = p2 + (t2 - used_m)
          if rem_p > with_pair[used_m]:
            with_pair[used_m] = rem_p

  return (
      (no_pair[0], no_pair[1], no_pair[2], no_pair[3], no_pair[4]),
      (with_pair[0], with_pair[1], with_pair[2], with_pair[3], with_pair[4]),
  )


def calculate_shanten(counts34: list[int], num_melds: int = 0) -> int:
  """Compute exact minimum shanten (standard, chiitoitsu, and kokushi)."""
  need_melds = 4 - num_melds
  s_m = _suit_states(tuple(counts34[0:9]))
  s_p = _suit_states(tuple(counts34[9:18]))
  s_s = _suit_states(tuple(counts34[18:27]))
  s_z = _honor_states(tuple(counts34[27:34]))

  cur_no = list(s_m[0])
  cur_yes = list(s_m[1])

  for nxt_no, nxt_yes in (s_p, s_s, s_z):
    merged_no = [-1] * 5
    merged_yes = [-1] * 5
    for m1 in range(need_melds + 1):
      if cur_no[m1] < 0 and cur_yes[m1] < 0:
        continue
      for m2 in range(need_melds + 1 - m1):
        m_tot = m1 + m2
        if cur_no[m1] >= 0 and nxt_no[m2] >= 0:
          v = cur_no[m1] + nxt_no[m2]
          if v > merged_no[m_tot]:
            merged_no[m_tot] = v
        if cur_yes[m1] >= 0 and nxt_no[m2] >= 0:
          v = cur_yes[m1] + nxt_no[m2]
          if v > merged_yes[m_tot]:
            merged_yes[m_tot] = v
        if cur_no[m1] >= 0 and nxt_yes[m2] >= 0:
          v = cur_no[m1] + nxt_yes[m2]
          if v > merged_yes[m_tot]:
            merged_yes[m_tot] = v
    cur_no = merged_no
    cur_yes = merged_yes

  max_score = 0
  for m in range(need_melds + 1):
    if cur_no[m] >= 0:
      t = min(need_melds - m, cur_no[m])
      sc = 2 * m + t
      if sc > max_score:
        max_score = sc
    if cur_yes[m] >= 0:
      t = min(need_melds - m, cur_yes[m])
      sc = 2 * m + t + 1
      if sc > max_score:
        max_score = sc

  best_shanten = 8 - 2 * num_melds - max_score

  if num_melds == 0:
    # Chiitoitsu shanten
    pairs = sum(1 for c in counts34 if c >= 2)
    kinds = sum(1 for c in counts34 if c >= 1)
    chiitoi_shanten = 6 - pairs + max(0, 7 - kinds)
    if chiitoi_shanten < best_shanten:
      best_shanten = chiitoi_shanten

    # Kokushi musou shanten
    yaochuu_kinds = sum(1 for k in YAOCHUU_KINDS if counts34[k] >= 1)
    yaochuu_pair = 1 if any(counts34[k] >= 2 for k in YAOCHUU_KINDS) else 0
    kokushi_shanten = 13 - yaochuu_kinds - yaochuu_pair
    if kokushi_shanten < best_shanten:
      best_shanten = kokushi_shanten

  return best_shanten


class SimpleClient:
  """Stdio JSONL Mahjong client supporting normal play and verification modes."""

  def __init__(self, mode: str = "normal") -> None:
    self.mode = mode
    self.seat: int = 0
    self.dealer_seat: int = 0
    self.round_wind: str = "E"
    self.seat_wind: str = "E"
    self.hand: list[str] = []
    self.melds: list[Meld] = []
    self.river: list[str] = []
    self.all_rivers: list[list[str]] = [[], [], [], []]
    self.all_melds: list[list[list[str]]] = [[], [], [], []]
    self.total_kans: int = 0
    self.is_riichi: bool = False
    self.is_double_riichi: bool = False
    self.is_ippatsu: bool = False
    self.temp_furiten: bool = False
    self.riichi_furiten: bool = False
    self.dora_indicators: list[str] = []
    self.points: list[int] = [25000] * 4
    self.rules = RuleConfig()
    self._triggered_test_action = False

  def _yakuhai_kinds(self) -> set[int]:
    return set(DRAGON_KINDS) | {
        WIND_TO_KIND34[self.round_wind],
        WIND_TO_KIND34[self.seat_wind],
    }

  def _dora_kinds(self) -> list[int]:
    return [
        dora_from_indicator_kind34(token_to_kind34(ind))
        for ind in self.dora_indicators
    ]

  def _visible_counts(self, closed_hand: list[str]) -> list[int]:
    counts = [0] * 34
    for t in closed_hand:
      counts[token_to_kind34(t)] += 1
    for r in self.all_rivers:
      for t in r:
        counts[token_to_kind34(t)] += 1
    for m_list in self.all_melds:
      for m_tiles in m_list:
        for t in m_tiles:
          counts[token_to_kind34(t)] += 1
    for ind in self.dora_indicators:
      counts[token_to_kind34(ind)] += 1
    return counts

  def _is_furiten(self, hand13: list[str] | None = None) -> bool:
    if self.temp_furiten or self.riichi_furiten:
      return True
    target_hand = self.hand if hand13 is None else hand13
    waits = get_tenpai_waits(target_hand, self.melds)
    if not waits:
      return False
    river_kinds = {token_to_kind34(t) for t in self.river}
    return bool(waits & river_kinds)

  def _tile_keep_value(self, token: str, counts34: list[int]) -> float:
    """Secondary heuristic score for how valuable a tile is to keep in hand."""
    k = token_to_kind34(token)
    val = 0.0
    if is_aka_token(token):
      val += 3.5
    for dk in self._dora_kinds():
      if k == dk:
        val += 3.0
    if k in self._yakuhai_kinds():
      if counts34[k] >= 2:
        val += 4.5
      else:
        val += 1.2
    elif k >= 27:
      # Guest wind
      val += 0.2 if counts34[k] == 1 else 1.5
    else:
      # Number tile 0..8
      num = (k % 9) + 1
      # Centrality bonus: 3..7 are most flexible, 2/8 next, 1/9 least
      centrality = {1: 0.5, 2: 1.4, 3: 2.2, 4: 2.5, 5: 2.6, 6: 2.5, 7: 2.2, 8: 1.4, 9: 0.5}[num]
      val += centrality
      # Adjacency bonus
      suit_base = (k // 9) * 9
      idx = k - suit_base
      if counts34[k] >= 2:
        val += 2.0
      if idx > 0 and counts34[suit_base + idx - 1] > 0:
        val += 1.8
      if idx < 8 and counts34[suit_base + idx + 1] > 0:
        val += 1.8
      if idx > 1 and counts34[suit_base + idx - 2] > 0:
        val += 0.9
      if idx < 7 and counts34[suit_base + idx + 2] > 0:
        val += 0.9
    return val

  def _score_hand13(
      self,
      hand13: list[str],
      melds: list[Meld],
      vis_counts: list[int],
      can_riichi: bool,
  ) -> tuple[int, float, bool]:
    """Evaluate a 13-tile hand.

    Returns:
      (shanten, efficiency_score, should_riichi)
      Lower shanten is better; higher efficiency_score is better.
    """
    counts34 = tokens_to_counts34(hand13)
    num_melds = len(melds)
    shanten = calculate_shanten(counts34, num_melds)

    if shanten == 0:
      waits = get_tenpai_waits(hand13, melds)
      if not waits:
        # 5th-tile wait in closed hand (not legal tenpai)
        return (1, 0.0, False)

      river_kinds = {token_to_kind34(t) for t in self.river}
      is_furiten_wait = bool(waits & river_kinds) or self.temp_furiten or self.riichi_furiten

      total_wait_copies = 0
      yaku_wait_copies = 0
      riichi_wait_copies = 0
      for w in waits:
        rem_copies = max(0, 4 - vis_counts[w])
        total_wait_copies += rem_copies
        w_tok = kind34_to_token(w)
        # Check if winning by Ron without riichi has yaku
        ctx_dama = WinContext(
            is_tsumo=False,
            winning_token=w_tok,
            seat_wind_kind34=WIND_TO_KIND34[self.seat_wind],
            round_wind_kind34=WIND_TO_KIND34[self.round_wind],
            is_dealer=(self.seat == self.dealer_seat),
            is_riichi=False,
            dora_indicators=self.dora_indicators,
            rules=self.rules,
        )
        res_dama = evaluate_agari(hand13, melds, ctx_dama)
        if res_dama.is_valid:
          yaku_wait_copies += rem_copies
        elif can_riichi:
          riichi_wait_copies += rem_copies
        else:
          # Check if at least Tsumo gives yaku (e.g. menzen tsumo)
          ctx_tsumo = WinContext(
              is_tsumo=True,
              winning_token=w_tok,
              seat_wind_kind34=WIND_TO_KIND34[self.seat_wind],
              round_wind_kind34=WIND_TO_KIND34[self.round_wind],
              is_dealer=(self.seat == self.dealer_seat),
              is_riichi=False,
              dora_indicators=self.dora_indicators,
              rules=self.rules,
          )
          if evaluate_agari(hand13, melds, ctx_tsumo).is_valid:
            yaku_wait_copies += rem_copies * 0.4

      effective_copies = yaku_wait_copies + (riichi_wait_copies if can_riichi else 0)
      furiten_Mult = 0.35 if is_furiten_wait else 1.0
      keep_bonus = sum(self._tile_keep_value(t, counts34) for t in hand13) * 0.01
      eff_score = (effective_copies * 100.0 + total_wait_copies * 10.0) * furiten_Mult + keep_bonus
      should_riichi = bool(can_riichi and total_wait_copies > 0 and not is_furiten_wait)
      # Even if furiten, riichi if we have no dama yaku so we can win by Tsumo
      if can_riichi and total_wait_copies > 0 and yaku_wait_copies == 0:
        should_riichi = True
      return (0, eff_score, should_riichi)

    # For shanten >= 1: calculate ukeire (number of remaining tiles that reduce shanten)
    ukeire_copies = 0
    for k in range(34):
      if counts34[k] >= 4:
        continue
      rem = 4 - vis_counts[k]
      if rem <= 0:
        continue
      counts34[k] += 1
      if calculate_shanten(counts34, num_melds) < shanten:
        ukeire_copies += rem
      counts34[k] -= 1

    keep_bonus = sum(self._tile_keep_value(t, counts34) for t in hand13) * 0.05
    return (shanten, ukeire_copies * 10.0 + keep_bonus, False)

  def _choose_discard(
      self,
      hand14: list[str],
      drawn_tile: str | None,
      forbidden: set[str],
      remaining_tiles: int = 10,
  ) -> tuple[str, str]:
    """Return (action, discard_tile) for a 14-tile (or post-call) hand."""
    if self.is_riichi and drawn_tile is not None:
      return ("discard", drawn_tile)

    if (
        self.mode in ("tsumogiri", "timeout")
        and drawn_tile is not None
        and drawn_tile not in forbidden
    ):
      return ("discard", drawn_tile)

    is_menzen = all(not m.is_open for m in self.melds)
    can_riichi = (
        not self.is_riichi
        and is_menzen
        and self.points[self.seat] >= 1000
        and drawn_tile is not None
        and remaining_tiles >= 4
    )

    vis_counts = self._visible_counts(hand14)
    best_shanten = 99
    best_score = -1e9
    best_tile = hand14[-1]
    best_riichi = False
    seen_candidates: set[str] = set()

    for i, candidate in enumerate(hand14):
      if candidate in forbidden or candidate in seen_candidates:
        continue
      seen_candidates.add(candidate)
      rem13 = hand14[:i] + hand14[i + 1 :]
      shanten, eff_score, should_riichi = self._score_hand13(
          rem13, self.melds, vis_counts, can_riichi
      )
      # Tie-break slightly against discarding red five or dora
      discard_penalty = 0.0
      if is_aka_token(candidate):
        discard_penalty += 0.25
      if token_to_kind34(candidate) in self._dora_kinds():
        discard_penalty += 0.2
      eff_score -= discard_penalty

      if shanten < best_shanten or (
          shanten == best_shanten and eff_score > best_score
      ):
        best_shanten = shanten
        best_score = eff_score
        best_tile = candidate
        best_riichi = should_riichi

    if best_riichi and can_riichi:
      return ("riichi", best_tile)
    return ("discard", best_tile)

  def _has_open_yaku_plan(
      self, rem_hand_after_discard: list[str], new_melds: list[Meld]
  ) -> bool:
    """Return True if opening/advancing with `new_melds` preserves a clear Yaku (Yakuhai, Toitoi, Honitsu, or Tanyao)."""
    yakuhai = self._yakuhai_kinds()
    # 1. Any completed Yakuhai triplet/kan in melds
    for m in new_melds:
      if m.meld_type in ("pon", "daiminkan", "ankan", "kakan"):
        if token_to_kind34(m.tiles[0]) in yakuhai:
          return True

    counts34 = tokens_to_counts34(rem_hand_after_discard)
    # 2. Concealed Yakuhai triplet in hand
    if any(counts34[yk] >= 3 for yk in yakuhai):
      return True

    # 3. Kuitan (Open Tanyao): all melds are simples (2..8) and hand is all simples
    if self.rules.kuitan:
      melds_all_simples = all(
          all(token_to_kind34(t) not in YAOCHUU_KINDS for t in m.tiles)
          for m in new_melds
      )
      if melds_all_simples:
        hand_yaochuu = sum(counts34[k] for k in YAOCHUU_KINDS)
        if hand_yaochuu == 0:
          return True

    # 4. Honitsu / Chinitsu (Single suit + honors)
    suits_present: set[int] = set()
    for m in new_melds:
      for t in m.tiles:
        k = token_to_kind34(t)
        if k < 27:
          suits_present.add(k // 9)
    for k in range(27):
      if counts34[k] > 0:
        suits_present.add(k // 9)
    if len(suits_present) == 1:
      return True

    return False

  def _consider_call(
      self, discarded_tile: str, discarder_seat: int, remaining_tiles: int
  ) -> dict[str, Any] | None:
    """Evaluate whether calling Pon or Chi on `discarded_tile` speeds up a valid win."""
    if self.is_riichi or remaining_tiles <= 0 or self.mode != "normal":
      return None

    vis_counts = self._visible_counts(self.hand)
    shanten_before = calculate_shanten(
        tokens_to_counts34(self.hand), len(self.melds)
    )
    k_disc = token_to_kind34(discarded_tile)
    yakuhai = self._yakuhai_kinds()

    best_call: dict[str, Any] | None = None
    best_shanten = shanten_before
    best_score = -1e9

    # Candidate 1: Pon
    matching = [t for t in self.hand if token_to_kind34(t) == k_disc]
    if len(matching) >= 2:
      # Prefer using red five in the meld if present
      matching.sort(key=lambda tok: (0 if is_aka_token(tok) else 1))
      pon_tiles = matching[:2]
      rem_hand = list(self.hand)
      for pt in pon_tiles:
        rem_hand.remove(pt)
      forb_kinds = get_kuikae_forbidden_kinds("pon", discarded_tile, pon_tiles)
      legal_discards = [
          t for t in rem_hand if token_to_kind34(t) not in forb_kinds
      ]
      if legal_discards:
        new_melds = self.melds + [
            Meld("pon", pon_tiles + [discarded_tile], discarded_tile, discarder_seat)
        ]
        for d in set(legal_discards):
          rem_after = list(rem_hand)
          rem_after.remove(d)
          if not self._has_open_yaku_plan(rem_after, new_melds):
            continue
          sh_after, eff_after, _ = self._score_hand13(
              rem_after, new_melds, vis_counts, False
          )
          # Allow Yakuhai pon even if shanten stays equal (when shanten <= 2 and locks in yaku)
          allow_equal = (k_disc in yakuhai and shanten_before <= 2)
          if sh_after < best_shanten or (
              allow_equal
              and sh_after == shanten_before
              and best_call is None
          ) or (
              sh_after == best_shanten
              and best_call is not None
              and eff_after > best_score
          ):
            best_shanten = sh_after
            best_score = eff_after
            best_call = {"action": "pon", "tiles": pon_tiles}

    # Candidate 2: Chi (only from Kamicha)
    if (discarder_seat + 1) % 4 == self.seat and k_disc < 27:
      suit_base = (k_disc // 9) * 9
      num = k_disc - suit_base  # 0..8
      chi_patterns: list[tuple[int, int]] = []
      if num >= 2:
        chi_patterns.append((suit_base + num - 2, suit_base + num - 1))
      if 1 <= num <= 7:
        chi_patterns.append((suit_base + num - 1, suit_base + num + 1))
      if num <= 6:
        chi_patterns.append((suit_base + num + 1, suit_base + num + 2))

      for k1, k2 in chi_patterns:
        t1_list = [t for t in self.hand if token_to_kind34(t) == k1]
        t2_list = [t for t in self.hand if token_to_kind34(t) == k2]
        if not t1_list or not t2_list:
          continue
        t1_list.sort(key=lambda tok: (0 if is_aka_token(tok) else 1))
        t2_list.sort(key=lambda tok: (0 if is_aka_token(tok) else 1))
        chi_tiles = [t1_list[0], t2_list[0]]
        rem_hand = list(self.hand)
        for ct in chi_tiles:
          rem_hand.remove(ct)
        forb_kinds = get_kuikae_forbidden_kinds(
            "chi", discarded_tile, chi_tiles
        )
        legal_discards = [
            t for t in rem_hand if token_to_kind34(t) not in forb_kinds
        ]
        if not legal_discards:
          continue
        new_melds = self.melds + [
            Meld("chi", chi_tiles + [discarded_tile], discarded_tile, discarder_seat)
        ]
        for d in set(legal_discards):
          rem_after = list(rem_hand)
          rem_after.remove(d)
          if not self._has_open_yaku_plan(rem_after, new_melds):
            continue
          sh_after, eff_after, _ = self._score_hand13(
              rem_after, new_melds, vis_counts, False
          )
          # Only Chi when it strictly reduces shanten (and reaches shanten <= 1)
          if sh_after < shanten_before and sh_after <= 1:
            if sh_after < best_shanten or (
                sh_after == best_shanten and eff_after > best_score
            ):
              best_shanten = sh_after
              best_score = eff_after
              best_call = {"action": "chi", "tiles": chi_tiles}

    return best_call

  def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
    msg_type = msg.get("type")
    seq = msg.get("seq")

    if msg_type == "match_start":
      self.seat = int(msg["seat"])
      self.rules = RuleConfig.from_dict(msg.get("rules", {}))
      return None

    if msg_type == "round_start":
      self.seat = int(msg["seat"])
      self.dealer_seat = int(msg["dealer_seat"])
      self.round_wind = str(msg["round_wind"])
      self.seat_wind = str(msg["seat_wind"])
      self.hand = list(msg["hand"])
      self.melds = []
      self.river = []
      self.all_rivers = [[], [], [], []]
      self.all_melds = [[], [], [], []]
      self.total_kans = 0
      self.is_riichi = False
      self.is_double_riichi = False
      self.is_ippatsu = False
      self.temp_furiten = False
      self.riichi_furiten = False
      self.dora_indicators = list(msg.get("dora_indicators", []))
      self.points = list(msg.get("points", [25000] * 4))
      return None

    if msg_type == "dora_notify":
      self.dora_indicators = list(
          msg.get("dora_indicators", self.dora_indicators)
      )
      return None

    if msg_type == "riichi_accepted":
      r_seat = int(msg["seat"])
      self.points = list(msg.get("points", self.points))
      if r_seat == self.seat:
        self.is_riichi = True
        self.is_double_riichi = bool(msg.get("is_double_riichi", False))
        self.is_ippatsu = True
      return None

    if msg_type == "call_notify":
      c_seat = int(msg["seat"])
      m_type = str(msg["meld_type"])
      m_tiles = list(msg.get("tiles", []))
      from_seat = msg.get("from_seat")
      if m_type in ("daiminkan", "ankan", "kakan"):
        self.total_kans += 1
      if from_seat is not None and m_type in ("chi", "pon", "daiminkan"):
        f_idx = int(from_seat)
        if self.all_rivers[f_idx]:
          self.all_rivers[f_idx].pop()
      self.all_melds[c_seat].append(m_tiles)

      if c_seat == self.seat:
        if m_type in ("chi", "pon", "daiminkan", "ankan"):
          self.melds.append(
              Meld(
                  meld_type=m_type,
                  tiles=m_tiles,
                  called_tile=msg.get("called_tile"),
                  from_seat=from_seat,
              )
          )
        elif m_type == "kakan":
          called_tok = str(msg.get("called_tile", ""))
          for idx, m in enumerate(self.melds):
            if m.meld_type == "pon" and token_to_kind34(
                m.tiles[0]
            ) == token_to_kind34(called_tok):
              self.melds[idx] = Meld(
                  meld_type="kakan",
                  tiles=m_tiles,
                  called_tile=called_tok,
                  from_seat=m.from_seat,
              )
              break
      self.is_ippatsu = False
      return None

    if msg_type == "discard_ack":
      tile = str(msg["tile"])
      if tile in self.hand:
        self.hand.remove(tile)
      self.river.append(tile)
      self.all_rivers[self.seat].append(tile)
      self.temp_furiten = False
      if self.is_riichi:
        self.is_ippatsu = False
      return None

    if msg_type == "turn_tsumo":
      drawn_tile = str(msg["tile"])
      if "hand" in msg:
        self.hand = list(msg["hand"])
      remaining_tiles = int(msg.get("remaining_tiles", 10))
      is_rinshan = bool(msg.get("is_rinshan", False))

      # Special test modes
      if self.mode == "mem-hog" and not self._triggered_test_action:
        self._triggered_test_action = True
        _ = bytearray(512 * 1024 * 1024)

      if self.mode == "timeout" and not self._triggered_test_action:
        self._triggered_test_action = True
        time.sleep(6.0)
        return {"seq": seq, "action": "discard", "tile": drawn_tile}

      if self.mode == "chombo" and not self._triggered_test_action:
        self._triggered_test_action = True
        return {"seq": seq, "action": "discard", "tile": "9z"}

      # 1. Check Tsumo win
      win_ctx = WinContext(
          is_tsumo=True,
          winning_token=drawn_tile,
          seat_wind_kind34=WIND_TO_KIND34[self.seat_wind],
          round_wind_kind34=WIND_TO_KIND34[self.round_wind],
          is_dealer=(self.seat == self.dealer_seat),
          is_riichi=self.is_riichi,
          is_double_riichi=self.is_double_riichi,
          is_ippatsu=self.is_ippatsu,
          is_rinshan=is_rinshan,
          is_haitei=(remaining_tiles == 0 and not is_rinshan),
          dora_indicators=self.dora_indicators,
          rules=self.rules,
      )
      agari = evaluate_agari(self.hand, self.melds, win_ctx)
      if agari.is_valid:
        return {"seq": seq, "action": "tsumo"}

      hand14 = self.hand + [drawn_tile]

      # 2. Check Riichi Ankan if in Riichi
      if (
          self.is_riichi
          and self.mode == "normal"
          and remaining_tiles > 0
          and self.total_kans < 4
      ):
        if can_ankan_in_riichi(self.hand, drawn_tile, self.melds, drawn_tile):
          self.hand = list(hand14)
          return {"seq": seq, "action": "ankan", "tile": drawn_tile}

      # 3. Choose optimal discard (or Riichi)
      action, disc_tile = self._choose_discard(
          hand14, drawn_tile, set(), remaining_tiles
      )
      if action == "riichi" and remaining_tiles < 4:
        action = "discard"
      self.hand = list(hand14)
      return {"seq": seq, "action": action, "tile": disc_tile}

    if msg_type == "turn_after_call":
      if "hand" in msg:
        self.hand = list(msg["hand"])
      forbidden = set(msg.get("forbidden_discards", []))
      _, disc_tile = self._choose_discard(self.hand, None, forbidden)
      return {"seq": seq, "action": "discard", "tile": disc_tile}

    if msg_type in ("discard_notify", "kakan_notify"):
      tile = str(msg["tile"])
      discarder = int(msg["seat"])
      is_chankan = msg_type == "kakan_notify"
      remaining_tiles = int(msg.get("remaining_tiles", 1))
      if not is_chankan:
        self.all_rivers[discarder].append(tile)
      is_houtei = not is_chankan and remaining_tiles == 0

      if self.mode != "tsumogiri" and not self._is_furiten():
        win_ctx = WinContext(
            is_tsumo=False,
            winning_token=tile,
            seat_wind_kind34=WIND_TO_KIND34[self.seat_wind],
            round_wind_kind34=WIND_TO_KIND34[self.round_wind],
            is_dealer=(self.seat == self.dealer_seat),
            is_riichi=self.is_riichi,
            is_double_riichi=self.is_double_riichi,
            is_ippatsu=self.is_ippatsu,
            is_chankan=is_chankan,
            is_houtei=is_houtei,
            dora_indicators=self.dora_indicators,
            rules=self.rules,
        )
        agari = evaluate_agari(self.hand, self.melds, win_ctx)
        if agari.is_valid:
          return {"seq": seq, "action": "ron"}

      # Track missed-win furiten locally
      waits = get_tenpai_waits(self.hand, self.melds)
      if token_to_kind34(tile) in waits:
        self.temp_furiten = True
        if self.is_riichi:
          self.riichi_furiten = True

      # Consider Pon / Chi on discard_notify in normal mode
      if not is_chankan and self.mode == "normal":
        call_action = self._consider_call(tile, discarder, remaining_tiles)
        if call_action is not None:
          return {"seq": seq, **call_action}

      return {"seq": seq, "action": "pass"}

    return None

  def run(self) -> None:
    for line in sys.stdin:
      line = line.strip()
      if not line:
        continue
      try:
        msg = json.loads(line)
      except json.JSONDecodeError:
        continue
      resp = self.handle_message(msg)
      if resp is not None:
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
      if msg.get("type") == "match_end":
        break


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Simple Mahjong Verification Client"
  )
  parser.add_argument(
      "--mode",
      choices=["normal", "tsumogiri", "timeout", "chombo", "mem-hog"],
      default="normal",
      help="Client behavior mode",
  )
  args = parser.parse_args()
  client = SimpleClient(mode=args.mode)
  client.run()


if __name__ == "__main__":
  main()
