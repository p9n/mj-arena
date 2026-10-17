"""Full Hanchan (東南戦 + 西入サドンデス) match engine."""

from __future__ import annotations

from pathlib import Path
import random
from typing import Any

from server.config import ServerConfig
from server.ipc import ClientConnection, IPCManager
from server.paifu import PaifuRecorder
from server.round_engine import RoundEngine

ROUND_WINDS = ("E", "S", "W")


def gosha_rokuiri(pt: float) -> int:
  """Apply Japanese Mahjong 五捨六入 (round <= .5 toward 0, >= .6 away from 0)."""
  tenths = int(round(pt * 10))
  abs_tenths = abs(tenths)
  abs_rounded = (abs_tenths + 4) // 10
  return abs_rounded if tenths >= 0 else -abs_rounded


class MatchEngine:
  """Manages a complete 4-player Hanchan game from seating to final settlement."""

  def __init__(
      self,
      config: ServerConfig,
      client_cmds: list[str | list[str]],
      rng: random.Random | None = None,
      paifu_path: str | None = None,
      seed: int | None = None,
  ) -> None:
    self.config = config
    self.rules = config.rules
    self.is_tag_mode = self.rules.is_tag_mode
    expected_clients = 2 if self.is_tag_mode else 4
    if len(client_cmds) != expected_clients:
      raise ValueError(
          f"Expected {expected_clients} client commands, got {len(client_cmds)}"
      )
    if seed is not None:
      self.seed: int | None = seed
      self.rng = rng or random.Random(seed)
    elif rng is None:
      self.seed = random.SystemRandom().randint(0, 2**32 - 1)
      self.rng = random.Random(self.seed)
    else:
      self.seed = None
      self.rng = rng
    self.paifu_path = paifu_path

    indexed_cmds = list(enumerate(client_cmds))
    self.rng.shuffle(indexed_cmds)

    self.seating_info: list[dict[str, Any]] = []
    self.clients: dict[int, ClientConnection] = {}
    if self.is_tag_mode:
      # 2v2 Tag Match: Team 0 controls (Seat 0: Kamicha, Seat 1: Shimocha),
      # Team 1 controls (Seat 2: Kamicha, Seat 3: Shimocha).
      for team_idx, (orig_idx, cmd) in enumerate(indexed_cmds):
        kamicha_seat = team_idx * 2
        shimocha_seat = team_idx * 2 + 1
        stderr_log_path = None
        if config.client_log_dir:
          stderr_log_path = str(
              Path(config.client_log_dir) / f"team{team_idx}.stderr"
          )
        conn = ClientConnection(
            seat=kamicha_seat,
            cmd=cmd,
            client_cpus=config.client_cpus,
            client_memory_mb=config.client_memory_mb,
            send_timeout_sec=min(2.0, config.timeout_sec),
            stderr_log_path=stderr_log_path,
        )
        self.clients[kamicha_seat] = conn
        self.clients[shimocha_seat] = conn
        for seat, role, partner in (
            (kamicha_seat, "kamicha", shimocha_seat),
            (shimocha_seat, "shimocha", kamicha_seat),
        ):
          self.seating_info.append(
              {
                  "seat": seat,
                  "team": team_idx,
                  "role": role,
                  "partner_seat": partner,
                  "client_index": orig_idx,
                  "client_cmd": conn.cmd_str,
              }
          )
    else:
      # Standard 4p: randomly assign the 4 client processes to seats 0, 1, 2, 3
      for seat, (orig_idx, cmd) in enumerate(indexed_cmds):
        stderr_log_path = None
        if config.client_log_dir:
          stderr_log_path = str(
              Path(config.client_log_dir) / f"seat{seat}.stderr"
          )
        conn = ClientConnection(
            seat=seat,
            cmd=cmd,
            client_cpus=config.client_cpus,
            client_memory_mb=config.client_memory_mb,
            send_timeout_sec=min(2.0, config.timeout_sec),
            stderr_log_path=stderr_log_path,
        )
        self.clients[seat] = conn
        self.seating_info.append(
            {
                "seat": seat,
                "client_index": orig_idx,
                "client_cmd": conn.cmd_str,
            }
        )

    # Roll 2d6 to determine the starting dealer (起家) for East 1
    self.dice_seating: tuple[int, int] = (
        self.rng.randint(1, 6),
        self.rng.randint(1, 6),
    )
    self.dealer_first_seat: int = (
        self.dice_seating[0] + self.dice_seating[1] - 1
    ) % 4

    for info in self.seating_info:
      s = int(info["seat"])
      info["initial_east_order"] = (s - self.dealer_first_seat) % 4

    self.ipc = IPCManager(self.clients, timeout_sec=config.timeout_sec)
    self.paifu = PaifuRecorder(
        seating_info=self.seating_info,
        dice_seating=self.dice_seating,
        dealer_first_seat=self.dealer_first_seat,
        rules_dict=self.rules.to_dict(),
        seed=self.seed,
    )

    self.scores: list[int] = [self.rules.start_points] * 4
    self.kyoutaku: int = 0
    self.honba: int = 0

  def _get_dealer_seat(self, round_number: int) -> int:
    """Return the dealer seat for `round_number` (1..4)."""
    return (self.dealer_first_seat + round_number - 1) % 4

  def _rank_seats(self, scores: list[int]) -> list[int]:
    """Return seats ordered from 1st to 4th place (ties broken by East 1 wind order)."""
    return sorted(
        range(4),
        key=lambda s: (-scores[s], (s - self.dealer_first_seat) % 4),
    )

  def _has_tobi(self) -> bool:
    """Return True if a mortal player's score is strictly negative (< 0)."""
    if not self.rules.tobi_enabled:
      return False
    if self.rules.is_tag_mode:
      # In 2v2 tag mode, Kamicha seats (0 and 2) are immortal and cannot bust;
      # only Shimocha seats (1 and 3) can trigger Tobi.
      return any(self.scores[s] < 0 for s in (1, 3))
    return any(sc < 0 for sc in self.scores)

  def _should_end_match(
      self,
      wind_idx: int,
      round_number: int,
      dealer_seat: int,
      result_type: str,
      renchan: bool,
  ) -> bool:
    """Evaluate Tenhou Hanchan / Sudden Death termination rules after a round."""
    # 1. Tobi (飛び終了: negative points ends match immediately; 0 continues)
    if self._has_tobi():
      return True

    # 2. Chombo replays the same round (unless tobi occurred above)
    if result_type == "chombo":
      return False

    # 3. East 1..4 and South 1..3 always continue (unless tobi)
    if wind_idx == 0 or (wind_idx == 1 and round_number < 4):
      return False

    # 4. South 4 (All-Last) or West 1..4 (Sudden Death)
    ranked = self._rank_seats(self.scores)
    top_seat = ranked[0]
    has_return_pts = any(sc >= self.rules.return_points for sc in self.scores)
    dealer_is_top_with_return = (
        top_seat == dealer_seat
        and self.scores[dealer_seat] >= self.rules.return_points
    )

    # Check renchan case:
    # '連荘○ 親が原点以上のトップなら和了止め/聴牌止め、他は連荘'
    if renchan:
      if result_type in (
          "tsumo",
          "ron",
          "ryuukyoku_exhaustive",
          "ryuukyoku_nagashi_mangan",
      ):
        if (wind_idx == 1 and round_number == 4) or wind_idx == 2:
          if dealer_is_top_with_return:
            return True
      return False

    # Check non-renchan case (連荘×):
    # '連荘× 最大局数以内で原点以上が存在しなければ続行、他は終了'
    if has_return_pts:
      return True

    # Nobody has >= return_points (30000):
    if not self.rules.west_sudden_death:
      return True
    # Maximum round for 4-player Hanchan is West 4 (wind_idx == 2, round_number == 4)
    if wind_idx == 2 and round_number == 4:
      return True
    return False

  def _compute_final_standings(self) -> list[dict[str, Any]]:
    """Compute final standings, leftover kyoutaku distribution, and Uma/Oka pt."""
    ranked_seats = self._rank_seats(self.scores)
    final_scores = list(self.scores)
    # 終了時の供託はトップ取り
    if self.kyoutaku > 0:
      final_scores[ranked_seats[0]] += 1000 * self.kyoutaku
      self.kyoutaku = 0

    oka_pt = ((self.rules.return_points - self.rules.start_points) * 4) / 1000.0
    standings: list[dict[str, Any]] = []
    for rank_idx, seat in enumerate(ranked_seats):
      raw = final_scores[seat]
      uma_pt = float(self.rules.uma[rank_idx])
      bonus_oka = oka_pt if rank_idx == 0 else 0.0
      pt = round((raw - self.rules.return_points) / 1000.0 + uma_pt + bonus_oka, 1)
      standings.append(
          {
              "rank": rank_idx + 1,
              "seat": seat,
              "raw_score": raw,
              "final_pt": pt,
          }
      )

    is_pm0 = self.rules.ranking_mode in ("plus_minus_zero", "正負0", "pm0")
    if is_pm0:
      for item in standings:
        raw_pt = float(item["final_pt"])
        item["raw_pt"] = raw_pt
        item["final_pt"] = gosha_rokuiri(raw_pt)

    if self.rules.is_tag_mode:
      by_seat = {int(item["seat"]): dict(item) for item in standings}
      shimocha_items = [by_seat[1], by_seat[3]]
      for item in shimocha_items:
        s = int(item["seat"])
        p_seat = s - 1
        p_info = by_seat[p_seat]
        item["team"] = 0 if s == 1 else 1
        item["partner_seat"] = p_seat
        item["partner_raw_score"] = p_info["raw_score"]
        item["partner_final_pt"] = p_info["final_pt"]
        if "raw_pt" in p_info:
          item["partner_raw_pt"] = p_info["raw_pt"]

      if is_pm0:
        shimocha_items.sort(
            key=lambda item: (
                abs(int(item["final_pt"])),
                (int(item["seat"]) - self.dealer_first_seat) % 4,
            )
        )
        shimocha_items[0]["rank"] = 1
        if abs(int(shimocha_items[1]["final_pt"])) == abs(
            int(shimocha_items[0]["final_pt"])
        ):
          shimocha_items[1]["rank"] = 1
        else:
          shimocha_items[1]["rank"] = 2
      else:
        shimocha_items.sort(
            key=lambda item: (
                -int(item["raw_score"]),
                (int(item["seat"]) - self.dealer_first_seat) % 4,
            )
        )
        shimocha_items[0]["rank"] = 1
        shimocha_items[1]["rank"] = 2
      return shimocha_items

    if is_pm0:
      standings.sort(
          key=lambda item: (
              abs(int(item["final_pt"])),
              (int(item["seat"]) - self.dealer_first_seat) % 4,
          )
      )
      for idx, item in enumerate(standings):
        if idx == 0:
          item["rank"] = 1
        elif abs(int(item["final_pt"])) == abs(int(standings[idx - 1]["final_pt"])):
          item["rank"] = standings[idx - 1]["rank"]
        else:
          item["rank"] = idx + 1

    return standings

  async def run_match(self) -> dict[str, Any]:
    """Start client processes, run the Hanchan match, save paifu, and stop clients."""
    await self.ipc.start_all()
    try:
      for s in range(4):
        match_start_msg: dict[str, Any] = {
            "type": "match_start",
            "seat": s,
            "target_seat": s,
            "dealer_first_seat": self.dealer_first_seat,
            "dice_seating": list(self.dice_seating),
            "timeout_sec": self.config.timeout_sec,
            "rules": self.rules.to_dict(),
        }
        if self.rules.is_tag_mode:
          match_start_msg["team"] = 0 if s in (0, 1) else 1
          match_start_msg["role"] = "kamicha" if s in (0, 2) else "shimocha"
          match_start_msg["partner_seat"] = (s + 1) if s in (0, 2) else (s - 1)
          match_start_msg["controlled_seats"] = (
              [0, 1] if s in (0, 1) else [2, 3]
          )
        if s in self.clients:
          self.clients[s].match_start_msg = match_start_msg
          self.clients[s].match_start_msgs.append(match_start_msg)
        await self.ipc.send_to(s, match_start_msg)

      wind_idx = 0  # 0: E, 1: S, 2: W
      round_number = 1  # 1..4
      consecutive_chombos = 0
      end_reason = "all_last"

      while True:
        dealer_seat = self._get_dealer_seat(round_number)
        round_engine = RoundEngine(
            config=self.config,
            ipc=self.ipc,
            paifu=self.paifu,
            round_wind=ROUND_WINDS[wind_idx],
            round_number=round_number,
            honba=self.honba,
            kyoutaku=self.kyoutaku,
            dealer_seat=dealer_seat,
            scores=self.scores,
            rng=self.rng,
        )
        outcome = await round_engine.run()
        self.scores = list(outcome.scores_after)
        self.kyoutaku = outcome.kyoutaku_next
        self.honba = outcome.honba_next

        if outcome.result_type == "chombo":
          consecutive_chombos += 1
          if consecutive_chombos >= 16:
            end_reason = "chombo_cap"
            break
        else:
          consecutive_chombos = 0

        if self._should_end_match(
            wind_idx=wind_idx,
            round_number=round_number,
            dealer_seat=dealer_seat,
            result_type=outcome.result_type,
            renchan=outcome.renchan,
        ):
          if self._has_tobi():
            end_reason = "tobi"
          else:
            end_reason = "all_last"
          break

        if not outcome.renchan:
          if round_number < 4:
            round_number += 1
          else:
            wind_idx += 1
            round_number = 1

      standings = self._compute_final_standings()
      self.paifu.set_final_results(standings)
      if self.paifu_path:
        self.paifu.save(self.paifu_path)

      await self.ipc.broadcast(
          {
              "type": "match_end",
              "end_reason": end_reason,
              "final_results": standings,
              "paifu_path": self.paifu_path,
          }
      )
      return self.paifu.to_dict()
    finally:
      await self.ipc.stop_all()
