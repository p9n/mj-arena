"""Configuration loader and dataclasses for the Mahjong Server."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass
class RuleConfig:
  """Configurable game rules (Tenhou 4-player Hanchan defaults)."""

  ranking_mode: str = "normal"  # "normal" or "plus_minus_zero" ("正負0")
  kuitan: bool = True
  aka_dora: bool = True
  start_points: int = 25000
  return_points: int = 30000
  uma: list[int] = field(default_factory=lambda: [20, 10, -10, -20])
  tobi_enabled: bool = True
  west_sudden_death: bool = True
  nagashi_mangan: bool = True
  double_ron: bool = True
  triple_ron_draw: bool = True
  pao_daisangen: bool = True
  pao_daisuushii: bool = True
  pao_suukantsu: bool = False
  noten_riichi_chombo_on_draw: bool = True

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> RuleConfig:
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    return cls(**filtered)

  def to_dict(self) -> dict[str, Any]:
    return {
        "ranking_mode": self.ranking_mode,
        "kuitan": self.kuitan,
        "aka_dora": self.aka_dora,
        "start_points": self.start_points,
        "return_points": self.return_points,
        "uma": list(self.uma),
        "tobi_enabled": self.tobi_enabled,
        "west_sudden_death": self.west_sudden_death,
        "nagashi_mangan": self.nagashi_mangan,
        "double_ron": self.double_ron,
        "triple_ron_draw": self.triple_ron_draw,
        "pao_daisangen": self.pao_daisangen,
        "pao_daisuushii": self.pao_daisuushii,
        "pao_suukantsu": self.pao_suukantsu,
        "noten_riichi_chombo_on_draw": self.noten_riichi_chombo_on_draw,
    }


@dataclass
class ServerConfig:
  """Top-level configuration loaded from config.json."""

  timeout_sec: float = 5.0
  rules: RuleConfig = field(default_factory=RuleConfig)
  local_yaku: dict[str, bool] = field(default_factory=dict)
  client_cpus: int = 1
  client_memory_mb: int = 4096
  client_log_dir: str | None = None

  @classmethod
  def load(cls, path: str | Path | None = None) -> ServerConfig:
    if path is None:
      default_path = Path(__file__).resolve().parent.parent / "config.json"
      if default_path.exists():
        path = default_path
      else:
        return cls()
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
      raw = json.load(f)
    timeout_sec = float(raw.get("timeout_sec", 5.0))
    rules = RuleConfig.from_dict(raw.get("rules", {}))
    local_yaku = {
        str(k): bool(v) for k, v in raw.get("local_yaku", {}).items()
    }
    client_cpus = int(raw.get("client_cpus", 1))
    client_memory_mb = int(raw.get("client_memory_mb", 4096))
    return cls(
        timeout_sec=timeout_sec,
        rules=rules,
        local_yaku=local_yaku,
        client_cpus=client_cpus,
        client_memory_mb=client_memory_mb,
    )
