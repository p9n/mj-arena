"""Unit tests for Tenhou edge-case rules (Chankan, Kuikae, Furiten, Noten Riichi, Sudden Death)."""

from __future__ import annotations

import asyncio
from typing import Any
import unittest

from server.config import ServerConfig
from server.hand import (
    Meld,
    can_ankan_in_riichi,
    get_kuikae_forbidden_kinds,
    get_tenpai_waits,
    is_tenpai,
)
from server.ipc import ClientResponse, IPCManager
from server.match_engine import MatchEngine, gosha_rokuiri
from server.paifu import PaifuRecorder
from server.round_engine import RoundEngine
from server.tile import parse_mpsz, token_to_kind34


class FakeIPC(IPCManager):
  """In-memory mock IPCManager for deterministic round state-machine testing."""

  def __init__(self, script_handler: Any) -> None:
    super().__init__(clients={}, timeout_sec=1.0)
    self.script_handler = script_handler
    self.broadcast_log: list[dict[str, Any]] = []

  async def ensure_all_alive(self) -> None:
    pass

  async def send_to(self, seat: int, message: dict[str, Any]) -> None:
    pass

  async def broadcast(
      self, message: dict[str, Any], exclude_seat: int | None = None
  ) -> None:
    self.broadcast_log.append(message)

  async def request_one(
      self, seat: int, message: dict[str, Any], timeout_sec: float | None = None
  ) -> ClientResponse:
    seq = self.next_seq()
    return await self.script_handler(seat, message, seq)

  async def request_others_parallel(
      self,
      actor_seat: int,
      message: dict[str, Any],
      timeout_sec: float | None = None,
  ) -> dict[int, ClientResponse]:
    seq = self.next_seq()
    res: dict[int, ClientResponse] = {}
    for s in range(4):
      if s != actor_seat:
        res[s] = await self.script_handler(s, message, seq)
    return res


class RulesEdgeTest(unittest.TestCase):

  def test_kuikae_forbidden_kinds(self) -> None:
    # 5m6m calling 4m -> forbids 4m (kind 3) and 7m (kind 6)
    forb = get_kuikae_forbidden_kinds("chi", "4m", ["5m", "6m"])
    self.assertEqual(forb, {token_to_kind34("4m"), token_to_kind34("7m")})

    # 4m6m kanchan calling 0m -> only forbids 5m (kind 4)
    forb_kanchan = get_kuikae_forbidden_kinds("chi", "0m", ["4m", "6m"])
    self.assertEqual(forb_kanchan, {token_to_kind34("5m")})

    # Pon of 0p -> forbids 5p (kind 13)
    forb_pon = get_kuikae_forbidden_kinds("pon", "0p", ["5p", "5p"])
    self.assertEqual(forb_pon, {token_to_kind34("5p")})

  def test_keishiki_tenpai_5th_tile_rule(self) -> None:
    """形式聴牌あり。5枚目を待つ聴牌あり(純手牌で４枚使用していなければ成立)."""
    # 4 copies of 1z in closed hand + tanki on 1z -> NOT tenpai
    hand_invalid = parse_mpsz("123m456p789s1111z")
    self.assertFalse(is_tenpai(hand_invalid, []))

    # 3 copies of 1z in open Pon meld + 1 copy of 1z in closed hand -> VALID tenpai on 1z!
    hand_valid = parse_mpsz("123m456p789s1z")
    melds = [Meld("pon", ["1z", "1z", "1z"], "1z", 1)]
    waits = get_tenpai_waits(hand_valid, melds)
    self.assertEqual(waits, {token_to_kind34("1z")})

  def test_ankan_in_riichi_rules(self) -> None:
    """リーチ後の暗槓は待ちが変わらない場合のみ。送り槓不可."""
    hand13 = parse_mpsz("123m456p23s99s111z")
    # Valid ankan on drawn 1z (waits remain {1s, 4s})
    self.assertTrue(can_ankan_in_riichi(hand13, "1z", [], "1z"))
    # Okurikan (drew 5p when holding four 1z in hand): forbidden
    hand13_four_1z = parse_mpsz("123m45p23s99s1111z")
    self.assertFalse(can_ankan_in_riichi(hand13_four_1z, "6p", [], "1z"))
    # Ankan that changes wait (e.g. 3444m waiting on 2m, 5m, 4m -> after 4444m only waits on 3m)
    hand13_change = parse_mpsz("3444m456p789s111z")
    self.assertFalse(can_ankan_in_riichi(hand13_change, "4m", [], "4m"))

  def test_chankan_and_ippatsu_combination(self) -> None:
    """※一発と槍槓（連続槓の１回目）は複合する; カンドラは後めくり."""

    async def run_scenario() -> None:
      cfg = ServerConfig()
      paifu = PaifuRecorder([], (1, 1), 0, cfg.rules.to_dict())

      async def handler(
          seat: int, msg: dict[str, Any], seq: int
      ) -> ClientResponse:
        mtype = msg["type"]
        if mtype == "turn_tsumo" and seat == 0:
          # Seat 0 declares Kakan on 2p
          return ClientResponse(
              seat=seat, data={"seq": seq, "action": "kakan", "tile": "2p"}
          )
        if mtype == "kakan_notify":
          if seat == 1:
            return ClientResponse(
                seat=seat, data={"seq": seq, "action": "ron"}
            )
          return ClientResponse(
              seat=seat, data={"seq": seq, "action": "pass"}
          )
        return ClientResponse(seat=seat, data={"seq": seq, "action": "pass"})

      ipc = FakeIPC(handler)
      engine = RoundEngine(
          config=cfg,
          ipc=ipc,
          paifu=paifu,
          round_wind="E",
          round_number=1,
          honba=0,
          kyoutaku=1,
          dealer_seat=0,
          scores=[25000, 24000, 25000, 25000],
      )

      orig_deal = engine.wall.deal_initial_hands

      def custom_deal() -> dict[int, list[int]]:
        hands = orig_deal()
        # Seat 0 has an open pon of 2p and will draw 2p (tid 40)
        engine.players[0].melds = [
            Meld("pon", ["2p", "2p", "2p"], "2p", 2)
        ]
        # Seat 1 is in Riichi + Ippatsu waiting on 2p (13p in hand)
        engine.players[1].is_riichi = True
        engine.players[1].is_ippatsu = True
        return hands

      engine.wall.deal_initial_hands = custom_deal  # type: ignore[method-assign]
      engine.wall._live_wall[52] = 40  # Next draw after deal (52 tiles) is 2p (kind 10 -> 40)

      # Override initial hands right after deal by patching players in first request
      orig_send_to = ipc.send_to

      async def setup_hands(seat: int, message: dict[str, Any]) -> None:
        if message.get("type") == "round_start":
          if seat == 0:
            engine.players[0].closed_tiles = parse_mpsz("123m456m789s11z")[:10]
          elif seat == 1:
            engine.players[1].closed_tiles = parse_mpsz("123m456m13p789s55z")
        await orig_send_to(seat, message)

      ipc.send_to = setup_hands  # type: ignore[method-assign]
      outcome = await engine.run()

      self.assertEqual(outcome.result_type, "ron")
      win_info = outcome.details["wins"][0]
      yaku_names = [y["name"] for y in win_info["yaku"]]
      self.assertIn("立直", yaku_names)
      self.assertIn("一発", yaku_names)
      self.assertIn("槍槓", yaku_names)
      # Deferred kandora must NOT have flipped because Chankan interrupted Kakan
      self.assertEqual(len(engine.wall.dora_indicators), 1)

    asyncio.run(run_scenario())

  def test_noten_riichi_option_b_chombo_at_exhaustive_draw(self) -> None:
    """Option B: Noten Riichi is accepted at declaration, and penalized as Chombo at exhaustive draw."""

    async def run_scenario() -> None:
      cfg = ServerConfig()
      paifu = PaifuRecorder([], (1, 1), 0, cfg.rules.to_dict())
      declared_riichi = False

      async def handler(
          seat: int, msg: dict[str, Any], seq: int
      ) -> ClientResponse:
        nonlocal declared_riichi
        if msg["type"] == "turn_tsumo":
          tile = msg["tile"]
          if seat == 1 and not declared_riichi:
            declared_riichi = True
            return ClientResponse(
                seat=seat, data={"seq": seq, "action": "riichi", "tile": tile}
            )
          return ClientResponse(
              seat=seat, data={"seq": seq, "action": "discard", "tile": tile}
          )
        return ClientResponse(seat=seat, data={"seq": seq, "action": "pass"})

      ipc = FakeIPC(handler)
      engine = RoundEngine(
          config=cfg,
          ipc=ipc,
          paifu=paifu,
          round_wind="E",
          round_number=1,
          honba=1,
          kyoutaku=0,
          dealer_seat=0,
          scores=[25000, 25000, 25000, 25000],
      )

      async def setup_noten_hand(seat: int, message: dict[str, Any]) -> None:
        if message.get("type") == "round_start" and seat == 1:
          # Completely disconnected hand (not tenpai)
          engine.players[1].closed_tiles = parse_mpsz("147m147p147s1234z")

      ipc.send_to = setup_noten_hand  # type: ignore[method-assign]
      outcome = await engine.run()

      self.assertEqual(outcome.result_type, "chombo")
      self.assertEqual(outcome.details["offender"], 1)
      # Seat 1 (non-dealer) riichi stick is refunded, then pays 4000 to dealer (0) and 2000 to seats 2, 3
      self.assertEqual(outcome.scores_after, [29000, 17000, 27000, 27000])
      # Honba unchanged (stays 1) and dealer replays (renchan=True)
      self.assertEqual(outcome.honba_next, 1)
      self.assertTrue(outcome.renchan)

    asyncio.run(run_scenario())

  def test_illegal_operations_ignored_with_error_message(self) -> None:
    """Verify illegal turn actions and reaction actions send error to client and are ignored without Chombo."""

    async def run_scenario() -> None:
      cfg = ServerConfig()
      paifu = PaifuRecorder([], (1, 1), 0, cfg.rules.to_dict())
      sent_illegal_turn = False
      sent_illegal_reaction = False
      received_errors: list[tuple[int, dict[str, Any]]] = []

      async def handler(
          seat: int, msg: dict[str, Any], seq: int
      ) -> ClientResponse:
        nonlocal sent_illegal_turn, sent_illegal_reaction
        if msg["type"] == "turn_tsumo":
          if seat == 0 and not sent_illegal_turn:
            sent_illegal_turn = True
            return ClientResponse(
                seat=seat, data={"seq": seq, "action": "discard", "tile": "9z"}
            )
          return ClientResponse(
              seat=seat,
              data={"seq": seq, "action": "discard", "tile": msg["tile"]},
          )
        if msg["type"] == "discard_notify":
          if seat == 2 and not sent_illegal_reaction:
            sent_illegal_reaction = True
            return ClientResponse(
                seat=seat, data={"seq": seq, "action": "ron"}
            )
        return ClientResponse(seat=seat, data={"seq": seq, "action": "pass"})

      ipc = FakeIPC(handler)

      async def record_send_to(seat: int, message: dict[str, Any]) -> None:
        if message.get("type") == "error":
          received_errors.append((seat, message))

      ipc.send_to = record_send_to  # type: ignore[method-assign]
      engine = RoundEngine(
          config=cfg,
          ipc=ipc,
          paifu=paifu,
          round_wind="E",
          round_number=1,
          honba=0,
          kyoutaku=0,
          dealer_seat=0,
          scores=[25000, 25000, 25000, 25000],
      )
      outcome = await engine.run()
      self.assertNotEqual(outcome.result_type, "chombo")
      self.assertEqual(len(received_errors), 2)
      self.assertEqual(received_errors[0][0], 0)
      self.assertIn("invalid discard tile token", received_errors[0][1]["message"])
      self.assertEqual(received_errors[1][0], 2)
      self.assertIn("ron", received_errors[1][1]["message"])

    asyncio.run(run_scenario())

  def test_sudden_death_and_orasu_termination(self) -> None:
    """Verify South 4 and West 1..4 Sudden Death termination rules."""
    cfg = ServerConfig()
    match = MatchEngine(cfg, ["echo"] * 4)
    match.dealer_first_seat = 0

    # South 4 (wind_idx=1, round_number=4), dealer is seat 3:
    # Case 1: Nobody >= 30000 and no renchan -> continues to West 1 (Sudden Death)
    match.scores = [28000, 26000, 24000, 22000]
    self.assertFalse(
        match._should_end_match(
            wind_idx=1,
            round_number=4,
            dealer_seat=3,
            result_type="ryuukyoku_exhaustive",
            renchan=False,
        )
    )

    # Case 2: Seat 0 has 31000, dealer (seat 3) has 22000 and gets renchan -> continues South 4 next honba
    match.scores = [31000, 25000, 22000, 22000]
    self.assertFalse(
        match._should_end_match(
            wind_idx=1,
            round_number=4,
            dealer_seat=3,
            result_type="tsumo",
            renchan=True,
        )
    )

    # Case 3: Dealer (seat 3) reaches 32000 (1st place >= 30000) -> Agari-yame ends match!
    match.scores = [28000, 20000, 20000, 32000]
    self.assertTrue(
        match._should_end_match(
            wind_idx=1,
            round_number=4,
            dealer_seat=3,
            result_type="tsumo",
            renchan=True,
        )
    )

    # Case 4: West 4 ends without renchan even if nobody reached 30000
    match.scores = [25000, 25000, 25000, 25000]
    self.assertTrue(
        match._should_end_match(
            wind_idx=2,
            round_number=4,
            dealer_seat=3,
            result_type="ryuukyoku_exhaustive",
            renchan=False,
        )
    )

  def test_gosha_rokuiri_and_plus_minus_zero_ranking(self) -> None:
    """Verify 五捨六入 (Gosha-Rokuiri) and 正負0 (plus_minus_zero) ranking with ties."""
    self.assertEqual(gosha_rokuiri(5.5), 5)
    self.assertEqual(gosha_rokuiri(5.6), 6)
    self.assertEqual(gosha_rokuiri(-5.5), -5)
    self.assertEqual(gosha_rokuiri(-5.6), -6)
    self.assertEqual(gosha_rokuiri(0.5), 0)
    self.assertEqual(gosha_rokuiri(-0.5), 0)

    cfg = ServerConfig()
    cfg.rules.ranking_mode = "plus_minus_zero"
    match = MatchEngine(cfg, ["echo"] * 4)
    match.dealer_first_seat = 0
    # Raw scores:
    # Seat 0 (raw 1st): 35500 -> (35500-30000)/1000 + 20 + 20 = +45.5 -> 五捨六入 +45
    # Seat 1 (raw 2nd): 21500 -> (21500-30000)/1000 + 10      = +1.5  -> 五捨六入 +1
    # Seat 2 (raw 3rd): 21400 -> (21400-30000)/1000 - 10      = -18.6 -> 五捨六入 -19
    # Seat 3 (raw 4th): 21600 -> wait, let's set scores so two players tie on abs(gosha_pt):
    # Seat 1 (raw 2nd): 21600 -> -8.4 + 10 = +1.6 -> 五捨六入 +2 (abs 2)
    # Seat 2 (raw 3rd): 38500? No, raw 3rd must have raw_score <= raw 2nd.
    # Let's use:
    # Seat 0 (raw 1st): 42500 -> +12.5 + 40 = +52.5 -> +52 (abs 52)
    # Seat 1 (raw 2nd): 22500 -> -7.5 + 10  = +2.5  -> +2  (abs 2)
    # Seat 2 (raw 3rd): 18500 -> -11.5 - 10 = -21.5 -> -21 (abs 21)
    # Seat 3 (raw 4th): 16500 -> -13.5 - 20 = -33.5 -> -33 (abs 33)
    match.scores = [42500, 22500, 18500, 16500]
    standings = match._compute_final_standings()
    self.assertEqual([s["seat"] for s in standings], [1, 2, 3, 0])
    self.assertEqual([s["final_pt"] for s in standings], [2, -21, -33, 52])
    self.assertEqual([s["rank"] for s in standings], [1, 2, 3, 4])

    # Now test tied ranks in 正負0 mode (e.g. +19 and -19 both have abs == 19):
    # Seat 0 (raw 1st): 30000 -> +40.0 -> +40 (abs 40)
    # Seat 1 (raw 2nd): 29000 -> -1.0 + 10 = +9.0 -> +9 (abs 9)
    # Seat 2 (raw 3rd): 21000 -> -9.0 - 10 = -19.0 -> -19 (abs 19)
    # Seat 3 (raw 4th): 20000 -> -10.0 - 20 = -30.0 -> -30 (abs 30)
    # Wait: how to get a tie between raw 2nd (+X) and raw 3rd (-X)?
    # Raw 2nd has raw_score R2 >= R3 (raw 3rd).
    # Raw 2nd pt = (R2 - 30000)/1000 + 10.
    # Raw 3rd pt = (R3 - 30000)/1000 - 10.
    # If R2 = 25000 -> pt = -5 + 10 = +5.0 -> +5 (abs 5)
    # And R3 = 25000 -> pt = -5 - 10 = -15.0 -> -15 (abs 15)
    # What if R1 and R4 tie on abs?
    # R1 = 30000 -> pt = 0 + 40 = +40
    # R4 = 10000 -> pt = -20 - 20 = -40!
    # Check: R1=30000, R2=30000, R3=30000, R4=10000 (sum = 100,000):
    # Seat 0 (R1=30000): +40 (abs 40)
    # Seat 1 (R2=30000): +10 (abs 10)
    # Seat 2 (R3=30000): -10 (abs 10)
    # Seat 3 (R4=10000): -40 (abs 40)
    match.scores = [30000, 30000, 30000, 10000]
    standings_tied = match._compute_final_standings()
    # Seat 1 (+10) and Seat 2 (-10) tie for 1st place (rank 1, 1);
    # Seat 0 (+40) and Seat 3 (-40) tie for 3rd place (rank 3, 3)!
    self.assertEqual([s["seat"] for s in standings_tied], [1, 2, 0, 3])
    self.assertEqual([s["final_pt"] for s in standings_tied], [10, -10, 40, -40])
    self.assertEqual([s["rank"] for s in standings_tied], [1, 1, 3, 3])

  def test_tag_mode_immortal_kamicha_and_shimocha_standings(self) -> None:
    """Verify 2v2 Tag Match: Kamicha (0, 2) cannot die (< 0 no tobi), 4p Uma/Oka pt, and Shimocha-only (1, 3) final ranking."""
    cfg = ServerConfig()
    cfg.rules.game_mode = "2v2"
    cfg.rules.ranking_mode = "normal"
    match = MatchEngine(cfg, ["cmd_a", "cmd_b"], rng=None)
    match.dealer_first_seat = 0

    # Verify Team 0 controls (0, 1) and Team 1 controls (2, 3) sharing 2 connections
    self.assertIs(match.ipc.clients[0], match.ipc.clients[1])
    self.assertIs(match.ipc.clients[2], match.ipc.clients[3])
    self.assertIsNot(match.ipc.clients[0], match.ipc.clients[2])

    # Kamicha (seat 0 and seat 2) having negative score does NOT trigger Tobi!
    match.scores = [-5000, 45000, -2000, 62000]
    self.assertFalse(match._has_tobi())
    self.assertFalse(
        match._should_end_match(
            wind_idx=0,
            round_number=2,
            dealer_seat=1,
            result_type="tsumo",
            renchan=False,
        )
    )

    # Shimocha (seat 1 or seat 3) having negative score DOES trigger Tobi!
    match.scores = [45000, -100, 30000, 25100]
    self.assertTrue(match._has_tobi())
    self.assertTrue(
        match._should_end_match(
            wind_idx=0,
            round_number=2,
            dealer_seat=1,
            result_type="tsumo",
            renchan=False,
        )
    )

    # Check 4-player Uma/Oka calculation and Shimocha-only final standings in normal mode:
    # Scores: Seat 0 = 50000 (4p 1st: +20+20+20 = +60.0)
    #         Seat 1 = 15000 (4p 3rd: -15-10    = -25.0)
    #         Seat 2 = -5000 (4p 4th: -35-20    = -55.0)
    #         Seat 3 = 40000 (4p 2nd: +10+10    = +20.0)
    match.scores = [50000, 15000, -5000, 40000]
    standings_normal = match._compute_final_standings()
    self.assertEqual(len(standings_normal), 2)
    self.assertEqual([s["seat"] for s in standings_normal], [3, 1])
    self.assertEqual([s["rank"] for s in standings_normal], [1, 2])
    self.assertEqual([s["final_pt"] for s in standings_normal], [20.0, -25.0])
    self.assertEqual([s["partner_seat"] for s in standings_normal], [2, 0])
    self.assertEqual([s["partner_raw_score"] for s in standings_normal], [-5000, 50000])
    self.assertEqual([s["partner_final_pt"] for s in standings_normal], [-55.0, 60.0])

    # Now check 2v2 combined with plus_minus_zero (正負0) mode:
    # Seat 1 has raw_pt = -25.0 (gosha = -25, abs = 25)
    # Seat 3 has raw_pt = +20.0 (gosha = +20, abs = 20)
    # Wait: what if Seat 1 is closer to 0 than Seat 3?
    # Let Seat 0 = 45000 (4p 1st: +55.0 -> +55)
    #     Seat 3 = 35000 (4p 2nd: +15.0 -> +15, abs 15)
    #     Seat 1 = 22000 (4p 3rd: -18.0 -> -18, abs 18)
    #     Seat 2 = -2000 (4p 4th: -52.0 -> -52)
    # And what if Seat 1 = 28000 (4p 2nd: +8.0 -> +8, abs 8) and Seat 3 = 40000 (4p 1st: +50.0 -> +50, abs 50)?
    # Then in plus_minus_zero mode, Seat 1 (abs 8) beats Seat 3 (abs 50)!
    cfg.rules.ranking_mode = "plus_minus_zero"
    match.scores = [22000, 28000, 10000, 40000]
    standings_pm0 = match._compute_final_standings()
    self.assertEqual(len(standings_pm0), 2)
    self.assertEqual([s["seat"] for s in standings_pm0], [1, 3])
    self.assertEqual([s["rank"] for s in standings_pm0], [1, 2])
    self.assertEqual([s["final_pt"] for s in standings_pm0], [8, 50])


if __name__ == "__main__":
  unittest.main()
