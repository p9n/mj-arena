"""Single-round (局) state machine implementing Tenhou 4-player rules."""

from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any

from server.agari import (
    AgariResult,
    WinContext,
    compute_score_deltas,
    evaluate_agari,
)
from server.config import ServerConfig
from server.hand import (
    Meld,
    can_ankan_in_riichi,
    get_kuikae_forbidden_kinds,
    get_tenpai_waits,
    is_tenpai,
    kinds_to_all_tokens,
)
from server.ipc import ClientResponse, IPCManager
from server.paifu import PaifuRecorder, RoundLog
from server.tile import (
    DRAGON_KINDS,
    WIND_KINDS,
    WIND_NAMES,
    WIND_TO_KIND34,
    YAOCHUU_KINDS,
    is_valid_token,
    kind34_to_token,
    sort_tokens,
    tile_id_to_token,
    token_to_kind34,
    tokens_to_mpsz,
)
from server.wall import Wall


@dataclass
class PlayerRoundState:
  """Per-player state within a single round."""

  seat: int
  seat_wind: str  # "E", "S", "W", "N"
  seat_wind_kind34: int  # 27..30
  closed_tiles: list[str] = field(default_factory=list)
  melds: list[Meld] = field(default_factory=list)
  river: list[str] = field(default_factory=list)  # All discards (even if called)
  river_was_called: bool = False  # True if any discard from this player was called
  is_riichi: bool = False
  is_double_riichi: bool = False
  is_ippatsu: bool = False
  temp_furiten: bool = False
  riichi_furiten: bool = False
  pao_seats: dict[str, int] = field(default_factory=dict)

  @property
  def is_menzen(self) -> bool:
    return all(not m.is_open for m in self.melds)

  def sort_hand(self) -> None:
    self.closed_tiles = sort_tokens(self.closed_tiles)


@dataclass
class RoundOutcome:
  """Summary of a completed round returned to MatchEngine."""

  result_type: str  # "tsumo", "ron", "chombo", "ryuukyoku_exhaustive", "ryuukyoku_nagashi_mangan", "ryuukyoku_abortive"
  scores_after: list[int]
  score_deltas: list[int]
  renchan: bool
  honba_next: int
  kyoutaku_next: int
  details: dict[str, Any] = field(default_factory=dict)


class RoundEngine:
  """Executes a single round of Japanese Mahjong."""

  def __init__(
      self,
      config: ServerConfig,
      ipc: IPCManager,
      paifu: PaifuRecorder,
      round_wind: str,  # "E", "S", "W"
      round_number: int,  # 1..4
      honba: int,
      kyoutaku: int,
      dealer_seat: int,
      scores: list[int],
      rng: random.Random | None = None,
      custom_wall: Wall | None = None,
  ) -> None:
    self.config = config
    self.rules = config.rules
    self.ipc = ipc
    self.paifu = paifu
    self.round_wind = round_wind
    self.round_wind_kind34 = WIND_TO_KIND34[round_wind]
    self.round_number = round_number
    self.round_name = f"{round_wind}{round_number}"
    self.honba = honba
    self.initial_kyoutaku = kyoutaku
    self.kyoutaku = kyoutaku
    self.dealer_seat = dealer_seat
    self.initial_scores = list(scores)
    self.scores = list(scores)
    self.rng = rng or random.Random()

    self.wall = custom_wall or Wall(
        rng=self.rng,
        aka_dora=self.rules.aka_dora,
        dealer_seat=self.dealer_seat,
    )

    self.players: dict[int, PlayerRoundState] = {}
    for s in range(4):
      rel_wind_idx = (s - self.dealer_seat) % 4
      w_str = WIND_NAMES[rel_wind_idx]
      self.players[s] = PlayerRoundState(
          seat=s,
          seat_wind=w_str,
          seat_wind_kind34=WIND_TO_KIND34[w_str],
      )

    self.uncalled_first_round: bool = True
    self.kans_by_seat: list[int] = []
    self.riichi_sticks_this_round: list[int] = []
    self.round_log: RoundLog | None = None

  def _is_furiten(self, seat: int) -> bool:
    """Return True if `seat` is currently in any form of Furiten."""
    p = self.players[seat]
    if p.temp_furiten or p.riichi_furiten:
      return True
    waits = get_tenpai_waits(p.closed_tiles, p.melds)
    if not waits:
      return False
    river_kinds = {token_to_kind34(t) for t in p.river}
    return bool(waits & river_kinds)

  def _update_missed_win_furiten(
      self, tile_token: str, actor_seat: int, winners: set[int]
  ) -> None:
    """Update temp_furiten and riichi_furiten for players who passed on a winning tile."""
    kind = token_to_kind34(tile_token)
    for s in range(4):
      if s == actor_seat or s in winners:
        continue
      p = self.players[s]
      waits = get_tenpai_waits(p.closed_tiles, p.melds)
      if kind in waits:
        p.temp_furiten = True
        if p.is_riichi:
          p.riichi_furiten = True

  def _record_pao_if_applicable(
      self, caller_seat: int, from_seat: int, meld_kind34: int
  ) -> None:
    """Check if calling a triplet/kan of dragons or winds triggers Pao (責任払い)."""
    p = self.players[caller_seat]
    if self.rules.pao_daisangen and meld_kind34 in DRAGON_KINDS:
      dragon_melds = sum(
          1
          for m in p.melds
          if m.meld_type in ("pon", "daiminkan", "ankan", "kakan")
          and token_to_kind34(m.tiles[0]) in DRAGON_KINDS
      )
      if dragon_melds == 3:
        p.pao_seats["大三元"] = from_seat

    if self.rules.pao_daisuushii and meld_kind34 in WIND_KINDS:
      wind_melds = sum(
          1
          for m in p.melds
          if m.meld_type in ("pon", "daiminkan", "ankan", "kakan")
          and token_to_kind34(m.tiles[0]) in WIND_KINDS
      )
      if wind_melds == 4:
        p.pao_seats["大四喜"] = from_seat

  async def _trigger_chombo(
      self, offender_seat: int, reason: str
  ) -> RoundOutcome:
    """Apply Chombo penalty, refund riichi sticks from this round, and replay same round."""
    # Refund riichi sticks declared during this round
    for s in self.riichi_sticks_this_round:
      self.scores[s] += 1000
    self.kyoutaku = self.initial_kyoutaku

    deltas = [0, 0, 0, 0]
    if offender_seat == self.dealer_seat:
      # Dealer pays 4000 to each of the 3 non-dealers (total 12000)
      deltas[offender_seat] = -12000
      for s in range(4):
        if s != offender_seat:
          deltas[s] = 4000
    else:
      # Non-dealer pays 4000 to dealer, 2000 to each other non-dealer (total 8000)
      deltas[offender_seat] = -8000
      for s in range(4):
        if s == offender_seat:
          continue
        deltas[s] = 4000 if s == self.dealer_seat else 2000

    for s in range(4):
      self.scores[s] += deltas[s]

    net_deltas = [
        self.scores[s] - self.initial_scores[s] for s in range(4)
    ]
    details = {
        "type": "chombo",
        "offender": offender_seat,
        "reason": reason,
        "penalty_deltas": deltas,
        "score_deltas": net_deltas,
        "scores_after": list(self.scores),
    }
    if self.round_log is not None:
      self.round_log.add_event(
          {"type": "chombo", "seat": offender_seat, "reason": reason}
      )
      self.round_log.result = details

    await self.ipc.broadcast({"type": "round_end", **details})
    return RoundOutcome(
        result_type="chombo",
        scores_after=list(self.scores),
        score_deltas=net_deltas,
        renchan=True,
        honba_next=self.honba,  # Honba does not increase on Chombo
        kyoutaku_next=self.kyoutaku,
        details=details,
    )

  async def _broadcast_new_dora(self, new_indicators: list[str]) -> None:
    """Broadcast newly revealed kan-dora indicators and log to paifu."""
    for ind in new_indicators:
      if self.round_log is not None:
        self.round_log.dora_indicators = list(self.wall.dora_indicators)
        self.round_log.ura_dora_indicators = list(self.wall.ura_dora_indicators)
        self.round_log.add_event({"type": "dora", "dora_indicator": ind})
      await self.ipc.broadcast(
          {
              "type": "dora_notify",
              "dora_indicator": ind,
              "dora_indicators": self.wall.dora_indicators,
          }
      )

  async def run(self) -> RoundOutcome:
    """Run the round from deal to completion."""
    await self.ipc.ensure_all_alive()

    raw_hands = self.wall.deal_initial_hands()
    initial_hands_tokens: dict[int, list[str]] = {}
    for s in range(4):
      toks = sort_tokens(
          tile_id_to_token(tid, aka_dora=self.rules.aka_dora)
          for tid in raw_hands[s]
      )
      self.players[s].closed_tiles = toks
      initial_hands_tokens[s] = list(toks)

    self.round_log = self.paifu.start_round(
        round_name=self.round_name,
        honba=self.honba,
        kyoutaku=self.kyoutaku,
        dealer=self.dealer_seat,
        dice_wall=self.wall.dice,
        wall_break_seat=self.wall.wall_break_seat,
        wall_break_stack=self.wall.wall_break_stack,
        dora_indicators=self.wall.dora_indicators,
        ura_dora_indicators=self.wall.ura_dora_indicators,
        initial_scores=self.scores,
        initial_hands_tokens=initial_hands_tokens,
    )

    # Notify each player of round_start with their own private hand
    for s in range(4):
      await self.ipc.send_to(
          s,
          {
              "type": "round_start",
              "round_wind": self.round_wind,
              "round_number": self.round_number,
              "honba": self.honba,
              "kyoutaku": self.kyoutaku,
              "dealer_seat": self.dealer_seat,
              "seat": s,
              "seat_wind": self.players[s].seat_wind,
              "dice_wall": list(self.wall.dice),
              "wall_break_seat": self.wall.wall_break_seat,
              "wall_break_stack": self.wall.wall_break_stack,
              "dora_indicators": self.wall.dora_indicators,
              "points": list(self.scores),
              "hand": list(self.players[s].closed_tiles),
          },
      )

    current_seat = self.dealer_seat
    need_draw = True
    is_rinshan_draw = False
    drawn_token: str | None = None
    forbidden_discards: set[int] = set()

    while True:
      player = self.players[current_seat]

      if need_draw:
        if is_rinshan_draw:
          tid = self.wall.draw_rinshan()
        else:
          tid = self.wall.draw_tsumo()
        drawn_token = tile_id_to_token(tid, aka_dora=self.rules.aka_dora)
        self.round_log.add_event(
            {
                "type": "draw",
                "seat": current_seat,
                "tile": drawn_token,
                "rinshan": is_rinshan_draw,
            }
        )
        # Notify other 3 players that current_seat drew a tile
        await self.ipc.broadcast(
            {
                "type": "player_tsumo",
                "seat": current_seat,
                "is_rinshan": is_rinshan_draw,
                "remaining_tiles": self.wall.remaining_tiles,
            },
            exclude_seat=current_seat,
        )
        resp = await self.ipc.request_one(
            current_seat,
            {
                "type": "turn_tsumo",
                "tile": drawn_token,
                "is_rinshan": is_rinshan_draw,
                "remaining_tiles": self.wall.remaining_tiles,
                "dora_indicators": self.wall.dora_indicators,
                "hand": list(player.closed_tiles),
            },
        )
      else:
        drawn_token = None
        forb_tokens = kinds_to_all_tokens(forbidden_discards)
        resp = await self.ipc.request_one(
            current_seat,
            {
                "type": "turn_after_call",
                "forbidden_discards": forb_tokens,
                "hand": list(player.closed_tiles),
            },
        )

      # Evaluate player's turn response
      if resp.is_invalid:
        return await self._trigger_chombo(current_seat, resp.error_reason)

      if resp.timed_out:
        # Timeout: auto-tsumogiri if drawn_token exists, else rightmost legal tile
        if drawn_token is not None:
          action = "discard"
          action_tile = drawn_token
        else:
          legal_candidates = [
              t
              for t in reversed(player.closed_tiles)
              if token_to_kind34(t) not in forbidden_discards
          ]
          if not legal_candidates:
            return await self._trigger_chombo(
                current_seat, "no legal tile to discard after call"
            )
          action = "discard"
          action_tile = legal_candidates[0]
      else:
        assert resp.data is not None
        action = str(resp.data.get("action", ""))
        action_tile = str(resp.data.get("tile", ""))

      # --- Process Active Turn Action ---
      is_first_turn_for_player = (
          self.uncalled_first_round and len(player.river) == 0
      )

      if action == "tsumo":
        if not need_draw or drawn_token is None:
          return await self._trigger_chombo(
              current_seat, "cannot declare tsumo after a meld call"
          )
        win_ctx = WinContext(
            is_tsumo=True,
            winning_token=drawn_token,
            seat_wind_kind34=player.seat_wind_kind34,
            round_wind_kind34=self.round_wind_kind34,
            is_dealer=(current_seat == self.dealer_seat),
            is_riichi=player.is_riichi,
            is_double_riichi=player.is_double_riichi,
            is_ippatsu=player.is_ippatsu,
            is_rinshan=is_rinshan_draw,
            is_chankan=False,
            is_haitei=(
                self.wall.remaining_tiles == 0 and not is_rinshan_draw
            ),
            is_houtei=False,
            is_first_uncalled_turn=is_first_turn_for_player,
            dora_indicators=self.wall.dora_indicators,
            ura_dora_indicators=self.wall.ura_dora_indicators,
            rules=self.rules,
            local_yaku_enabled=self.config.local_yaku,
            pao_seats=player.pao_seats,
        )
        agari = evaluate_agari(player.closed_tiles, player.melds, win_ctx)
        if not agari.is_valid:
          return await self._trigger_chombo(
              current_seat, "invalid tsumo declaration (no yaku or not agari)"
          )
        return await self._finish_tsumo(current_seat, drawn_token, agari)

      elif action == "kyuushu":
        if not need_draw or drawn_token is None or not is_first_turn_for_player:
          return await self._trigger_chombo(
              current_seat,
              "kyuushu kyuuhai only allowed on first uncalled turn",
          )
        hand14 = player.closed_tiles + [drawn_token]
        yaochuu_types = {
            token_to_kind34(t)
            for t in hand14
            if token_to_kind34(t) in YAOCHUU_KINDS
        }
        if len(yaochuu_types) < 9:
          return await self._trigger_chombo(
              current_seat,
              f"kyuushu kyuuhai requires >= 9 yaochuu types, got {len(yaochuu_types)}",
          )
        player.closed_tiles.append(drawn_token)
        player.sort_hand()
        return await self._finish_abortive_draw(
            "ryuukyoku_kyuushu_kyuuhai", actor_seat=current_seat
        )

      elif action == "ankan":
        if (
            not need_draw
            or drawn_token is None
            or not is_valid_token(action_tile)
        ):
          return await self._trigger_chombo(
              current_seat, "invalid ankan declaration"
          )
        if len(self.kans_by_seat) >= 4 or self.wall.remaining_tiles == 0:
          return await self._trigger_chombo(
              current_seat,
              "cannot declare ankan when 4 kans already exist or wall is empty",
          )
        kan_kind = token_to_kind34(action_tile)
        full_14 = player.closed_tiles + [drawn_token]
        matching_tiles = [
            t for t in full_14 if token_to_kind34(t) == kan_kind
        ]
        if len(matching_tiles) != 4:
          return await self._trigger_chombo(
              current_seat,
              f"ankan requires 4 copies of {action_tile}, found {len(matching_tiles)}",
          )
        if player.is_riichi:
          if not can_ankan_in_riichi(
              player.closed_tiles, drawn_token, player.melds, action_tile
          ):
            return await self._trigger_chombo(
                current_seat,
                "illegal ankan in riichi (changes waits, okurikan, or not tenpai)",
            )

        # Execute Ankan
        player.closed_tiles = sort_tokens(
            t for t in full_14 if token_to_kind34(t) != kan_kind
        )
        meld = Meld(meld_type="ankan", tiles=sort_tokens(matching_tiles))
        player.melds.append(meld)
        self.kans_by_seat.append(current_seat)

        # Clear first round & ippatsu for all players
        self.uncalled_first_round = False
        for p in self.players.values():
          p.is_ippatsu = False

        self.round_log.add_event(
            {
                "type": "ankan",
                "seat": current_seat,
                "tiles": list(meld.tiles),
                "mpsz": meld.to_mpsz(),
            }
        )
        await self.ipc.broadcast(
            {"type": "call_notify", "seat": current_seat, **meld.to_dict()}
        )

        # Flush any deferred kandora from a previous kan in a consecutive kan sequence,
        # then immediately reveal the Ankan's own kandora (暗槓は即乗り)
        deferred = self.wall.flush_deferred_kandora()
        imm_ind = self.wall.reveal_immediate_kandora()
        await self._broadcast_new_dora(deferred + [imm_ind])

        # Draw Rinshan tile on next loop iteration
        need_draw = True
        is_rinshan_draw = True
        forbidden_discards = set()
        continue

      elif action == "kakan":
        if (
            not need_draw
            or drawn_token is None
            or player.is_riichi
            or not is_valid_token(action_tile)
        ):
          return await self._trigger_chombo(
              current_seat, "invalid kakan declaration"
          )
        if len(self.kans_by_seat) >= 4 or self.wall.remaining_tiles == 0:
          return await self._trigger_chombo(
              current_seat,
              "cannot declare kakan when 4 kans already exist or wall is empty",
          )
        full_14 = player.closed_tiles + [drawn_token]
        if action_tile not in full_14:
          return await self._trigger_chombo(
              current_seat, f"kakan tile {action_tile} not in hand"
          )
        kan_kind = token_to_kind34(action_tile)
        pon_idx = next(
            (
                i
                for i, m in enumerate(player.melds)
                if m.meld_type == "pon"
                and token_to_kind34(m.tiles[0]) == kan_kind
            ),
            None,
        )
        if pon_idx is None:
          return await self._trigger_chombo(
              current_seat, f"no matching pon meld for kakan {action_tile}"
          )

        # Remove 1 copy of action_tile from full_14 and upgrade the pon meld to kakan
        full_14.remove(action_tile)
        player.closed_tiles = sort_tokens(full_14)
        old_pon = player.melds[pon_idx]
        kakan_meld = Meld(
            meld_type="kakan",
            tiles=sort_tokens(old_pon.tiles + [action_tile]),
            called_tile=action_tile,
            from_seat=old_pon.from_seat,
        )
        player.melds[pon_idx] = kakan_meld

        self.round_log.add_event(
            {
                "type": "kakan",
                "seat": current_seat,
                "tile": action_tile,
                "tiles": list(kakan_meld.tiles),
                "mpsz": kakan_meld.to_mpsz(),
            }
        )

        # Ask other 3 players in parallel (up to timeout_sec) for Chankan (搶槓)
        # Note: per tenhou-rules.txt:
        # '純正巡消しは発声＆和了打診後（加槓のみ)、嶺上ツモの前'
        # '※一発と槍槓（連続槓の１回目）は複合する'
        chankan_resps = await self.ipc.request_others_parallel(
            current_seat,
            {
                "type": "kakan_notify",
                "seat": current_seat,
                "tile": action_tile,
            },
        )
        ron_seats: list[int] = []
        for offset in (1, 2, 3):
          s = (current_seat + offset) % 4
          c_resp = chankan_resps[s]
          if c_resp.is_invalid:
            return await self._trigger_chombo(s, c_resp.error_reason)
          if c_resp.timed_out or c_resp.data is None:
            continue
          r_act = str(c_resp.data.get("action", "pass"))
          if r_act == "pass":
            continue
          if r_act != "ron":
            return await self._trigger_chombo(
                s, f"illegal response {r_act!r} to kakan_notify"
            )
          # Validate Chankan Ron
          if self._is_furiten(s):
            return await self._trigger_chombo(
                s, "declared chankan ron while in furiten"
            )
          p_other = self.players[s]
          win_ctx = WinContext(
              is_tsumo=False,
              winning_token=action_tile,
              seat_wind_kind34=p_other.seat_wind_kind34,
              round_wind_kind34=self.round_wind_kind34,
              is_dealer=(s == self.dealer_seat),
              is_riichi=p_other.is_riichi,
              is_double_riichi=p_other.is_double_riichi,
              is_ippatsu=p_other.is_ippatsu,
              is_rinshan=False,
              is_chankan=True,
              is_haitei=False,
              is_houtei=False,
              is_first_uncalled_turn=False,
              dora_indicators=self.wall.dora_indicators,
              ura_dora_indicators=self.wall.ura_dora_indicators,
              rules=self.rules,
              local_yaku_enabled=self.config.local_yaku,
              pao_seats=p_other.pao_seats,
          )
          agari = evaluate_agari(p_other.closed_tiles, p_other.melds, win_ctx)
          if not agari.is_valid:
            return await self._trigger_chombo(
                s, "invalid chankan ron declaration"
            )
          ron_seats.append(s)

        self._update_missed_win_furiten(
            action_tile, current_seat, set(ron_seats)
        )

        if len(ron_seats) == 3 and self.rules.triple_ron_draw:
          return await self._finish_abortive_draw(
              "ryuukyoku_sanchahou",
              actor_seat=current_seat,
              extra={"ron_seats": ron_seats, "tile": action_tile},
          )
        if ron_seats:
          return await self._finish_ron(
              ron_seats,
              loser_seat=current_seat,
              winning_token=action_tile,
              is_chankan=True,
              is_houtei=False,
          )

        # Chankan passed! Clear ippatsu & first-round flags right before Rinshan draw
        self.uncalled_first_round = False
        for p in self.players.values():
          p.is_ippatsu = False

        await self.ipc.broadcast(
            {"type": "call_notify", "seat": current_seat, **kakan_meld.to_dict()}
        )

        # Flush any previously queued deferred kandora right before subsequent Rinshan draw,
        # then queue this Kakan's deferred kandora
        prev_deferred = self.wall.flush_deferred_kandora()
        if prev_deferred:
          await self._broadcast_new_dora(prev_deferred)
        self.wall.queue_deferred_kandora()
        self.kans_by_seat.append(current_seat)

        need_draw = True
        is_rinshan_draw = True
        forbidden_discards = set()
        continue

      elif action in ("discard", "riichi"):
        declaring_riichi = action == "riichi"
        if not is_valid_token(action_tile):
          return await self._trigger_chombo(
              current_seat, f"invalid discard tile token: {action_tile!r}"
          )

        if need_draw:
          assert drawn_token is not None
          full_hand = player.closed_tiles + [drawn_token]
        else:
          full_hand = list(player.closed_tiles)

        if action_tile not in full_hand:
          return await self._trigger_chombo(
              current_seat, f"discarded tile {action_tile} not in hand"
          )

        # Check kuikae restriction after Chi/Pon
        if not need_draw and token_to_kind34(action_tile) in forbidden_discards:
          return await self._trigger_chombo(
              current_seat,
              f"discard of {action_tile} violates kuikae (喰い替えなし)",
          )

        # If already in riichi, must tsumogiri the drawn tile
        if player.is_riichi:
          if declaring_riichi:
            return await self._trigger_chombo(
                current_seat, "cannot declare riichi when already in riichi"
            )
          if action_tile != drawn_token:
            return await self._trigger_chombo(
                current_seat,
                f"player in riichi must tsumogiri {drawn_token}, tried {action_tile}",
            )

        # If declaring riichi now, validate public riichi requirements
        # Note (Option B): we do NOT check tenpai at declaration time;
        # Noten Riichi is checked at exhaustive draw (荒牌平局)!
        if declaring_riichi:
          if not need_draw or not player.is_menzen:
            return await self._trigger_chombo(
                current_seat, "riichi requires menzen hand on a draw turn"
            )
          if self.scores[current_seat] < 1000:
            return await self._trigger_chombo(
                current_seat, "riichi requires at least 1000 points"
            )
          if self.wall.remaining_tiles < 4:
            return await self._trigger_chombo(
                current_seat,
                "riichi requires at least 4 tiles remaining in live wall",
            )

        # Right before discarding: flush any deferred kandora from Daiminkan/Kakan
        # ('明槓/加槓は後めくり（打牌または続く嶺上の直前）')
        if self.wall.pending_kandora_count > 0:
          newly_flipped = self.wall.flush_deferred_kandora()
          if newly_flipped:
            await self._broadcast_new_dora(newly_flipped)

        is_tsumogiri = need_draw and (action_tile == drawn_token)
        full_hand.remove(action_tile)
        player.closed_tiles = sort_tokens(full_hand)
        player.river.append(action_tile)

        # Discarding clears temporary furiten (同巡振聴の解消は打牌時),
        # and if already in riichi from a prior turn, clears ippatsu.
        player.temp_furiten = False
        if player.is_riichi:
          player.is_ippatsu = False

        self.round_log.add_event(
            {
                "type": "discard",
                "seat": current_seat,
                "tile": action_tile,
                "tsumogiri": is_tsumogiri,
                "riichi": declaring_riichi,
            }
        )

        # Notify the discarder of their own discard (no response needed)
        await self.ipc.send_to(
            current_seat,
            {
                "type": "discard_ack",
                "seat": current_seat,
                "tile": action_tile,
                "is_tsumogiri": is_tsumogiri,
                "is_riichi": declaring_riichi,
            },
        )

        # Query the other 3 players in parallel (up to timeout_sec)
        call_resps = await self.ipc.request_others_parallel(
            current_seat,
            {
                "type": "discard_notify",
                "seat": current_seat,
                "tile": action_tile,
                "is_tsumogiri": is_tsumogiri,
                "is_riichi": declaring_riichi,
                "remaining_tiles": self.wall.remaining_tiles,
            },
        )

        is_houtei_discard = self.wall.remaining_tiles == 0
        ron_seats: list[int] = []
        pon_kan_call: tuple[int, str, list[str]] | None = None
        chi_call: tuple[int, list[str]] | None = None

        for offset in (1, 2, 3):
          s = (current_seat + offset) % 4
          c_resp = call_resps[s]
          if c_resp.is_invalid:
            return await self._trigger_chombo(s, c_resp.error_reason)
          if c_resp.timed_out or c_resp.data is None:
            continue
          c_act = str(c_resp.data.get("action", "pass"))
          if c_act == "pass":
            continue

          p_other = self.players[s]
          if c_act == "ron":
            if self._is_furiten(s):
              return await self._trigger_chombo(
                  s, "declared ron while in furiten"
              )
            win_ctx = WinContext(
                is_tsumo=False,
                winning_token=action_tile,
                seat_wind_kind34=p_other.seat_wind_kind34,
                round_wind_kind34=self.round_wind_kind34,
                is_dealer=(s == self.dealer_seat),
                is_riichi=p_other.is_riichi,
                is_double_riichi=p_other.is_double_riichi,
                is_ippatsu=p_other.is_ippatsu,
                is_rinshan=False,
                is_chankan=False,
                is_haitei=False,
                is_houtei=is_houtei_discard,
                is_first_uncalled_turn=False,
                dora_indicators=self.wall.dora_indicators,
                ura_dora_indicators=self.wall.ura_dora_indicators,
                rules=self.rules,
                local_yaku_enabled=self.config.local_yaku,
                pao_seats=p_other.pao_seats,
            )
            agari = evaluate_agari(
                p_other.closed_tiles, p_other.melds, win_ctx
            )
            if not agari.is_valid:
              return await self._trigger_chombo(
                  s, "invalid ron declaration (no yaku or not agari)"
              )
            ron_seats.append(s)

          elif c_act == "pon":
            if is_houtei_discard or p_other.is_riichi:
              return await self._trigger_chombo(
                  s, "cannot pon on houtei tile or while in riichi"
              )
            raw_tiles = c_resp.data.get("tiles")
            called_kind = token_to_kind34(action_tile)
            if isinstance(raw_tiles, list) and len(raw_tiles) == 2:
              hand_pair = [str(x) for x in raw_tiles]
              if not all(
                  is_valid_token(x) and token_to_kind34(x) == called_kind
                  for x in hand_pair
              ):
                return await self._trigger_chombo(
                    s, f"pon tiles {hand_pair} do not match {action_tile}"
                )
              temp_hand = list(p_other.closed_tiles)
              for x in hand_pair:
                if x not in temp_hand:
                  return await self._trigger_chombo(
                      s, f"pon tile {x} not in hand"
                  )
                temp_hand.remove(x)
            else:
              matching = [
                  x
                  for x in p_other.closed_tiles
                  if token_to_kind34(x) == called_kind
              ]
              if len(matching) < 2:
                return await self._trigger_chombo(
                    s, f"not enough tiles in hand to pon {action_tile}"
                )
              hand_pair = matching[:2]
              temp_hand = list(p_other.closed_tiles)
              for x in hand_pair:
                temp_hand.remove(x)

            # Ensure player has at least 1 non-kuikae tile left to discard after Pon
            forb = get_kuikae_forbidden_kinds("pon", action_tile, hand_pair)
            if not any(token_to_kind34(x) not in forb for x in temp_hand):
              return await self._trigger_chombo(
                  s, "pon would leave only kuikae-forbidden tiles in hand"
              )
            pon_kan_call = (s, "pon", hand_pair)

          elif c_act == "daiminkan":
            if (
                is_houtei_discard
                or p_other.is_riichi
                or len(self.kans_by_seat) >= 4
            ):
              return await self._trigger_chombo(
                  s,
                  "cannot daiminkan on houtei, in riichi, or when 4 kans exist",
              )
            called_kind = token_to_kind34(action_tile)
            matching = [
                x
                for x in p_other.closed_tiles
                if token_to_kind34(x) == called_kind
            ]
            if len(matching) != 3:
              return await self._trigger_chombo(
                  s, f"daiminkan requires 3 matching tiles for {action_tile}"
              )
            pon_kan_call = (s, "daiminkan", matching)

          elif c_act == "chi":
            if offset != 1:
              return await self._trigger_chombo(
                  s, "chi is only allowed from kamicha (left player)"
              )
            if is_houtei_discard or p_other.is_riichi:
              return await self._trigger_chombo(
                  s, "cannot chi on houtei tile or while in riichi"
              )
            called_kind = token_to_kind34(action_tile)
            if called_kind >= 27:
              return await self._trigger_chombo(s, "cannot chi an honor tile")
            raw_tiles = c_resp.data.get("tiles")
            if not isinstance(raw_tiles, list) or len(raw_tiles) != 2:
              return await self._trigger_chombo(
                  s, "chi requires specifying 2 tiles from hand"
              )
            hand_pair = [str(x) for x in raw_tiles]
            if not all(is_valid_token(x) for x in hand_pair):
              return await self._trigger_chombo(
                  s, f"invalid chi tiles: {hand_pair}"
              )
            temp_hand = list(p_other.closed_tiles)
            for x in hand_pair:
              if x not in temp_hand:
                return await self._trigger_chombo(
                    s, f"chi tile {x} not in hand"
                )
              temp_hand.remove(x)
            ks = sorted(
                [called_kind] + [token_to_kind34(x) for x in hand_pair]
            )
            if (
                ks[2] >= 27
                or (ks[0] // 9) != (ks[2] // 9)
                or ks[1] != ks[0] + 1
                or ks[2] != ks[0] + 2
            ):
              return await self._trigger_chombo(
                  s,
                  f"tiles {hand_pair} + {action_tile} do not form a valid sequence",
              )
            forb = get_kuikae_forbidden_kinds("chi", action_tile, hand_pair)
            if not any(token_to_kind34(x) not in forb for x in temp_hand):
              return await self._trigger_chombo(
                  s, "chi would leave only kuikae-forbidden tiles in hand"
              )
            chi_call = (s, hand_pair)

          else:
            return await self._trigger_chombo(
                s, f"unknown reaction action: {c_act!r}"
            )

        # Update furiten for any tenpai player who did not win on this discard
        self._update_missed_win_furiten(
            action_tile, current_seat, set(ron_seats)
        )

        # Priority 1: Ron
        if len(ron_seats) == 3 and self.rules.triple_ron_draw:
          return await self._finish_abortive_draw(
              "ryuukyoku_sanchahou",
              actor_seat=current_seat,
              extra={"ron_seats": ron_seats, "tile": action_tile},
          )
        if ron_seats:
          # Note: 'リーチ宣言牌で放銃した場合は供託料は発生しない'
          return await self._finish_ron(
              ron_seats,
              loser_seat=current_seat,
              winning_token=action_tile,
              is_chankan=False,
              is_houtei=is_houtei_discard,
          )

        # Discard was not Ron'd! If declaring_riichi, finalize riichi stick deposit
        if declaring_riichi:
          player.is_riichi = True
          player.is_double_riichi = is_first_turn_for_player
          player.is_ippatsu = True
          self.scores[current_seat] -= 1000
          self.kyoutaku += 1
          self.riichi_sticks_this_round.append(current_seat)
          await self.ipc.broadcast(
              {
                  "type": "riichi_accepted",
                  "seat": current_seat,
                  "is_double_riichi": player.is_double_riichi,
                  "kyoutaku": self.kyoutaku,
                  "points": list(self.scores),
              }
          )

        # Check abortive draws that trigger after a discard passes without Ron:
        # 1. 四家立直 (Four Riichi)
        if all(self.players[s].is_riichi for s in range(4)):
          return await self._finish_abortive_draw(
              "ryuukyoku_suuchariichi", actor_seat=current_seat
          )

        # 2. 四槓散了 (Four Kans by 2+ players)
        if len(self.kans_by_seat) == 4 and len(set(self.kans_by_seat)) >= 2:
          return await self._finish_abortive_draw(
              "ryuukyoku_suukaikan", actor_seat=current_seat
          )

        # 3. 四風連打 (Four Winds on 1st uncalled turn)
        if self.uncalled_first_round and all(
            len(self.players[s].river) == 1 for s in range(4)
        ):
          first_kinds = {
              token_to_kind34(self.players[s].river[0]) for s in range(4)
          }
          if len(first_kinds) == 1 and next(iter(first_kinds)) in WIND_KINDS:
            return await self._finish_abortive_draw(
                "ryuukyoku_suufon_renda", actor_seat=current_seat
            )
          # First round of discards is over
          self.uncalled_first_round = False

        # Priority 2: Pon / Daiminkan
        if pon_kan_call is not None:
          caller_seat, call_type, hand_used = pon_kan_call
          caller = self.players[caller_seat]
          for t in hand_used:
            caller.closed_tiles.remove(t)
          caller.sort_hand()
          player.river_was_called = True

          meld = Meld(
              meld_type=call_type,
              tiles=sort_tokens(hand_used + [action_tile]),
              called_tile=action_tile,
              from_seat=current_seat,
          )
          caller.melds.append(meld)
          self._record_pao_if_applicable(
              caller_seat, current_seat, token_to_kind34(action_tile)
          )

          self.uncalled_first_round = False
          for p in self.players.values():
            p.is_ippatsu = False

          self.round_log.add_event(
              {
                  "type": call_type,
                  "seat": caller_seat,
                  "from_seat": current_seat,
                  "called_tile": action_tile,
                  "hand_tiles": list(hand_used),
                  "mpsz": meld.to_mpsz(),
              }
          )
          await self.ipc.broadcast(
              {"type": "call_notify", "seat": caller_seat, **meld.to_dict()}
          )

          current_seat = caller_seat
          if call_type == "daiminkan":
            self.wall.queue_deferred_kandora()
            self.kans_by_seat.append(caller_seat)
            need_draw = True
            is_rinshan_draw = True
            forbidden_discards = set()
          else:
            need_draw = False
            is_rinshan_draw = False
            forbidden_discards = get_kuikae_forbidden_kinds(
                "pon", action_tile, hand_used
            )
          continue

        # Priority 3: Chi
        if chi_call is not None:
          caller_seat, hand_used = chi_call
          caller = self.players[caller_seat]
          for t in hand_used:
            caller.closed_tiles.remove(t)
          caller.sort_hand()
          player.river_was_called = True

          meld = Meld(
              meld_type="chi",
              tiles=sort_tokens(hand_used + [action_tile]),
              called_tile=action_tile,
              from_seat=current_seat,
          )
          caller.melds.append(meld)

          self.uncalled_first_round = False
          for p in self.players.values():
            p.is_ippatsu = False

          self.round_log.add_event(
              {
                  "type": "chi",
                  "seat": caller_seat,
                  "from_seat": current_seat,
                  "called_tile": action_tile,
                  "hand_tiles": list(hand_used),
                  "mpsz": meld.to_mpsz(),
              }
          )
          await self.ipc.broadcast(
              {"type": "call_notify", "seat": caller_seat, **meld.to_dict()}
          )

          current_seat = caller_seat
          need_draw = False
          is_rinshan_draw = False
          forbidden_discards = get_kuikae_forbidden_kinds(
              "chi", action_tile, hand_used
          )
          continue

        # Priority 4: All passed
        if self.wall.remaining_tiles == 0:
          return await self._finish_exhaustive_draw()

        current_seat = (current_seat + 1) % 4
        need_draw = True
        is_rinshan_draw = False
        forbidden_discards = set()

      else:
        return await self._trigger_chombo(
            current_seat, f"unknown turn action: {action!r}"
        )

  async def _finish_tsumo(
      self, winner_seat: int, winning_token: str, agari: AgariResult
  ) -> RoundOutcome:
    """Settle a Tsumo win."""
    winner = self.players[winner_seat]
    deltas = compute_score_deltas(
        winner_seat=winner_seat,
        loser_seat=None,
        dealer_seat=self.dealer_seat,
        agari=agari,
        honba=self.honba,
        kyoutaku=self.kyoutaku,
        pao_seats=winner.pao_seats,
    )
    for s in range(4):
      self.scores[s] += deltas[s]
    self.kyoutaku = 0

    renchan = winner_seat == self.dealer_seat
    honba_next = (self.honba + 1) if renchan else 0
    net_deltas = [
        self.scores[s] - self.initial_scores[s] for s in range(4)
    ]

    details = {
        "type": "tsumo",
        "winner": winner_seat,
        "winning_tile": winning_token,
        "hand_mpsz": tokens_to_mpsz(winner.closed_tiles),
        "melds_mpsz": [m.to_mpsz() for m in winner.melds],
        "han": agari.han,
        "fu": agari.fu,
        "yaku": [{"name": n, "han": h} for n, h in agari.yaku],
        "yakuman": list(agari.yakuman),
        "pao_seats": dict(winner.pao_seats),
        "dora_indicators": self.wall.dora_indicators,
        "ura_dora_indicators": (
            self.wall.ura_dora_indicators if winner.is_riichi else []
        ),
        "score_deltas": net_deltas,
        "scores_after": list(self.scores),
    }
    if self.round_log is not None:
      self.round_log.result = details
    await self.ipc.broadcast({"type": "round_end", **details})
    return RoundOutcome(
        result_type="tsumo",
        scores_after=list(self.scores),
        score_deltas=net_deltas,
        renchan=renchan,
        honba_next=honba_next,
        kyoutaku_next=0,
        details=details,
    )

  async def _finish_ron(
      self,
      ron_seats: list[int],
      loser_seat: int,
      winning_token: str,
      is_chankan: bool,
      is_houtei: bool,
  ) -> RoundOutcome:
    """Settle Single or Double Ron (ダブロンあり。積み棒/供託は上家取り)."""
    # Sort winners counterclockwise from loser_seat
    ordered_winners = sorted(
        ron_seats, key=lambda s: (s - loser_seat) % 4
    )
    if not self.rules.double_ron:
      ordered_winners = ordered_winners[:1]

    win_records: list[dict[str, Any]] = []
    for idx, w_seat in enumerate(ordered_winners):
      w_player = self.players[w_seat]
      win_ctx = WinContext(
          is_tsumo=False,
          winning_token=winning_token,
          seat_wind_kind34=w_player.seat_wind_kind34,
          round_wind_kind34=self.round_wind_kind34,
          is_dealer=(w_seat == self.dealer_seat),
          is_riichi=w_player.is_riichi,
          is_double_riichi=w_player.is_double_riichi,
          is_ippatsu=w_player.is_ippatsu,
          is_rinshan=False,
          is_chankan=is_chankan,
          is_haitei=False,
          is_houtei=is_houtei,
          is_first_uncalled_turn=False,
          dora_indicators=self.wall.dora_indicators,
          ura_dora_indicators=self.wall.ura_dora_indicators,
          rules=self.rules,
          local_yaku_enabled=self.config.local_yaku,
          pao_seats=w_player.pao_seats,
      )
      agari = evaluate_agari(w_player.closed_tiles, w_player.melds, win_ctx)
      # First winner in counterclockwise order from loser takes honba & kyoutaku
      w_honba = self.honba if idx == 0 else 0
      w_kyoutaku = self.kyoutaku if idx == 0 else 0
      deltas = compute_score_deltas(
          winner_seat=w_seat,
          loser_seat=loser_seat,
          dealer_seat=self.dealer_seat,
          agari=agari,
          honba=w_honba,
          kyoutaku=w_kyoutaku,
          pao_seats=w_player.pao_seats,
      )
      for s in range(4):
        self.scores[s] += deltas[s]

      win_records.append(
          {
              "winner": w_seat,
              "loser": loser_seat,
              "winning_tile": winning_token,
              "hand_mpsz": tokens_to_mpsz(w_player.closed_tiles),
              "melds_mpsz": [m.to_mpsz() for m in w_player.melds],
              "han": agari.han,
              "fu": agari.fu,
              "yaku": [{"name": n, "han": h} for n, h in agari.yaku],
              "yakuman": list(agari.yakuman),
              "pao_seats": dict(w_player.pao_seats),
              "ura_dora_indicators": (
                  self.wall.ura_dora_indicators if w_player.is_riichi else []
              ),
          }
      )

    self.kyoutaku = 0
    # ダブロンは親が含まれれば連荘
    renchan = self.dealer_seat in ordered_winners
    honba_next = (self.honba + 1) if renchan else 0
    net_deltas = [
        self.scores[s] - self.initial_scores[s] for s in range(4)
    ]

    details = {
        "type": "ron",
        "loser": loser_seat,
        "winning_tile": winning_token,
        "wins": win_records,
        "dora_indicators": self.wall.dora_indicators,
        "score_deltas": net_deltas,
        "scores_after": list(self.scores),
    }
    if self.round_log is not None:
      self.round_log.result = details
    await self.ipc.broadcast({"type": "round_end", **details})
    return RoundOutcome(
        result_type="ron",
        scores_after=list(self.scores),
        score_deltas=net_deltas,
        renchan=renchan,
        honba_next=honba_next,
        kyoutaku_next=0,
        details=details,
    )

  async def _finish_abortive_draw(
      self, reason: str, actor_seat: int, extra: dict[str, Any] | None = None
  ) -> RoundOutcome:
    """Settle an abortive draw (途中流局: すべて連荘, honba + 1)."""
    net_deltas = [
        self.scores[s] - self.initial_scores[s] for s in range(4)
    ]
    details: dict[str, Any] = {
        "type": "ryuukyoku_abortive",
        "reason": reason,
        "actor_seat": actor_seat,
        "score_deltas": net_deltas,
        "scores_after": list(self.scores),
    }
    if extra:
      details.update(extra)
    if self.round_log is not None:
      self.round_log.result = details
    await self.ipc.broadcast({"type": "round_end", **details})
    return RoundOutcome(
        result_type="ryuukyoku_abortive",
        scores_after=list(self.scores),
        score_deltas=net_deltas,
        renchan=True,
        honba_next=self.honba + 1,
        kyoutaku_next=self.kyoutaku,
        details=details,
    )

  async def _finish_exhaustive_draw(self) -> RoundOutcome:
    """Settle an exhaustive draw (荒牌平局: check Noten Riichi -> Nagashi Mangan -> Tenpai)."""
    # 1. Option B Noten Riichi check:
    # At exhaustive draw, all riichi players must open their hands.
    # If any riichi player is not in tenpai, trigger Chombo immediately!
    if self.rules.noten_riichi_chombo_on_draw:
      for offset in range(4):
        s = (self.dealer_seat + offset) % 4
        p = self.players[s]
        if p.is_riichi and not is_tenpai(p.closed_tiles, p.melds):
          return await self._trigger_chombo(
              s, "noten riichi (ノーテンリーチ) revealed at exhaustive draw"
          )

    tenpai_seats = [
        s
        for s in range(4)
        if is_tenpai(self.players[s].closed_tiles, self.players[s].melds)
    ]
    renchan = self.dealer_seat in tenpai_seats

    # 2. Check Nagashi Mangan (流し満貫)
    # '流し満貫あり。聴牌清算を満貫清算に代替。親の聴牌で連荘。'
    # '流し満貫は、自分の河が鳴かれていない場合に成立。自分が鳴いた場合でも成立。'
    nagashi_seats: list[int] = []
    if self.rules.nagashi_mangan:
      for s in range(4):
        p = self.players[s]
        if (
            p.river
            and not p.river_was_called
            and all(token_to_kind34(t) in YAOCHUU_KINDS for t in p.river)
        ):
          nagashi_seats.append(s)

    if nagashi_seats:
      for ns in nagashi_seats:
        if ns == self.dealer_seat:
          for s in range(4):
            if s != ns:
              self.scores[s] -= 4000
              self.scores[ns] += 4000
        else:
          for s in range(4):
            if s == ns:
              continue
            pay = 4000 if s == self.dealer_seat else 2000
            self.scores[s] -= pay
            self.scores[ns] += pay

      net_deltas = [
          self.scores[s] - self.initial_scores[s] for s in range(4)
      ]
      details = {
          "type": "ryuukyoku_nagashi_mangan",
          "nagashi_seats": nagashi_seats,
          "tenpai_seats": tenpai_seats,
          "score_deltas": net_deltas,
          "scores_after": list(self.scores),
      }
      if self.round_log is not None:
        self.round_log.result = details
      await self.ipc.broadcast({"type": "round_end", **details})
      return RoundOutcome(
          result_type="ryuukyoku_nagashi_mangan",
          scores_after=list(self.scores),
          score_deltas=net_deltas,
          renchan=renchan,
          honba_next=self.honba + 1,
          kyoutaku_next=self.kyoutaku,
          details=details,
      )

    # 3. Standard Tenpai / Noten settlement (3000 points total)
    num_tenpai = len(tenpai_seats)
    if 0 < num_tenpai < 4:
      gain = 3000 // num_tenpai
      loss = 3000 // (4 - num_tenpai)
      for s in range(4):
        if s in tenpai_seats:
          self.scores[s] += gain
        else:
          self.scores[s] -= loss

    net_deltas = [
        self.scores[s] - self.initial_scores[s] for s in range(4)
    ]
    details = {
        "type": "ryuukyoku_exhaustive",
        "tenpai_seats": tenpai_seats,
        "tenpai_hands": {
            s: tokens_to_mpsz(self.players[s].closed_tiles)
            for s in tenpai_seats
        },
        "score_deltas": net_deltas,
        "scores_after": list(self.scores),
    }
    if self.round_log is not None:
      self.round_log.result = details
    await self.ipc.broadcast({"type": "round_end", **details})
    return RoundOutcome(
        result_type="ryuukyoku_exhaustive",
        scores_after=list(self.scores),
        score_deltas=net_deltas,
        renchan=renchan,
        honba_next=self.honba + 1,
        kyoutaku_next=self.kyoutaku,
        details=details,
    )
