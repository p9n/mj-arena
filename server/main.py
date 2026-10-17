"""CLI entry point for the Japanese Mahjong Server."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import random
import sys

# Ensure project root is on sys.path when invoked directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT))

from server.config import ServerConfig
from server.match_engine import MatchEngine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Tenhou-rules 4-player Japanese Mahjong Server"
  )
  parser.add_argument(
      "--config",
      type=str,
      default=str(PROJECT_ROOT / "config.json"),
      help="Path to config.json (default: ./config.json)",
  )
  parser.add_argument(
      "--clients",
      nargs="*",
      default=[],
      help=(
          "0 to 4 client command strings to spawn. If fewer than 4 are given, "
          "remaining seats are filled with `simple_client.py --mode=normal`."
      ),
  )
  parser.add_argument(
      "--client-cpus",
      type=int,
      default=1,
      help="Number of CPU cores per client process (default: 1)",
  )
  parser.add_argument(
      "--client-memory-mb",
      type=int,
      default=4096,
      help="Memory hard limit in MB per client process (default: 4096)",
  )
  parser.add_argument(
      "--client-log-dir",
      type=str,
      default=None,
      help="Optional directory to tee each client's stderr to seat{N}.stderr",
  )
  parser.add_argument(
      "--paifu",
      type=str,
      default=str(PROJECT_ROOT / "paifu.json"),
      help="Output path for MPSZ paifu JSON file",
  )
  parser.add_argument(
      "--seed",
      type=int,
      default=None,
      help="Optional RNG seed for reproducible matches",
  )
  return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
  args = parse_args(argv)
  config = ServerConfig.load(args.config)
  config.client_cpus = args.client_cpus
  config.client_memory_mb = args.client_memory_mb
  config.client_log_dir = args.client_log_dir

  default_client_cmd = (
      f"{sys.executable} {PROJECT_ROOT / 'client' / 'simple_client.py'} --mode=normal"
  )
  provided_clients = list(args.clients or [])
  if len(provided_clients) > 4:
    raise ValueError(
        f"Expected at most 4 client commands, got {len(provided_clients)}."
    )
  client_cmds = provided_clients + [default_client_cmd] * (
      4 - len(provided_clients)
  )

  seed = (
      args.seed
      if args.seed is not None
      else random.SystemRandom().randint(0, 2**32 - 1)
  )
  rng = random.Random(seed)
  engine = MatchEngine(
      config=config,
      client_cmds=client_cmds,
      rng=rng,
      paifu_path=args.paifu,
      seed=seed,
  )
  paifu_data = await engine.run_match()
  print(
      json.dumps(
          {
              "match_id": paifu_data["match_id"],
              "seed": paifu_data["seed"],
              "rounds_played": len(paifu_data["rounds"]),
              "final_results": paifu_data["final_results"],
              "paifu_path": args.paifu,
          },
          ensure_ascii=False,
          indent=2,
      )
  )
  return 0


def main() -> None:
  sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
  main()
