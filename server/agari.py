"""Hand decomposition, Fu/Han/Yaku evaluation, Local Yaku registry, and scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Callable, Sequence

from server.config import RuleConfig
from server.hand import Meld, tokens_to_counts34
from server.tile import (
    DRAGON_KINDS,
    GREEN_KINDS,
    HONOR_KINDS,
    TERMINAL_KINDS,
    WIND_KINDS,
    YAOCHUU_KINDS,
    dora_from_indicator_token,
    is_aka_token,
    kind34_to_token,
    token_to_kind34,
)

DRAGON_YAKU_NAMES = {
    31: "役牌:白",
    32: "役牌:發",
    33: "役牌:中",
}


@dataclass(frozen=True)
class MentsuPart:
  """A single meld in a 4-mentsu 1-jantou decomposition."""

  kind: str  # "shuntsu", "koutsu", "kantsu"
  tiles34: tuple[int, ...]  # (k, k+1, k+2) for shuntsu, (k, k, k) for koutsu/kantsu
  is_open: bool  # True if open meld or ron-completed koutsu


@dataclass(frozen=True)
class Decomposition:
  """A specific winning decomposition of a 14-tile hand."""

  shape: str  # "standard", "chiitoitsu", "kokushi"
  jantou: int  # kind34 of the pair
  mentsu: tuple[MentsuPart, ...] = ()
  wait_type: str = ""  # "ryanmen", "kanchan", "penchan", "shabo", "tanki", "kokushi13"


@dataclass
class WinContext:
  """Contextual flags and environment for evaluating a win (Agari)."""

  is_tsumo: bool
  winning_token: str
  seat_wind_kind34: int  # 27..30 (東..北)
  round_wind_kind34: int  # 27..30 (東..北)
  is_dealer: bool
  is_riichi: bool = False
  is_double_riichi: bool = False
  is_ippatsu: bool = False
  is_rinshan: bool = False
  is_chankan: bool = False
  is_haitei: bool = False
  is_houtei: bool = False
  is_first_uncalled_turn: bool = False
  dora_indicators: Sequence[str] = ()
  ura_dora_indicators: Sequence[str] = ()
  rules: RuleConfig = field(default_factory=RuleConfig)
  local_yaku_enabled: dict[str, bool] = field(default_factory=dict)
  # Pao seats: map of yakuman name ("大三元", "大四喜") -> responsible seat
  pao_seats: dict[str, int] = field(default_factory=dict)


@dataclass
class AgariResult:
  """Result of evaluating a winning hand under Tenhou high-point rules."""

  is_valid: bool
  han: int = 0
  fu: int = 0
  yaku: list[tuple[str, int]] = field(default_factory=list)
  yakuman: list[str] = field(default_factory=list)
  base_points: int = 0
  total_points: int = 0  # Base win points before honba/kyoutaku
  dora_count: int = 0
  aka_dora_count: int = 0
  ura_dora_count: int = 0


# Extensible Local Yaku Registry:
# Signature: fn(closed_tokens_14, melds, decomp, ctx) -> list[tuple[str, int, bool]]
# where tuple is (yaku_name, han_or_yakuman_multiplier, is_yakuman)
LocalYakuEvaluator = Callable[
    [Sequence[str], Sequence[Meld], Decomposition, WinContext],
    list[tuple[str, int, bool]],
]
LOCAL_YAKU_REGISTRY: dict[str, LocalYakuEvaluator] = {}


def register_local_yaku(name: str, evaluator: LocalYakuEvaluator) -> None:
  """Register a custom/local yaku evaluator that can be enabled via config.json."""
  LOCAL_YAKU_REGISTRY[name] = evaluator


def _find_closed_mentsu(
    counts: list[int], idx: int, needed: int
) -> list[list[tuple[str, tuple[int, int, int]]]]:
  """Enumerate all ways to partition `counts` into `needed` closed mentsu."""
  while idx < 34 and counts[idx] == 0:
    idx += 1
  if idx >= 34:
    return [[]] if needed == 0 else []
  if needed <= 0:
    return []

  results: list[list[tuple[str, tuple[int, int, int]]]] = []
  # Option 1: koutsu
  if counts[idx] >= 3:
    counts[idx] -= 3
    for sub in _find_closed_mentsu(counts, idx, needed - 1):
      results.append([("koutsu", (idx, idx, idx))] + sub)
    counts[idx] += 3

  # Option 2: shuntsu
  if idx < 27 and (idx % 9) <= 6 and counts[idx + 1] > 0 and counts[idx + 2] > 0:
    counts[idx] -= 1
    counts[idx + 1] -= 1
    counts[idx + 2] -= 1
    for sub in _find_closed_mentsu(counts, idx, needed - 1):
      results.append([("shuntsu", (idx, idx + 1, idx + 2))] + sub)
    counts[idx] += 1
    counts[idx + 1] += 1
    counts[idx + 2] += 1

  return results


def enumerate_decompositions(
    closed_tokens_13: Sequence[str],
    winning_token: str,
    melds: Sequence[Meld],
    is_tsumo: bool,
) -> list[Decomposition]:
  """Enumerate all valid decompositions of (closed_tokens_13 + [winning_token], melds)."""
  win_kind = token_to_kind34(winning_token)
  counts13 = tokens_to_counts34(closed_tokens_13)
  # Tenhou rule: cannot win on a 5th copy if all 4 copies are already in closed hand
  if counts13[win_kind] >= 4:
    return []

  counts14 = list(counts13)
  counts14[win_kind] += 1
  num_melds = len(melds)
  if sum(counts14) != 14 - 3 * num_melds:
    return []

  decomps: list[Decomposition] = []

  # 1. Kokushi Musou (only with 0 melds)
  if num_melds == 0:
    if all(counts14[k] >= 1 for k in YAOCHUU_KINDS) and (
        sum(counts14[k] for k in YAOCHUU_KINDS) == 14
    ):
      is_13_way = all(counts13[k] == 1 for k in YAOCHUU_KINDS)
      pair_kind = next(k for k in YAOCHUU_KINDS if counts14[k] == 2)
      decomps.append(
          Decomposition(
              shape="kokushi",
              jantou=pair_kind,
              wait_type="kokushi13" if is_13_way else "tanki",
          )
      )

    # 2. Chiitoitsu (7 distinct pairs, 0 melds)
    if sum(1 for c in counts14 if c == 2) == 7:
      decomps.append(
          Decomposition(
              shape="chiitoitsu",
              jantou=win_kind,
              wait_type="tanki",
          )
      )

  # 3. Standard 4-mentsu 1-jantou
  fixed_meld_parts: list[MentsuPart] = []
  for m in melds:
    k0 = token_to_kind34(m.tiles[0])
    if m.meld_type == "chi":
      ks = tuple(sorted(token_to_kind34(t) for t in m.tiles))
      fixed_meld_parts.append(MentsuPart("shuntsu", ks, is_open=True))
    elif m.meld_type == "pon":
      fixed_meld_parts.append(
          MentsuPart("koutsu", (k0, k0, k0), is_open=True)
      )
    else:
      fixed_meld_parts.append(
          MentsuPart("kantsu", (k0, k0, k0), is_open=m.is_open)
      )

  seen_partitions: set[tuple[int, tuple[tuple[str, tuple[int, int, int]], ...]]] = set()
  work = list(counts14)
  for jantou in range(34):
    if work[jantou] < 2:
      continue
    work[jantou] -= 2
    raw_mentsu_lists = _find_closed_mentsu(work, 0, 4 - num_melds)
    work[jantou] += 2

    for raw_list in raw_mentsu_lists:
      canonical = tuple(sorted(raw_list))
      key = (jantou, canonical)
      if key in seen_partitions:
        continue
      seen_partitions.add(key)

      # Determine which component (the jantou or one of the closed mentsu) was
      # completed by `win_kind`.
      # Case A: `win_kind` completed the jantou (tanki wait)
      if jantou == win_kind:
        parts = [
            MentsuPart(mtype, mtiles, is_open=False)
            for mtype, mtiles in canonical
        ] + fixed_meld_parts
        decomps.append(
            Decomposition(
                shape="standard",
                jantou=jantou,
                mentsu=tuple(parts),
                wait_type="tanki",
            )
        )

      # Case B: `win_kind` completed one of the closed mentsu at index `i`
      for i, (mtype, mtiles) in enumerate(canonical):
        if win_kind not in mtiles:
          continue
        if mtype == "koutsu":
          # Shabo wait: if won by Ron, this koutsu is considered open (明刻子)
          parts = []
          for j, (mt, mti) in enumerate(canonical):
            is_open_part = (j == i) and (not is_tsumo)
            parts.append(MentsuPart(mt, mti, is_open=is_open_part))
          parts.extend(fixed_meld_parts)
          decomps.append(
              Decomposition(
                  shape="standard",
                  jantou=jantou,
                  mentsu=tuple(parts),
                  wait_type="shabo",
              )
          )
        elif mtype == "shuntsu":
          # Sequence completed by win_kind; always considered closed (暗順子)
          k_low, k_mid, k_high = mtiles
          if win_kind == k_mid:
            wtype = "kanchan"
          elif (win_kind == k_high and (k_low % 9) == 0) or (
              win_kind == k_low and (k_high % 9) == 8
          ):
            wtype = "penchan"
          else:
            wtype = "ryanmen"
          parts = [
              MentsuPart(mt, mti, is_open=False) for mt, mti in canonical
          ] + fixed_meld_parts
          decomps.append(
              Decomposition(
                  shape="standard",
                  jantou=jantou,
                  mentsu=tuple(parts),
                  wait_type=wtype,
              )
          )

  return decomps


def calculate_fu(
    decomp: Decomposition, is_menzen: bool, ctx: WinContext, is_pinfu: bool
) -> int:
  """Calculate Fu for a given decomposition under Tenhou rules."""
  if decomp.shape == "chiitoitsu":
    return 25
  if decomp.shape == "kokushi":
    return 30

  if is_pinfu:
    # ツモピン20符; 門前ロン平和30符
    return 20 if ctx.is_tsumo else 30

  fu = 20
  if is_menzen and not ctx.is_tsumo:
    fu += 10
  if ctx.is_tsumo:
    # 嶺上ツモも2符加算
    fu += 2

  # Jantou fu (連風牌は4符)
  if decomp.jantou in DRAGON_KINDS:
    fu += 2
  if decomp.jantou == ctx.round_wind_kind34:
    fu += 2
  if decomp.jantou == ctx.seat_wind_kind34:
    fu += 2

  # Wait fu
  if decomp.wait_type in ("kanchan", "penchan", "tanki"):
    fu += 2

  # Mentsu fu
  for m in decomp.mentsu:
    if m.kind == "shuntsu":
      continue
    is_yaochuu = m.tiles34[0] in YAOCHUU_KINDS
    if m.kind == "koutsu":
      base = 4 if is_yaochuu else 2
      fu += base if m.is_open else base * 2
    elif m.kind == "kantsu":
      base = 16 if is_yaochuu else 8
      fu += base if m.is_open else base * 2

  # Open Pinfu shape (20 fu with open hand) is rounded up to 30 fu
  if not is_menzen and fu == 20:
    return 30

  return int(math.ceil(fu / 10.0) * 10)


def _is_pinfu(decomp: Decomposition, is_menzen: bool, ctx: WinContext) -> bool:
  """Check if a standard decomposition qualifies for Pinfu (平和)."""
  if not is_menzen or decomp.shape != "standard":
    return False
  if decomp.wait_type != "ryanmen":
    return False
  if (
      decomp.jantou in DRAGON_KINDS
      or decomp.jantou == ctx.round_wind_kind34
      or decomp.jantou == ctx.seat_wind_kind34
  ):
    return False
  return all(m.kind == "shuntsu" for m in decomp.mentsu)


def _evaluate_yakuman(
    closed_tokens_13: Sequence[str],
    all_kinds34: Sequence[int],
    melds: Sequence[Meld],
    decomp: Decomposition,
    ctx: WinContext,
) -> list[str]:
  """Return list of Yakuman names satisfied by this decomposition."""
  yakuman_list: list[str] = []
  num_melds = len(melds)

  # 天和 / 地和: 0 melds (no ankan either), first uncalled turn, tsumo
  if ctx.is_first_uncalled_turn and ctx.is_tsumo and num_melds == 0:
    if ctx.is_dealer:
      yakuman_list.append("天和")
    else:
      yakuman_list.append("地和")

  if decomp.shape == "kokushi":
    if decomp.wait_type == "kokushi13":
      yakuman_list.append("国士無双13面")
    else:
      yakuman_list.append("国士無双")
    return yakuman_list

  # 字一色 (includes Chiitoitsu 字一色 per ※七対子の字一色あり)
  if all(k in HONOR_KINDS for k in all_kinds34):
    yakuman_list.append("字一色")

  if decomp.shape == "chiitoitsu":
    return yakuman_list

  # Standard 4-mentsu 1-jantou yakuman checks
  # 四暗刻 / 四暗刻単騎
  ankou_count = sum(
      1 for m in decomp.mentsu if m.kind in ("koutsu", "kantsu") and not m.is_open
  )
  if ankou_count == 4:
    if decomp.wait_type == "tanki":
      yakuman_list.append("四暗刻単騎")
    else:
      yakuman_list.append("四暗刻")

  # 大三元
  dragon_triplets = sum(
      1
      for m in decomp.mentsu
      if m.kind in ("koutsu", "kantsu") and m.tiles34[0] in DRAGON_KINDS
  )
  if dragon_triplets == 3:
    yakuman_list.append("大三元")

  # 小四喜 / 大四喜
  wind_triplets = sum(
      1
      for m in decomp.mentsu
      if m.kind in ("koutsu", "kantsu") and m.tiles34[0] in WIND_KINDS
  )
  if wind_triplets == 4:
    yakuman_list.append("大四喜")
  elif wind_triplets == 3 and decomp.jantou in WIND_KINDS:
    yakuman_list.append("小四喜")

  # 緑一色 (※緑一色は發がなくても成立)
  if all(k in GREEN_KINDS for k in all_kinds34):
    yakuman_list.append("緑一色")

  # 清老頭
  if all(k in TERMINAL_KINDS for k in all_kinds34):
    yakuman_list.append("清老頭")

  # 四槓子
  kan_count = sum(1 for m in decomp.mentsu if m.kind == "kantsu")
  if kan_count == 4:
    yakuman_list.append("四槓子")

  # 九蓮宝燈 / 純正九蓮宝燈 (※九蓮宝燈は暗槓不可、萬筒索可)
  if num_melds == 0:
    suits = {k // 9 for k in all_kinds34}
    if len(suits) == 1 and next(iter(suits)) in (0, 1, 2):
      suit_base = next(iter(suits)) * 9
      counts14 = [0] * 9
      for k in all_kinds34:
        counts14[k - suit_base] += 1
      req = [3, 1, 1, 1, 1, 1, 1, 1, 3]
      if all(counts14[i] >= req[i] for i in range(9)):
        counts13 = [0] * 9
        for tok in closed_tokens_13:
          k = token_to_kind34(tok)
          if suit_base <= k < suit_base + 9:
            counts13[k - suit_base] += 1
        if counts13 == req:
          yakuman_list.append("純正九蓮宝燈")
        else:
          yakuman_list.append("九蓮宝燈")

  return yakuman_list


def _evaluate_regular_yaku(
    all_kinds34: Sequence[int],
    is_menzen: bool,
    decomp: Decomposition,
    ctx: WinContext,
) -> tuple[list[tuple[str, int]], bool]:
  """Return (list of (yaku_name, han), is_pinfu) for non-yakuman yaku (excluding dora/ippatsu)."""
  yaku: list[tuple[str, int]] = []
  is_pinfu = _is_pinfu(decomp, is_menzen, ctx)

  # Situational / Riichi yaku
  if ctx.is_double_riichi:
    yaku.append(("両立直", 2))
  elif ctx.is_riichi:
    yaku.append(("立直", 1))

  if is_menzen and ctx.is_tsumo:
    yaku.append(("門前清自摸和", 1))

  if ctx.is_chankan:
    yaku.append(("槍槓", 1))
  if ctx.is_rinshan:
    yaku.append(("嶺上開花", 1))
  # ※海底と嶺上は複合しない
  if ctx.is_haitei and not ctx.is_rinshan:
    yaku.append(("海底摸月", 1))
  if ctx.is_houtei and not ctx.is_chankan:
    yaku.append(("河底撈魚", 1))

  # 断幺九 (with kuitan check)
  if all(k not in YAOCHUU_KINDS for k in all_kinds34):
    if is_menzen or ctx.rules.kuitan:
      yaku.append(("断幺九", 1))

  # 混一色 / 清一色 / 混老頭
  number_suits = {k // 9 for k in all_kinds34 if k < 27}
  has_honors = any(k in HONOR_KINDS for k in all_kinds34)
  if len(number_suits) == 1:
    if has_honors:
      yaku.append(("混一色", 3 if is_menzen else 2))
    else:
      yaku.append(("清一色", 6 if is_menzen else 5))

  if all(k in YAOCHUU_KINDS for k in all_kinds34) and has_honors and any(
      k in TERMINAL_KINDS for k in all_kinds34
  ):
    yaku.append(("混老頭", 2))

  if decomp.shape == "chiitoitsu":
    yaku.append(("七対子", 2))
    return yaku, False

  if decomp.shape != "standard":
    return yaku, False

  if is_pinfu:
    yaku.append(("平和", 1))

  # Yakuhai (役牌:場風牌, 役牌:門風牌, 役牌:三元牌)
  for m in decomp.mentsu:
    if m.kind in ("koutsu", "kantsu"):
      k = m.tiles34[0]
      if k == ctx.round_wind_kind34:
        yaku.append(("役牌:場風牌", 1))
      if k == ctx.seat_wind_kind34:
        yaku.append(("役牌:門風牌", 1))
      if k in DRAGON_KINDS:
        yaku.append((DRAGON_YAKU_NAMES[k], 1))

  # 一盃口 / 二盃口 (menzen only)
  shuntsu_list = [m.tiles34 for m in decomp.mentsu if m.kind == "shuntsu"]
  if is_menzen and len(shuntsu_list) >= 2:
    sorted_shuntsu = sorted(shuntsu_list)
    if (
        len(sorted_shuntsu) == 4
        and sorted_shuntsu[0] == sorted_shuntsu[1]
        and sorted_shuntsu[2] == sorted_shuntsu[3]
    ):
      yaku.append(("二盃口", 3))
    else:
      # Check if at least one pair of identical sequences exists
      has_pair = any(
          sorted_shuntsu[i] == sorted_shuntsu[i + 1]
          for i in range(len(sorted_shuntsu) - 1)
      )
      if has_pair:
        yaku.append(("一盃口", 1))

  # 三色同順
  if len(shuntsu_list) >= 3:
    shuntsu_set = set(shuntsu_list)
    for base in range(7):
      if (
          (base, base + 1, base + 2) in shuntsu_set
          and (base + 9, base + 10, base + 11) in shuntsu_set
          and (base + 18, base + 19, base + 20) in shuntsu_set
      ):
        yaku.append(("三色同順", 2 if is_menzen else 1))
        break

  # 一気通貫
  if len(shuntsu_list) >= 3:
    shuntsu_set = set(shuntsu_list)
    for suit_idx in range(3):
      b = suit_idx * 9
      if (
          (b, b + 1, b + 2) in shuntsu_set
          and (b + 3, b + 4, b + 5) in shuntsu_set
          and (b + 6, b + 7, b + 8) in shuntsu_set
      ):
        yaku.append(("一気通貫", 2 if is_menzen else 1))
        break

  # 対々和
  koutsu_list = [
      m for m in decomp.mentsu if m.kind in ("koutsu", "kantsu")
  ]
  if len(koutsu_list) == 4:
    yaku.append(("対々和", 2))

  # 三暗刻
  ankou_count = sum(1 for m in koutsu_list if not m.is_open)
  if ankou_count == 3:
    yaku.append(("三暗刻", 2))

  # 三槓子
  kan_count = sum(1 for m in decomp.mentsu if m.kind == "kantsu")
  if kan_count == 3:
    yaku.append(("三槓子", 2))

  # 三色同刻
  if len(koutsu_list) >= 3:
    koutsu_kinds = {m.tiles34[0] for m in koutsu_list}
    for num in range(9):
      if (
          num in koutsu_kinds
          and (num + 9) in koutsu_kinds
          and (num + 18) in koutsu_kinds
      ):
        yaku.append(("三色同刻", 2))
        break

  # 小三元
  dragon_koutsu = sum(1 for m in koutsu_list if m.tiles34[0] in DRAGON_KINDS)
  if dragon_koutsu == 2 and decomp.jantou in DRAGON_KINDS:
    yaku.append(("小三元", 2))

  # 混全帯幺九 / 純全帯幺九 (requires at least 1 shuntsu)
  if shuntsu_list:
    all_parts_have_terminal = (decomp.jantou in TERMINAL_KINDS) and all(
        any(k in TERMINAL_KINDS for k in m.tiles34) for m in decomp.mentsu
    )
    all_parts_have_yaochuu = (decomp.jantou in YAOCHUU_KINDS) and all(
        any(k in YAOCHUU_KINDS for k in m.tiles34) for m in decomp.mentsu
    )
    if all_parts_have_terminal:
      yaku.append(("純全帯幺九", 3 if is_menzen else 2))
    elif all_parts_have_yaochuu:
      yaku.append(("混全帯幺九", 2 if is_menzen else 1))

  return yaku, is_pinfu


def calculate_base_points(han: int, fu: int, yakuman_multiplier: int = 0) -> int:
  """Compute base points B from (han, fu) or yakuman multiplier."""
  if yakuman_multiplier > 0:
    return 8000 * yakuman_multiplier
  if han >= 13:
    return 8000  # 数え役満
  if han >= 11:
    return 6000  # 三倍満
  if han >= 8:
    return 4000  # 倍満
  if han >= 6:
    return 3000  # 跳満
  if han >= 5:
    return 2000  # 満貫
  base = fu * (2 ** (han + 2))
  return min(base, 2000)


def base_to_total_win_points(base_points: int, is_dealer: bool, is_tsumo: bool) -> int:
  """Return total points received by the winner (excluding honba/kyoutaku)."""
  if is_dealer:
    if is_tsumo:
      each = int(math.ceil((base_points * 2) / 100.0) * 100)
      return each * 3
    return int(math.ceil((base_points * 6) / 100.0) * 100)
  else:
    if is_tsumo:
      oya_pay = int(math.ceil((base_points * 2) / 100.0) * 100)
      ko_pay = int(math.ceil(base_points / 100.0) * 100)
      return oya_pay + ko_pay * 2
    return int(math.ceil((base_points * 4) / 100.0) * 100)


def evaluate_agari(
    closed_tokens_13: Sequence[str],
    melds: Sequence[Meld],
    ctx: WinContext,
) -> AgariResult:
  """Evaluate a hand for Agari and return the highest-scoring interpretation (高点法)."""
  decomps = enumerate_decompositions(
      closed_tokens_13, ctx.winning_token, melds, is_tsumo=ctx.is_tsumo
  )
  if not decomps:
    return AgariResult(is_valid=False)

  is_menzen = all(not m.is_open for m in melds)
  all_tokens: list[str] = list(closed_tokens_13) + [ctx.winning_token]
  for m in melds:
    all_tokens.extend(m.tiles)
  all_kinds34 = [token_to_kind34(t) for t in all_tokens]
  closed14_tokens = list(closed_tokens_13) + [ctx.winning_token]

  # Count Dora, Aka Dora, Ura Dora
  dora_kinds = [
      token_to_kind34(dora_from_indicator_token(ind))
      for ind in ctx.dora_indicators
  ]
  dora_count = sum(all_kinds34.count(dk) for dk in dora_kinds)
  aka_dora_count = (
      sum(1 for t in all_tokens if is_aka_token(t))
      if ctx.rules.aka_dora
      else 0
  )
  ura_dora_count = 0
  if ctx.is_riichi or ctx.is_double_riichi:
    ura_kinds = [
        token_to_kind34(dora_from_indicator_token(ind))
        for ind in ctx.ura_dora_indicators
    ]
    ura_dora_count = sum(all_kinds34.count(uk) for uk in ura_kinds)

  best_result: AgariResult | None = None

  for decomp in decomps:
    yakuman_names = _evaluate_yakuman(
        closed_tokens_13, all_kinds34, melds, decomp, ctx
    )
    local_yakuman: list[str] = []
    local_regular: list[tuple[str, int]] = []
    for ly_name, enabled in ctx.local_yaku_enabled.items():
      if enabled and ly_name in LOCAL_YAKU_REGISTRY:
        for y_name, val, is_ym in LOCAL_YAKU_REGISTRY[ly_name](
            closed14_tokens, melds, decomp, ctx
        ):
          if is_ym:
            local_yakuman.extend([y_name] * val)
          else:
            local_regular.append((y_name, val))

    total_yakuman = yakuman_names + local_yakuman
    if total_yakuman:
      ym_mult = len(total_yakuman)
      base_pts = calculate_base_points(0, 0, yakuman_multiplier=ym_mult)
      total_pts = base_to_total_win_points(base_pts, ctx.is_dealer, ctx.is_tsumo)
      fu = calculate_fu(decomp, is_menzen, ctx, is_pinfu=False)
      candidate = AgariResult(
          is_valid=True,
          han=13 * ym_mult,
          fu=fu,
          yaku=[(ym, 13) for ym in total_yakuman],
          yakuman=total_yakuman,
          base_points=base_pts,
          total_points=total_pts,
      )
    else:
      reg_yaku, is_pinfu = _evaluate_regular_yaku(
          all_kinds34, is_menzen, decomp, ctx
      )
      reg_yaku.extend(local_regular)
      # ※懸賞役は縛りの飜数には数えない (must have >= 1 han before dora/ippatsu)
      if not reg_yaku:
        continue

      # Add bonus yaku (懸賞役)
      # ※一発と嶺上は複合しない; ※一発と槍槓（連続槓の１回目）は複合する
      if ctx.is_ippatsu and not ctx.is_rinshan:
        reg_yaku.append(("一発", 1))
      if dora_count > 0:
        reg_yaku.append(("ドラ", dora_count))
      if aka_dora_count > 0:
        reg_yaku.append(("赤ドラ", aka_dora_count))
      if ura_dora_count > 0:
        reg_yaku.append(("裏ドラ", ura_dora_count))

      han = sum(h for _, h in reg_yaku)
      fu = calculate_fu(decomp, is_menzen, ctx, is_pinfu=is_pinfu)
      base_pts = calculate_base_points(han, fu, yakuman_multiplier=0)
      total_pts = base_to_total_win_points(base_pts, ctx.is_dealer, ctx.is_tsumo)
      candidate = AgariResult(
          is_valid=True,
          han=han,
          fu=fu,
          yaku=reg_yaku,
          yakuman=[],
          base_points=base_pts,
          total_points=total_pts,
          dora_count=dora_count,
          aka_dora_count=aka_dora_count,
          ura_dora_count=ura_dora_count,
      )

    if best_result is None or (
        candidate.total_points,
        candidate.han,
        candidate.fu,
    ) > (best_result.total_points, best_result.han, best_result.fu):
      best_result = candidate

  return best_result if best_result is not None else AgariResult(is_valid=False)


def compute_score_deltas(
    winner_seat: int,
    loser_seat: int | None,
    dealer_seat: int,
    agari: AgariResult,
    honba: int,
    kyoutaku: int,
    pao_seats: dict[str, int] | None = None,
) -> list[int]:
  """Compute the 4-player point deltas for a single win (including Pao, honba, kyoutaku).

  Per Tenhou rules:
  '大三元/大四喜のパオあり。複合役満を含む得点を、ツモ＝全額・ロン＝折半で支払う。積み棒は包。'
  """
  deltas = [0, 0, 0, 0]
  is_dealer = winner_seat == dealer_seat
  is_tsumo = loser_seat is None
  pao_seats = pao_seats or {}

  # Check if any triggered yakuman has a Pao responsible player
  active_pao_seat: int | None = None
  for ym_name in agari.yakuman:
    if ym_name in pao_seats and pao_seats[ym_name] != winner_seat:
      active_pao_seat = pao_seats[ym_name]
      break

  if active_pao_seat is not None and agari.yakuman:
    # Pao applies to the entire composite yakuman score + honba
    total_win = agari.total_points
    honba_pts = 300 * honba
    if is_tsumo:
      deltas[active_pao_seat] -= total_win + honba_pts
      deltas[winner_seat] += total_win + honba_pts + 1000 * kyoutaku
    else:
      assert loser_seat is not None
      if loser_seat == active_pao_seat:
        deltas[loser_seat] -= total_win + honba_pts
      else:
        half = total_win // 2
        deltas[loser_seat] -= half
        deltas[active_pao_seat] -= (total_win - half) + honba_pts
      deltas[winner_seat] += total_win + honba_pts + 1000 * kyoutaku
    return deltas

  # Normal (non-Pao) payment
  if is_tsumo:
    if is_dealer:
      each = int(math.ceil((agari.base_points * 2) / 100.0) * 100) + 100 * honba
      for s in range(4):
        if s != winner_seat:
          deltas[s] -= each
          deltas[winner_seat] += each
    else:
      oya_pay = (
          int(math.ceil((agari.base_points * 2) / 100.0) * 100) + 100 * honba
      )
      ko_pay = int(math.ceil(agari.base_points / 100.0) * 100) + 100 * honba
      for s in range(4):
        if s == winner_seat:
          continue
        pay = oya_pay if s == dealer_seat else ko_pay
        deltas[s] -= pay
        deltas[winner_seat] += pay
    deltas[winner_seat] += 1000 * kyoutaku
  else:
    assert loser_seat is not None
    pay = agari.total_points + 300 * honba
    deltas[loser_seat] -= pay
    deltas[winner_seat] += pay + 1000 * kyoutaku

  return deltas
