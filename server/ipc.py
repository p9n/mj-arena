"""IPC management, subprocess resource limiting (CPU/Memory), and async JSONL I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import functools
import json
import os
import resource
import shlex
import shutil
import subprocess
from typing import Any


def _make_preexec_fn(memory_limit_bytes: int) -> Any:
  """Create a preexec_fn for subprocess launch that sets RLIMIT_AS."""

  def _apply_limits() -> None:
    if memory_limit_bytes > 0:
      try:
        resource.setrlimit(
            resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes)
        )
      except (ValueError, OSError):
        pass

  return _apply_limits


@functools.lru_cache(maxsize=1)
def _can_use_systemd_cpu_quota() -> bool:
  """Return True if `systemd-run --user --scope -p CPUQuota=...` is supported."""
  if shutil.which("systemd-run") is None:
    return False
  try:
    res = subprocess.run(
        ["systemd-run", "--user", "--scope", "-q", "-p", "CPUQuota=100%", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3.0,
        check=False,
    )
    return res.returncode == 0
  except Exception:  # pylint: disable=broad-except
    return False


@dataclass
class ClientResponse:
  """Represents a client's response (or timeout / invalid / EOF)."""

  seat: int
  timed_out: bool = False
  is_invalid: bool = False
  error_reason: str = ""
  data: dict[str, Any] | None = None


class ClientConnection:
  """Manages a single client subprocess with CPU/Memory limits and async JSONL I/O."""

  def __init__(
      self,
      seat: int,
      cmd: str | list[str],
      client_cpus: int = 1,
      client_memory_mb: int = 4096,
      send_timeout_sec: float = 2.0,
      stderr_log_path: str | None = None,
  ) -> None:
    self.seat = seat
    self.cmd_list = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    self.cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    self.client_cpus = client_cpus
    self.client_memory_mb = client_memory_mb
    self.send_timeout_sec = send_timeout_sec
    self.stderr_log_path = stderr_log_path
    self.memory_limit_bytes = client_memory_mb * 1024 * 1024

    self.proc: asyncio.subprocess.Process | None = None
    self._reader_task: asyncio.Task[None] | None = None
    self._stderr_task: asyncio.Task[None] | None = None
    self._pending_futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
    self._write_lock = asyncio.Lock()
    self._eof: bool = False
    self.match_start_msg: dict[str, Any] | None = None
    self.match_start_msgs: list[dict[str, Any]] = []

  def _fail_all_pending(self, reason: str) -> None:
    for fut in list(self._pending_futures.values()):
      if not fut.done():
        fut.set_result({"_invalid": True, "_reason": reason})

  def _fail_oldest_pending(self, reason: str) -> None:
    for seq in sorted(self._pending_futures.keys()):
      fut = self._pending_futures[seq]
      if not fut.done():
        fut.set_result({"_invalid": True, "_reason": reason})
        return

  async def start(self) -> None:
    """Spawn the client subprocess with OS-scheduled CPUQuota and memory limits."""
    await self.stop()
    self._eof = False
    self._pending_futures.clear()
    preexec = _make_preexec_fn(self.memory_limit_bytes)
    exec_cmd = list(self.cmd_list)
    if self.client_cpus > 0 and _can_use_systemd_cpu_quota():
      quota_pct = self.client_cpus * 100
      exec_cmd = [
          "systemd-run",
          "--user",
          "--scope",
          "-q",
          "-p",
          f"CPUQuota={quota_pct}%",
          "--",
          *exec_cmd,
      ]
    self.proc = await asyncio.create_subprocess_exec(
        *exec_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=preexec,
    )
    self._reader_task = asyncio.create_task(self._read_loop())
    self._stderr_task = asyncio.create_task(self._drain_stderr())

  async def ensure_alive(self) -> None:
    """Restart the client subprocess if it has terminated or hit EOF, resending match_start."""
    if (
        self.proc is None
        or self.proc.returncode is not None
        or self._eof
    ):
      await self.start()
      if self.match_start_msgs:
        for msg in self.match_start_msgs:
          await self.send(msg)
      elif self.match_start_msg is not None:
        await self.send(self.match_start_msg)

  async def stop(self) -> None:
    """Terminate the client subprocess and clean up background tasks."""
    for task in (self._reader_task, self._stderr_task):
      if task is not None:
        task.cancel()
        try:
          await task
        except (asyncio.CancelledError, Exception):  # pylint: disable=broad-except
          pass
    self._reader_task = None
    self._stderr_task = None

    if self.proc is not None:
      if self.proc.returncode is None:
        try:
          self.proc.terminate()
          await asyncio.wait_for(self.proc.wait(), timeout=1.0)
        except (ProcessLookupError, asyncio.TimeoutError):
          try:
            self.proc.kill()
            await self.proc.wait()
          except ProcessLookupError:
            pass
      self.proc = None

  async def _drain_stderr(self) -> None:
    """Continuously drain client stderr (and optionally tee to stderr_log_path) so logging never blocks."""
    if self.proc is None or self.proc.stderr is None:
      return
    log_file = None
    try:
      if self.stderr_log_path:
        os.makedirs(os.path.dirname(os.path.abspath(self.stderr_log_path)), exist_ok=True)
        log_file = open(self.stderr_log_path, "ab")
      while True:
        chunk = await self.proc.stderr.read(4096)
        if not chunk:
          break
        if log_file is not None:
          log_file.write(chunk)
          log_file.flush()
    except (asyncio.CancelledError, Exception):  # pylint: disable=broad-except
      pass
    finally:
      if log_file is not None:
        try:
          log_file.close()
        except OSError:
          pass

  async def _read_loop(self) -> None:
    """Continuously read JSON lines from client stdout and match against pending seq."""
    assert self.proc is not None and self.proc.stdout is not None
    try:
      while True:
        try:
          line_bytes = await self.proc.stdout.readline()
        except Exception as exc:  # pylint: disable=broad-except
          self._eof = True
          self._fail_all_pending(f"stdout read error: {exc}")
          break

        if not line_bytes:
          self._eof = True
          self._fail_all_pending(
              "client process closed stdout (EOF/OOM/crash)"
          )
          break

        line_str = line_bytes.decode("utf-8", errors="replace").strip()
        if not line_str:
          continue

        # If no request is currently awaiting a response from this client, ignore stray output
        if not any(not f.done() for f in self._pending_futures.values()):
          continue

        try:
          parsed = json.loads(line_str)
        except json.JSONDecodeError as exc:
          self._fail_oldest_pending(f"malformed JSON: {exc}")
          continue

        if not isinstance(parsed, dict):
          self._fail_oldest_pending("JSON response must be an object")
          continue

        msg_seq = parsed.get("seq")
        if not isinstance(msg_seq, int) or isinstance(msg_seq, bool):
          self._fail_oldest_pending(
              f"missing or non-integer seq: {msg_seq!r}"
          )
          continue

        active_seqs = [
            s for s, f in self._pending_futures.items() if not f.done()
        ]
        if not active_seqs:
          continue

        if msg_seq in self._pending_futures and not self._pending_futures[msg_seq].done():
          self._pending_futures[msg_seq].set_result(parsed)
        elif msg_seq < min(active_seqs):
          # Stale response from an earlier timed-out turn; discard it
          continue
        else:
          self._fail_oldest_pending(
              f"mismatched seq: expected {min(active_seqs)}, got {msg_seq}"
          )
    except asyncio.CancelledError:
      raise
    except Exception as exc:  # pylint: disable=broad-except
      self._eof = True
      self._fail_all_pending(f"reader error: {exc}")

  async def send(
      self, message: dict[str, Any], timeout_sec: float | None = None
  ) -> bool:
    """Send a JSON line message to the client with a drain timeout."""
    if self.proc is None or self.proc.stdin is None or self._eof:
      return False
    t = self.send_timeout_sec if timeout_sec is None else timeout_sec
    try:
      data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
      async with self._write_lock:
        if self.proc is None or self.proc.stdin is None or self._eof:
          return False
        self.proc.stdin.write(data)
        await asyncio.wait_for(self.proc.stdin.drain(), timeout=t)
      return True
    except (
        BrokenPipeError,
        ConnectionResetError,
        OSError,
        asyncio.TimeoutError,
    ):
      self._eof = True
      if self.proc is not None and self.proc.returncode is None:
        try:
          self.proc.kill()
        except ProcessLookupError:
          pass
      return False

  async def request(
      self,
      message: dict[str, Any],
      seq: int,
      timeout_sec: float,
      target_seat: int | None = None,
  ) -> ClientResponse:
    """Send a request with `seq` and wait up to `timeout_sec` for a response."""
    res_seat = self.seat if target_seat is None else target_seat
    msg_with_seq = dict(message)
    msg_with_seq["seq"] = seq
    if target_seat is not None:
      msg_with_seq["target_seat"] = target_seat

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    self._pending_futures[seq] = fut

    if self._eof or not await self.send(msg_with_seq):
      self._pending_futures.pop(seq, None)
      return ClientResponse(
          seat=res_seat,
          is_invalid=True,
          error_reason="client disconnected or crashed before request",
      )

    try:
      result = await asyncio.wait_for(fut, timeout=timeout_sec)
    except asyncio.TimeoutError:
      return ClientResponse(seat=res_seat, timed_out=True)
    finally:
      self._pending_futures.pop(seq, None)

    if result.get("_invalid"):
      return ClientResponse(
          seat=res_seat,
          is_invalid=True,
          error_reason=str(result.get("_reason", "invalid response")),
      )
    return ClientResponse(seat=res_seat, data=result)


class IPCManager:
  """Manages all 4 seated ClientConnections and provides broadcast/parallel query APIs."""

  def __init__(self, clients: dict[int, ClientConnection], timeout_sec: float = 5.0) -> None:
    self.clients = clients
    self.timeout_sec = timeout_sec
    self._seq_counter = 0

  def _unique_connections(
      self, exclude_seat: int | None = None
  ) -> list[ClientConnection]:
    seen: set[int] = set()
    unique: list[ClientConnection] = []
    for s, conn in self.clients.items():
      if s == exclude_seat:
        continue
      if id(conn) not in seen:
        seen.add(id(conn))
        unique.append(conn)
    return unique

  def next_seq(self) -> int:
    self._seq_counter += 1
    return self._seq_counter

  async def start_all(self) -> None:
    await asyncio.gather(*(c.start() for c in self._unique_connections()))

  async def ensure_all_alive(self) -> None:
    await asyncio.gather(
        *(c.ensure_alive() for c in self._unique_connections())
    )

  async def stop_all(self) -> None:
    await asyncio.gather(*(c.stop() for c in self._unique_connections()))

  async def send_to(self, seat: int, message: dict[str, Any]) -> None:
    msg = dict(message)
    msg.setdefault("target_seat", seat)
    await self.clients[seat].send(msg)

  async def broadcast(
      self, message: dict[str, Any], exclude_seat: int | None = None
  ) -> None:
    tasks = [
        c.send(message) for c in self._unique_connections(exclude_seat)
    ]
    if tasks:
      await asyncio.gather(*tasks)

  async def request_one(
      self, seat: int, message: dict[str, Any], timeout_sec: float | None = None
  ) -> ClientResponse:
    seq = self.next_seq()
    t = self.timeout_sec if timeout_sec is None else timeout_sec
    return await self.clients[seat].request(
        message, seq=seq, timeout_sec=t, target_seat=seat
    )

  async def request_others_parallel(
      self,
      actor_seat: int,
      message: dict[str, Any],
      timeout_sec: float | None = None,
  ) -> dict[int, ClientResponse]:
    """Send `message` to all 3 seats other than `actor_seat` in parallel and wait up to `timeout_sec`."""
    t = self.timeout_sec if timeout_sec is None else timeout_sec
    other_seats = [s for s in range(4) if s != actor_seat]
    coros = [
        self.clients[s].request(
            message, seq=self.next_seq(), timeout_sec=t, target_seat=s
        )
        for s in other_seats
    ]
    responses = await asyncio.gather(*coros)
    return {resp.seat: resp for resp in responses}
