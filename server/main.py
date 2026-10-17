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
      "--tag",
      "--2v2",
      dest="tag",
      action="store_true",
      help="Enable 2v2 Tag Match mode (2 clients, each controlling 2 adjacent seats)",
  )
  parser.add_argument(
      "--game-mode",
      dest="game_mode",
      choices=["4p", "2v2", "tag"],
      default=None,
      help="Game mode: 4p (standard 4-player) or 2v2/tag (2v2 Tag Match)",
  )
  parser.add_argument(
      "--clients",
      nargs="*",
      default=[],
      help=(
          "Client command strings to spawn (0~4 in 4p mode, filled with "
          "`client/simple_client --mode=normal`; 0~2 in 2v2 tag mode, filled "
          "with `client/tag_client --mode=normal`)."
      ),
  )
  parser.add_argument(
      "--client-cpus",
      type=int,
      default=None,
      help="Number of CPU cores per client process (default: 1 in 4p, 2 in 2v2)",
  )
  parser.add_argument(
      "--client-memory-mb",
      type=int,
      default=None,
      help="Memory hard limit in MB per client process (default: 4096 in 4p, 8192 in 2v2)",
  )
  parser.add_argument(
      "--client-log-dir",
      type=str,
      default=None,
      help="Optional directory to tee each client's stderr to seat{N}.stderr",
  )
  parser.add_argument(
      "--iterations",
      type=int,
      default=None,
      help="Number of Hanchan matches to run sequentially (requires --log-dir)",
  )
  log_group = parser.add_mutually_exclusive_group()
  log_group.add_argument(
      "--log",
      "--paifu",
      dest="paifu",
      type=str,
      default=None,
      help="Output path for single MPSZ paifu JSON file (default: ./paifu.json)",
  )
  log_group.add_argument(
      "--log-dir",
      dest="log_dir",
      type=str,
      default=None,
      help="Directory to save paifu-$i.json files when running iterations",
  )
  parser.add_argument(
      "--seed",
      type=int,
      default=None,
      help="Optional RNG seed for reproducible matches",
  )
  args = parser.parse_args(argv)
  is_tag_cli = bool(args.tag or args.game_mode in ("2v2", "tag"))
  args.client_cpus_explicit = args.client_cpus is not None
  args.client_memory_mb_explicit = args.client_memory_mb is not None
  if args.client_cpus is None:
    args.client_cpus = 2 if is_tag_cli else 1
  if args.client_memory_mb is None:
    args.client_memory_mb = 8192 if is_tag_cli else 4096
  if args.iterations is not None:
    if args.iterations < 1:
      parser.error("--iterations must be >= 1")
    if args.log_dir is None:
      parser.error("--log-dir is required when --iterations is specified")
  if args.log_dir is None and args.paifu is None:
    args.paifu = str(PROJECT_ROOT / "paifu.json")
  return args


async def async_main(argv: list[str] | None = None) -> int:
  args = parse_args(argv)
  config = ServerConfig.load(args.config)
  if args.tag or args.game_mode in ("2v2", "tag"):
    config.rules.game_mode = "2v2"
  elif args.game_mode == "4p":
    config.rules.game_mode = "4p"

  if config.rules.is_tag_mode:
    config.client_cpus = args.client_cpus if args.client_cpus_explicit else 2
    config.client_memory_mb = (
        args.client_memory_mb if args.client_memory_mb_explicit else 8192
    )
    default_client_cmd = (
        f"{PROJECT_ROOT / 'client' / 'tag_client'} --mode=normal"
    )
    max_clients = 2
  else:
    config.client_cpus = args.client_cpus
    config.client_memory_mb = args.client_memory_mb
    default_client_cmd = (
        f"{PROJECT_ROOT / 'client' / 'simple_client'} --mode=normal"
    )
    max_clients = 4

  config.client_log_dir = args.client_log_dir

  provided_clients = list(args.clients or [])
  if len(provided_clients) > max_clients:
    raise ValueError(
        f"Expected at most {max_clients} client commands, got {len(provided_clients)}."
    )
  client_cmds = provided_clients + [default_client_cmd] * (
      max_clients - len(provided_clients)
  )

  num_iterations = args.iterations if args.iterations is not None else 1
  if args.log_dir is not None:
    log_dir_path = Path(args.log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(num_iterations):
      seed = (
          (args.seed + i)
          if args.seed is not None
          else random.SystemRandom().randint(0, 2**32 - 1)
      )
      rng = random.Random(seed)
      paifu_file = str(log_dir_path / f"paifu-{i}.json")
      # Each MatchEngine instance re-samples CPU IDs for the 4 seats
      engine = MatchEngine(
          config=config,
          client_cmds=client_cmds,
          rng=rng,
          paifu_path=paifu_file,
          seed=seed,
      )
      paifu_data = await engine.run_match()
      results.append(
          {
              "iteration": i,
              "match_id": paifu_data["match_id"],
              "seed": paifu_data["seed"],
              "rounds_played": len(paifu_data["rounds"]),
              "final_results": paifu_data["final_results"],
              "paifu_path": paifu_file,
          }
      )
    print(
        json.dumps(
            results if num_iterations > 1 else results[0],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0

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
