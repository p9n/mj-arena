"""End-to-end integration tests with 4 real client subprocesses over IPC."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

from server.config import ServerConfig
from server.main import parse_args
from server.match_engine import MatchEngine
from server.tile import parse_mpsz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SCRIPT = str(PROJECT_ROOT / "client" / "simple_client.py")


class IntegrationTest(unittest.TestCase):

  def test_cli_args_defaults_and_partial_clients(self) -> None:
    """Verify --client-cpus=1, --client-memory-mb=4096 defaults, 0~4 --clients, and --iterations / --log-dir."""
    args0 = parse_args([])
    self.assertEqual(args0.client_cpus, 1)
    self.assertEqual(args0.client_memory_mb, 4096)
    self.assertEqual(args0.clients, [])
    self.assertIsNone(args0.iterations)
    self.assertIsNone(args0.log_dir)

    args2 = parse_args(["--clients", "cmd1", "cmd2"])
    self.assertEqual(args2.clients, ["cmd1", "cmd2"])

    args_iter = parse_args(["--iterations", "3", "--log-dir", "logs_out"])
    self.assertEqual(args_iter.iterations, 3)
    self.assertEqual(args_iter.log_dir, "logs_out")
    self.assertIsNone(args_iter.paifu)

    # --log and --log-dir cannot appear together
    with self.assertRaises(SystemExit):
      parse_args(["--log", "a.json", "--log-dir", "logs_out"])
    with self.assertRaises(SystemExit):
      parse_args(["--paifu", "a.json", "--log-dir", "logs_out"])
    # --iterations requires --log-dir
    with self.assertRaises(SystemExit):
      parse_args(["--iterations", "2"])

  def test_full_hanchan_with_four_clients_and_paifu(self) -> None:
    """Run a complete Hanchan match with 4 simple_client.py subprocesses."""

    async def run_match() -> None:
      cfg = ServerConfig.load(PROJECT_ROOT / "config.json")
      cmd = f"{sys.executable} {CLIENT_SCRIPT} --mode normal"
      with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as tmpdir:
        paifu_path = Path(tmpdir) / "test_paifu.json"
        engine = MatchEngine(
            config=cfg,
            client_cmds=[cmd] * 4,
            rng=random.Random(42),
            paifu_path=str(paifu_path),
            seed=42,
        )
        result = await engine.run_match()
        self.assertTrue(paifu_path.exists())
        saved = json.loads(paifu_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["seed"], 42)
        has_tobi = any(item["raw_score"] < 0 for item in saved["final_results"])
        if not has_tobi:
          self.assertGreaterEqual(len(saved["rounds"]), 8)
        else:
          self.assertGreaterEqual(len(saved["rounds"]), 1)
        self.assertEqual(len(saved["final_results"]), 4)
        # Verify at least one round ended with agari (tsumo or ron)
        agari_rounds = [
            r for r in saved["rounds"] if r["result"]["type"] in ("tsumo", "ron")
        ]
        self.assertGreater(len(agari_rounds), 0)
        # Verify MPSZ initial hands in paifu
        for r in saved["rounds"]:
          for h_mpsz in r["initial_hands_mpsz"]:
            toks = parse_mpsz(h_mpsz)
            self.assertEqual(len(toks), 13)
        # Verify sum of unrounded pt is 0.0
        total_pt = round(
            sum(
                item.get("raw_pt", item["final_pt"])
                for item in result["final_results"]
            ),
            1,
        )
        self.assertEqual(total_pt, 0.0)

    asyncio.run(run_match())

  def test_client_timeout_auto_tsumogiri(self) -> None:
    """Verify that a client exceeding timeout_sec is auto-tsumogiri'd without crashing."""

    async def run_timeout_round() -> None:
      cfg = ServerConfig()
      cfg.timeout_sec = 0.25  # Fast timeout for test
      cmds = [
          f"{sys.executable} {CLIENT_SCRIPT} --mode timeout",
          f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
          f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
          f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
      ]
      engine = MatchEngine(
          config=cfg,
          client_cmds=cmds,
          rng=random.Random(7),
      )
      await engine.ipc.start_all()
      try:
        for s in range(4):
          await engine.ipc.send_to(
              s,
              {
                  "type": "match_start",
                  "seat": s,
                  "dealer_first_seat": engine.dealer_first_seat,
                  "dice_seating": list(engine.dice_seating),
                  "rules": cfg.rules.to_dict(),
              },
          )
        from server.round_engine import RoundEngine
        r_engine = RoundEngine(
            config=cfg,
            ipc=engine.ipc,
            paifu=engine.paifu,
            round_wind="E",
            round_number=1,
            honba=0,
            kyoutaku=0,
            dealer_seat=engine.dealer_first_seat,
            scores=[25000] * 4,
            rng=random.Random(7),
        )
        outcome = await r_engine.run()
        self.assertNotEqual(outcome.result_type, "chombo")
      finally:
        await engine.ipc.stop_all()

    asyncio.run(run_timeout_round())

  def test_client_illegal_move_triggers_chombo_and_memory_limit(self) -> None:
    """Verify illegal move (--mode chombo) and memory limit (--mode mem-hog) both trigger Chombo."""

    async def run_chombo_and_mem_test() -> None:
      for mode in ("chombo", "mem-hog"):
        cfg = ServerConfig()
        cfg.client_memory_mb = 128  # 128 MB hard limit; mem-hog tries to allocate 512 MB
        cmds = [
            f"{sys.executable} {CLIENT_SCRIPT} --mode {mode}",
            f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
            f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
            f"{sys.executable} {CLIENT_SCRIPT} --mode tsumogiri",
        ]
        engine = MatchEngine(
            config=cfg,
            client_cmds=cmds,
            rng=random.Random(10),
        )
        await engine.ipc.start_all()
        try:
          from server.round_engine import RoundEngine
          r_engine = RoundEngine(
              config=cfg,
              ipc=engine.ipc,
              paifu=engine.paifu,
              round_wind="E",
              round_number=1,
              honba=0,
              kyoutaku=0,
              dealer_seat=0,
              scores=[25000] * 4,
              rng=random.Random(10),
          )
          outcome = await r_engine.run()
          self.assertEqual(outcome.result_type, "chombo")
          offender = outcome.details["offender"]
          if offender == 0:
            self.assertEqual(outcome.scores_after[offender], 25000 - 12000)
          else:
            self.assertEqual(outcome.scores_after[offender], 25000 - 8000)
        finally:
          await engine.ipc.stop_all()

    asyncio.run(run_chombo_and_mem_test())

  def test_ipc_stderr_drain_and_non_integer_seq_and_respawn(self) -> None:
    """Verify B1/R3 (stderr drain + log), B2/B7 (non-int/missing seq), B3/R1b (stuck stdin), and B4/R1a (respawn match_start)."""

    async def run_ipc_edge_tests() -> None:
      from server.ipc import ClientConnection

      with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as tmpdir:
        stderr_file = Path(tmpdir) / "seat0.stderr"
        # 1. Verbose stderr (>300 KB) + stderr tee log + match_start on respawn (R1a, R3)
        verbose_code = (
            "import json, sys\n"
            "first_type = None\n"
            "for line in sys.stdin:\n"
            "  msg = json.loads(line)\n"
            "  if first_type is None:\n"
            "    first_type = msg.get('type')\n"
            "  if msg.get('die'):\n"
            "    sys.exit(1)\n"
            "  sys.stderr.write('X' * 50000 + '\\n')\n"
            "  sys.stderr.flush()\n"
            "  if 'seq' in msg:\n"
            "    sys.stdout.write(json.dumps({'seq': msg['seq'], 'action': 'pass', 'first_type': first_type}) + '\\n')\n"
            "    sys.stdout.flush()\n"
        )
        conn = ClientConnection(
            seat=0,
            cmd=[sys.executable, "-c", verbose_code],
            stderr_log_path=str(stderr_file),
        )
        conn.match_start_msg = {
            "type": "match_start",
            "seat": 0,
            "timeout_sec": 5.0,
        }
        await conn.start()
        try:
          for i in range(1, 8):
            resp = await conn.request(
                {"type": "discard_notify"}, seq=i, timeout_sec=1.0
            )
            self.assertFalse(resp.timed_out)
            self.assertFalse(resp.is_invalid)

          # Trigger child exit, then call ensure_alive() and verify match_start was re-sent first
          dead_resp = await conn.request(
              {"type": "discard_notify", "die": True}, seq=8, timeout_sec=1.0
          )
          self.assertTrue(dead_resp.is_invalid)
          await conn.ensure_alive()
          resp_after = await conn.request(
              {"type": "discard_notify"}, seq=9, timeout_sec=1.0
          )
          self.assertFalse(resp_after.is_invalid)
          assert resp_after.data is not None
          self.assertEqual(resp_after.data.get("first_type"), "match_start")
          self.assertTrue(stderr_file.exists())
          self.assertGreater(stderr_file.stat().st_size, 300000)
        finally:
          await conn.stop()

      # 2. String seq ("15") and missing seq must return is_invalid=True without killing reader
      bad_seq_code = (
          "import json, sys\n"
          "for line in sys.stdin:\n"
          "  msg = json.loads(line)\n"
          "  if msg.get('seq') == 1:\n"
          "    sys.stdout.write(json.dumps({'seq': '1', 'action': 'pass'}) + '\\n')\n"
          "  elif msg.get('seq') == 2:\n"
          "    sys.stdout.write(json.dumps({'action': 'pass'}) + '\\n')\n"
          "  else:\n"
          "    sys.stdout.write(json.dumps({'seq': msg.get('seq'), 'action': 'pass'}) + '\\n')\n"
          "  sys.stdout.flush()\n"
      )
      conn2 = ClientConnection(seat=1, cmd=[sys.executable, "-c", bad_seq_code])
      await conn2.start()
      try:
        r1 = await conn2.request({"type": "discard_notify"}, seq=1, timeout_sec=1.0)
        self.assertTrue(r1.is_invalid)
        r2 = await conn2.request({"type": "discard_notify"}, seq=2, timeout_sec=1.0)
        self.assertTrue(r2.is_invalid)
        r3 = await conn2.request({"type": "discard_notify"}, seq=3, timeout_sec=1.0)
        self.assertFalse(r3.is_invalid)
        self.assertFalse(r3.timed_out)
      finally:
        await conn2.stop()

      # 3. Stuck client that never reads stdin (B3 / R1b / R4)
      stuck_code = "import time\ntime.sleep(30)\n"
      conn3 = ClientConnection(
          seat=2,
          cmd=[sys.executable, "-c", stuck_code],
          send_timeout_sec=0.25,
      )
      await conn3.start()
      try:
        big_msg = {"type": "filler", "pad": "A" * 65536}
        sent_ok = True
        for _ in range(16):
          if not await conn3.send(big_msg):
            sent_ok = False
            break
        self.assertFalse(sent_ok)
        r_stuck = await conn3.request(
            {"type": "discard_notify"}, seq=1, timeout_sec=0.5
        )
        self.assertTrue(r_stuck.is_invalid)
      finally:
        await conn3.stop()

    asyncio.run(run_ipc_edge_tests())


if __name__ == "__main__":
  unittest.main()
