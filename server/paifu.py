"""MPSZ Paifu (牌譜) recorder and JSON serializer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from server.tile import tokens_to_mpsz


@dataclass
class RoundLog:
  """Recorded log for a single round (局)."""

  round_name: str  # e.g. "E1", "S4", "W1"
  honba: int
  kyoutaku: int
  dealer: int
  dice_wall: list[int]
  wall_break_seat: int
  wall_break_stack: int
  dora_indicators: list[str]
  ura_dora_indicators: list[str]
  initial_scores: list[int]
  initial_hands_mpsz: list[str]
  events: list[dict[str, Any]] = field(default_factory=list)
  result: dict[str, Any] = field(default_factory=dict)

  def add_event(self, event: dict[str, Any]) -> None:
    self.events.append(event)

  def to_dict(self) -> dict[str, Any]:
    return {
        "round": self.round_name,
        "honba": self.honba,
        "kyoutaku": self.kyoutaku,
        "dealer": self.dealer,
        "dice_wall": self.dice_wall,
        "wall_break_seat": self.wall_break_seat,
        "wall_break_stack": self.wall_break_stack,
        "dora_indicators": list(self.dora_indicators),
        "ura_dora_indicators": list(self.ura_dora_indicators),
        "initial_scores": list(self.initial_scores),
        "initial_hands_mpsz": list(self.initial_hands_mpsz),
        "events": self.events,
        "result": self.result,
    }


class PaifuRecorder:
  """Records an entire Hanchan match in MPSZ notation."""

  def __init__(
      self,
      seating_info: list[dict[str, Any]],
      dice_seating: tuple[int, int],
      dealer_first_seat: int,
      rules_dict: dict[str, Any],
      seed: int | None = None,
  ) -> None:
    self.match_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    self.seed = seed
    self.seating_info = seating_info
    self.dice_seating = list(dice_seating)
    self.dealer_first_seat = dealer_first_seat
    self.rules_dict = rules_dict
    self.rounds: list[RoundLog] = []
    self.final_results: list[dict[str, Any]] = []

  def start_round(
      self,
      round_name: str,
      honba: int,
      kyoutaku: int,
      dealer: int,
      dice_wall: tuple[int, int],
      wall_break_seat: int,
      wall_break_stack: int,
      dora_indicators: list[str],
      ura_dora_indicators: list[str],
      initial_scores: list[int],
      initial_hands_tokens: dict[int, list[str]],
  ) -> RoundLog:
    rlog = RoundLog(
        round_name=round_name,
        honba=honba,
        kyoutaku=kyoutaku,
        dealer=dealer,
        dice_wall=list(dice_wall),
        wall_break_seat=wall_break_seat,
        wall_break_stack=wall_break_stack,
        dora_indicators=list(dora_indicators),
        ura_dora_indicators=list(ura_dora_indicators),
        initial_scores=list(initial_scores),
        initial_hands_mpsz=[
            tokens_to_mpsz(initial_hands_tokens[s]) for s in range(4)
        ],
    )
    self.rounds.append(rlog)
    return rlog

  def set_final_results(self, final_results: list[dict[str, Any]]) -> None:
    self.final_results = final_results

  def to_dict(self) -> dict[str, Any]:
    return {
        "match_id": self.match_id,
        "seed": self.seed,
        "notation": "MPSZ (0m/0p/0s for red fives, 1z-7z for E/S/W/N/P/F/C)",
        "seating": self.seating_info,
        "dice_seating": self.dice_seating,
        "dealer_first_seat": self.dealer_first_seat,
        "rules": self.rules_dict,
        "rounds": [r.to_dict() for r in self.rounds],
        "final_results": self.final_results,
    }

  def save(self, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
      json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
