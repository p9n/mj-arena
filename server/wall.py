"""Wall construction, dice rolling, wall break (開門), dealing, and dead wall."""

from __future__ import annotations

import random
from server.tile import tile_id_to_token


class Wall:
  """Represents the 136-tile Mahjong wall, dead wall (王牌), and dora indicators."""

  def __init__(
      self,
      rng: random.Random | None = None,
      aka_dora: bool = True,
      custom_tiles: list[int] | None = None,
      dealer_seat: int = 0,
      dice: tuple[int, int] | None = None,
  ) -> None:
    self.rng = rng or random.Random()
    self.aka_dora = aka_dora
    self.dealer_seat = dealer_seat
    self.dice: tuple[int, int] = dice or (
        self.rng.randint(1, 6),
        self.rng.randint(1, 6),
    )
    dice_sum = self.dice[0] + self.dice[1]
    self.wall_break_seat: int = (dealer_seat + dice_sum - 1) % 4
    self.wall_break_stack: int = dice_sum

    if custom_tiles is not None:
      if len(custom_tiles) != 136:
        raise ValueError(f"Expected 136 tiles, got {len(custom_tiles)}")
      ring = list(custom_tiles)
      break_idx = 0
    else:
      ring = list(range(136))
      self.rng.shuffle(ring)
      # Each wall has 17 stacks (34 tiles); break at (wall_break_seat, wall_break_stack)
      break_idx = ((self.wall_break_seat * 17 + self.wall_break_stack) * 2) % 136

    rotated = ring[break_idx:] + ring[:break_idx]
    # First 122 tiles form the live wall (52 for initial deal + 70 for draws)
    # Last 14 tiles form the dead wall (王牌: 4 rinshan, 5 dora, 5 uradora)
    self._live_wall: list[int] = rotated[:122]
    dead_wall: list[int] = rotated[122:]

    self._rinshan_tiles: list[int] = dead_wall[0:4]
    self._dora_indicator_ids: list[int] = dead_wall[4:9]
    self._ura_dora_indicator_ids: list[int] = dead_wall[9:14]

    self.revealed_dora_count: int = 1
    self.pending_kandora_count: int = 0
    self.kans_drawn: int = 0

  def deal_initial_hands(self) -> dict[int, list[int]]:
    """Deal 13 tiles to each of the 4 seats starting from dealer_seat.

    Follows traditional dealing: 3 rounds of 4 tiles (2 stacks) per player,
    then 1 tile (チョンチョン) per player. Leaves 70 tiles in the live wall.
    """
    hands: dict[int, list[int]] = {0: [], 1: [], 2: [], 3: []}
    order = [(self.dealer_seat + i) % 4 for i in range(4)]
    cursor = 0
    for _ in range(3):
      for seat in order:
        hands[seat].extend(self._live_wall[cursor : cursor + 4])
        cursor += 4
    for seat in order:
      hands[seat].append(self._live_wall[cursor])
      cursor += 1
    self._live_wall = self._live_wall[cursor:]
    for seat in range(4):
      hands[seat].sort()
    return hands

  @property
  def remaining_tiles(self) -> int:
    """Number of drawable tiles remaining in the live wall."""
    return len(self._live_wall)

  def draw_tsumo(self) -> int:
    """Draw the next tile from the front of the live wall."""
    if not self._live_wall:
      raise RuntimeError("Cannot draw from empty live wall")
    return self._live_wall.pop(0)

  def draw_rinshan(self) -> int:
    """Draw a replacement tile from the dead wall (嶺上牌) after a Kan.

    Consumes one tile from the tail of the live wall to replenish the dead wall
    so that total remaining drawable tiles decreases by 1.
    """
    if self.kans_drawn >= 4:
      raise RuntimeError("All 4 rinshan tiles have already been drawn")
    if not self._live_wall:
      raise RuntimeError("Cannot draw rinshan when live wall is empty")
    rinshan_tile = self._rinshan_tiles[self.kans_drawn]
    self.kans_drawn += 1
    # Replenish dead wall from the end of live wall
    self._live_wall.pop()
    return rinshan_tile

  def reveal_immediate_kandora(self) -> str:
    """Immediately reveal a new kan-dora indicator (used for Ankan)."""
    if self.revealed_dora_count + self.pending_kandora_count >= 5:
      raise RuntimeError("Maximum 5 dora indicators already reached")
    idx = self.revealed_dora_count
    self.revealed_dora_count += 1
    return tile_id_to_token(
        self._dora_indicator_ids[idx], aka_dora=self.aka_dora
    )

  def queue_deferred_kandora(self) -> None:
    """Queue a deferred kan-dora indicator (used for Daiminkan and Kakan)."""
    if self.revealed_dora_count + self.pending_kandora_count < 5:
      self.pending_kandora_count += 1

  def flush_deferred_kandora(self) -> list[str]:
    """Reveal all queued deferred kan-dora indicators and return their tokens."""
    newly_revealed: list[str] = []
    while self.pending_kandora_count > 0 and self.revealed_dora_count < 5:
      idx = self.revealed_dora_count
      self.revealed_dora_count += 1
      self.pending_kandora_count -= 1
      newly_revealed.append(
          tile_id_to_token(self._dora_indicator_ids[idx], aka_dora=self.aka_dora)
      )
    return newly_revealed

  @property
  def dora_indicators(self) -> list[str]:
    """Return currently revealed dora indicator MPSZ tokens."""
    return [
        tile_id_to_token(tid, aka_dora=self.aka_dora)
        for tid in self._dora_indicator_ids[: self.revealed_dora_count]
    ]

  @property
  def ura_dora_indicators(self) -> list[str]:
    """Return ura-dora indicator MPSZ tokens matching revealed dora count."""
    return [
        tile_id_to_token(tid, aka_dora=self.aka_dora)
        for tid in self._ura_dora_indicator_ids[: self.revealed_dora_count]
    ]
