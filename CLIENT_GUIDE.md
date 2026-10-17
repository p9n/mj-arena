# Japanese Mahjong Client Implementation Guide

This document specifies everything required to implement a compatible client for the 4-player Japanese Mahjong (Tenhou rules) environment. An AI agent or developer can implement a fully compliant client solely by following this specification.

---

## 1. Execution & IPC Protocol

### 1.1 Transport Layer (`stdin` / `stdout` JSON Lines)
- Your client is launched as a standalone child process.
- All communication uses **UTF-8 JSON Lines (JSONL)**:
  - **Input (`stdin`)**: Read one newline-terminated JSON object (`\n`) at a time.
  - **Output (`stdout`)**: Write a single-line JSON object followed by `\n` and **flush `stdout` immediately** (`sys.stdout.flush()`, `fflush(stdout)`, etc.).
  - **Logging (`stderr` only)**: **Never** write debug text or non-JSON output to `stdout`. Any malformed JSON or unexpected output on `stdout` while a request is pending is treated as an illegal response and immediately penalized as **Chombo (チョンボ)**.

### 1.2 Resource Limits & Timeouts
- **CPU & Memory**: Each client process is restricted to a fixed number of CPU cores (default: `1` core) and a hard virtual address space limit (`RLIMIT_AS`, default: `4096 MB` / 4 GB). Crashing, closing `stdout` (EOF), or exceeding memory limits during a round results in an immediate **Chombo** when the server next interacts with that seat.
- **Thinking Time Limit (Default `5.0` seconds, sent in `match_start.timeout_sec`)**:
  - Every action request has a strict timeout (configured at `5.0` seconds by default).
  - **If you time out on your own turn (`turn_tsumo` / `turn_after_call`)**: You are **not** penalized with Chombo; instead, the environment automatically discards the just-drawn tile (**ツモ切り**, tsumogiri) on `turn_tsumo`, or the rightmost legal non-kuikae tile in your hand on `turn_after_call`.
  - **If you time out on a reaction query (`discard_notify` / `kakan_notify`)**: It is automatically treated as `{"action": "pass"}`.
- **Sequence Number (`seq`) Synchronization**:
  - Every message that requires a reply contains an integer `"seq"` field.
  - Your reply **must** echo the exact same `"seq"` as a **JSON integer** (e.g. `{"seq": 15, "action": ...}`). Missing `"seq"` or non-integer `"seq"` (such as `"15"` or `true`) is treated as an illegal response and ignored (sending an `error` message).

### 1.3 Message Ordering, Timeouts, and Process Restart Semantics
- **Strict Sequential Request-Response**: You will never receive a new message while your reply to an Action Request is pending, *unless you timed out* (or in 2v2 mode when both of your controlled seats are queried in parallel on an opponent's discard/kakan).
- **Late Replies After Timeout**:
  - After a timeout on `turn_tsumo` or `turn_after_call`, the server has already acted for you and sends you `discard_ack` indicating which tile was auto-discarded.
  - Any late reply carrying an older `"seq" < current_pending_seq` is safely dropped by the server.
  - **Furiten Side Effects of Timeouts**:
    - Timing out on `discard_notify` / `kakan_notify` when the tile is in your Tenpai wait set causes **Temporary Furiten (`同巡フリテン`)** (and **Permanent Post-Riichi Furiten** if you are in Riichi).
    - Timing out on `turn_tsumo` when the drawn tile was a winning tile causes it to be auto-discarded into your river, putting you in **Discard Furiten (`捨牌フリテン`)** (and **Permanent Post-Riichi Furiten** if in Riichi).
- **Process Crash & Restart Behavior**:
  - If your process crashes or closes `stdout` (EOF), it is automatically respawned at the start of the next round.
  - Upon respawn, the server immediately re-sends your `match_start` message(s) followed by the new round's `round_start` message(s).
  - Note: The server drains your `stderr` pipe asynchronously, so debug logging to `stderr` will not block your process.

### 1.4 2v2 (Tag Match) Mode (`--tag` / `--2v2` or `rules.game_mode = "2v2"`)
In **2v2 Tag Match** mode, only **2 client processes** are spawned (`Client A` and `Client B`), and each client controls **2 adjacent seats**:
- **Team 0**: Seat `0` (**Kamicha / 上家**, immortal) and Seat `1` (**Shimocha / 下家**, ranked).
- **Team 1**: Seat `2` (**Kamicha / 上家**, immortal) and Seat `3` (**Shimocha / 下家**, ranked).
- **Double Resource Limits**: Because a single client process operates 2 players simultaneously, the default per-process resource limits are doubled to **`2` CPU cores** (`--client-cpus 2`) and **`8192 MB` (8 GB) memory** (`--client-memory-mb 8192`).
- **Single Pipe with `"target_seat"` Multiplexing**:
  - Each client process uses a single `stdin`/`stdout` JSON Lines pipe.
  - Every per-seat message (`match_start`, `round_start`, `player_tsumo`, `discard_ack`, `error`, `turn_tsumo`, `turn_after_call`, `discard_notify`, `kakan_notify`) includes `"target_seat": <0..3>` identifying which of your two controlled seats is receiving the message.
  - Notice that when an opponent discards a tile (`discard_notify`) or declares an added kan (`kakan_notify`), your client process will receive **two** action requests back-to-back (one with `"target_seat": seat_a, "seq": seq1` and one with `"target_seat": seat_b, "seq": seq2`). Your client must reply to **both** requests by echoing their respective `"seq"` integers (`{"seq": seq1, ...}` and `{"seq": seq2, ...}` in either order).
  - Table-wide broadcast notifications (`dora_notify`, `riichi_accepted`, `call_notify`, `round_end`, `match_end`) do not have `"target_seat"` and apply to the whole table (you only receive one copy per broadcast).
  - Because both of your team's seats run inside the same process, they can freely share hand visibility and coordinate tile feeding (e.g., Kamicha feeding Chi/Pon/Ron tiles to Shimocha).
- **2v2 Game Rules**:
  1. **Immortal Kamicha (上家不死)**: Seats `0` and `2` (the Kamicha of each team) **cannot die (no Tobi)** — even if Seat `0` or Seat `2` drops below `0` points (`< 0`), the match continues! Only if a **Shimocha** player (Seat `1` or Seat `3`) finishes a round with `< 0` points does `飛び終了` (Tobi) occur.
  2. **Riichi Point Requirement for Kamicha (`>= 1000` Required)**: Even though Kamicha seats (`0` and `2`) can play on with `< 1000` or negative points, **they CANNOT declare Riichi when their score is `< 1000` (including negative scores)**. All 4 seats must have `points[seat] >= 1000` to declare `"riichi"`; attempting to declare Riichi with `< 1000` points is rejected with an `error` message and ignored (falling back to auto-tsumogiri).
  3. **4-Player Uma/Oka Settlement + Shimocha-Only Final Ranking**:
     - At the end of the match, standard 4-player Uma (`+20 / +10 / -10 / -20`) and Oka (`+20` to 1st place) are calculated across all 4 seats (`0, 1, 2, 3`) as usual.
     - **Final Ranking (`final_results`)**: Only the two **Shimocha** seats (Seat `1` and Seat `3`) are compared and ranked against each other (`rank 1` and `rank 2`, or tied `1, 1` in `"plus_minus_zero"` mode). Each entry in `final_results` also records `"partner_seat"`, `"partner_raw_score"`, and `"partner_final_pt"`.
  4. **Compatible with `"plus_minus_zero"` (`"正負0"`)**: When both `game_mode = "2v2"` and `ranking_mode = "plus_minus_zero"` are enabled, the 4-player post-Uma/Oka points are rounded via Gosha-Rokuiri (`五捨六入`), and the two Shimocha players (`Seat 1` and `Seat 3`) are ranked by `abs(final_pt)` ascending (closest to `0` wins).
- **Reference C++20 Tag Client**: See [`client/tag_client.cpp`](file:///usr/local/google/home/phoenixshen/temp/mj/client/tag_client.cpp) (compiled to `client/tag_client` via `make`).

---

## 2. Tile Notation (MPSZ Tokens)

Every individual tile in protocol messages is represented as a 2-character string `<digit><suit>`:

| Suit | Tokens | Description |
| :--- | :--- | :--- |
| **Manzu (萬子)** | `"1m"` .. `"9m"`, `"0m"` | Characters 1–9. `"0m"` is the **Red Five (赤五萬)** (1 copy in the 136-tile deck; the other 3 copies are `"5m"`). |
| **Pinzu (筒子)** | `"1p"` .. `"9p"`, `"0p"` | Circles 1–9. `"0p"` is the **Red Five (赤五筒)** (1 copy; the other 3 copies are `"5p"`). |
| **Souzu (索子)** | `"1s"` .. `"9s"`, `"0s"` | Bamboo 1–9. `"0s"` is the **Red Five (赤五索)** (1 copy; the other 3 copies are `"5s"`). |
| **Winds (風牌)** | `"1z"`, `"2z"`, `"3z"`, `"4z"` | `"1z"` = 東 (East), `"2z"` = 南 (South), `"3z"` = 西 (West), `"4z"` = 北 (North). |
| **Dragons (三元牌)** | `"5z"`, `"6z"`, `"7z"` | `"5z"` = 白 (White), `"6z"` = 發 (Green), `"7z"` = 中 (Red). |

### Important Rules for Red Fives (`"0m"`, `"0p"`, `"0s"`)
1. `"0m"` and `"5m"` represent the same tile kind (Five Manzu) for forming sequences, triplets, pairs, and waits, **but they are distinct physical tokens in your hand**.
2. When discarding or specifying hand tiles for `chi`, `pon`, or `kakan`, you **must** specify the exact token (`"0m"` vs `"5m"`) that is actually present in your closed hand. Attempting to discard `"5m"` when you only hold `"0m"` is illegal (Chombo).
3. For `ankan`, either `"0m"` or `"5m"` may be passed as `"tile"` since all 4 copies of Five Manzu are used.

### Dora Indicator Mapping
A Dora indicator tile indicates the **next** tile in sequence within its category as the Dora:
- **Number suits (`m`, `p`, `s`)**: `1 -> 2 -> 3 -> 4 -> 5(and 0) -> 6 -> 7 -> 8 -> 9 -> 1`. (e.g., indicator `"9m"` makes `"1m"` dora; indicator `"4p"` makes both `"5p"` and `"0p"` dora; indicator `"0p"` makes `"6p"` dora).
- **Winds (`1z`–`4z`)**: `"1z" (東) -> "2z" (南) -> "3z" (西) -> "4z" (北) -> "1z" (東)`.
- **Dragons (`5z`–`7z`)**: `"5z" (白) -> "6z" (發) -> "7z" (中) -> "5z" (白)`.

---

## 3. Turn Order, Seating & Client State Tracking

- There are 4 seats indexed `0`, `1`, `2`, `3`.
- Turn order proceeds counterclockwise: `0 -> 1 -> 2 -> 3 -> 0`.
- Relative to your seat `my_seat`:
  - **Shimocha (下家 / Right player / Next turn)**: `(my_seat + 1) % 4`
  - **Toimen (對家 / Across player)**: `(my_seat + 2) % 4`
  - **Kamicha (上家 / Left player / Previous turn)**: `(my_seat + 3) % 4` (i.e. `(discarder_seat + 1) % 4 == my_seat`). **You may only `chi` from your Kamicha.**

### 3.1 State Your Client Must Maintain
Because `turn_tsumo` and `turn_after_call` only include your closed `"hand"`, your client must track the following round state across messages (reset on every `round_start`):
1. **Your Own Melds (`melds`)**: Reconstruct from `call_notify` where `"seat" == my_seat` (note that `call_notify` is broadcast to **all 4 players**, including the caller). For `"kakan"`, upgrade your existing `"pon"` meld of the same tile kind.
2. **All Four Rivers (`rivers[0..3]`)**:
   - Append to `rivers[my_seat]` when you receive `discard_ack` (this covers all your discards, including timeouts).
   - Append to `rivers[seat]` when you receive `discard_notify`.
   - Note: For Discard Furiten (`捨牌フリテン`), your own river history **includes** tiles that were later called (`chi`/`pon`/`daiminkan`) by other players. For visible-tile counting or Nagashi Mangan tracking, note when `call_notify` (`chi`/`pon`/`daiminkan`) takes the last tile from `from_seat`.
3. **Riichi Status (`is_riichi`, `is_double_riichi`, `is_ippatsu`)**:
   - Track from `riichi_accepted` (do **not** mark a player as in Riichi merely upon `discard_notify` with `"is_riichi": true`, because that discard has not yet passed Ron checks).
   - Event sequence for a Riichi declaration is: `discard_ack` (to declarer) / `discard_notify` (to other 3 players) $\rightarrow$ if no player Rons, `riichi_accepted` is broadcast to all 4 players.
4. **Temporary & Post-Riichi Furiten (`temp_furiten`, `riichi_furiten`)**:
   - The server does **not** send a furiten flag in `discard_notify` or `kakan_notify`.
   - Whenever you receive `discard_notify` or `kakan_notify` and the tile kind is in your current 13-tile Tenpai wait set (`待ち`) and you do not win on it (including when you have no yaku or time out), set `temp_furiten = True` (and if you are in Riichi, set `riichi_furiten = True`).
   - Clear `temp_furiten = False` whenever you receive `discard_ack` (your own discard, including after `chi`/`pon`).
   - Also, while in Riichi, if you draw a winning tile on `turn_tsumo` and do not declare `"tsumo"`, set `riichi_furiten = True`.
5. **Total Kans in the Round (`total_kans`)**: Increment whenever `call_notify` has `meld_type` in `("daiminkan", "ankan", "kakan")`. Once `total_kans >= 4`, no further kans may be declared.
6. **Helper Functions & Reference Implementation**: See `client/simple_client.cpp` for a complete C++20 reference implementation of shanten/ukeire evaluation, kuikae checks, furiten tracking, and agari/yaku evaluation. If implementing your client in Python within this repository, you may also directly import `from server.hand import Meld, get_tenpai_waits, is_tenpai, can_ankan_in_riichi, get_kuikae_forbidden_kinds` and `from server.agari import WinContext, evaluate_agari`.

---

## 4. Incoming Messages (`stdin`)

Incoming messages are divided into **Notifications** (do **not** send a response) and **Action Requests** (contain `"seq"` and **require** a response within `5.0` seconds).

### 4.1 Notifications (No Response Allowed)

#### 1. `match_start`
Sent once at the beginning of the Hanchan match (and re-sent if your process crashes and is restarted at the start of the next round).
```json
{
  "type": "match_start",
  "seat": 0,
  "dealer_first_seat": 2,
  "dice_seating": [3, 4],
  "timeout_sec": 5.0,
  "rules": {
    "kuitan": true,
    "aka_dora": true,
    "start_points": 25000,
    "return_points": 30000,
    "uma": [20, 10, -10, -20],
    "ranking_mode": "normal",
    "tobi_enabled": true,
    "west_sudden_death": true,
    "nagashi_mangan": true,
    "double_ron": true,
    "triple_ron_draw": true,
    "pao_daisangen": true,
    "pao_daisuushii": true,
    "pao_suukantsu": false,
    "noten_riichi_chombo_on_draw": true
  }
}
```

#### 2. `round_start`
Sent at the start of each round (局) with your initial 13-tile hand.
```json
{
  "type": "round_start",
  "round_wind": "E",
  "round_number": 1,
  "honba": 0,
  "kyoutaku": 0,
  "dealer_seat": 2,
  "seat": 0,
  "seat_wind": "W",
  "dice_wall": [5, 6],
  "wall_break_seat": 0,
  "wall_break_stack": 11,
  "dora_indicators": ["3p"],
  "points": [25000, 25000, 25000, 25000],
  "hand": ["1m", "2m", "3m", "0p", "6p", "7p", "2s", "3s", "4s", "1z", "1z", "7z", "7z"]
}
```
- `round_wind`: `"E"` (East / 東場), `"S"` (South / 南場), or `"W"` (West / 西入サドンデス).
- `seat_wind`: Your wind for this round (`"E"`, `"S"`, `"W"`, or `"N"`).
- `honba`: Number of repeat counters (本場). Each honba adds `+300` points (`+100` from each non-winner on Tsumo) to a hand win.
- `kyoutaku`: Number of `1,000`-point riichi sticks currently on the table.
- `points`: Array of 4 integers `[seat0_pts, seat1_pts, seat2_pts, seat3_pts]`.

#### 3. `player_tsumo`
Broadcast to the other 3 players when `seat` draws a tile (does not reveal the tile).
```json
{
  "type": "player_tsumo",
  "seat": 2,
  "is_rinshan": false,
  "remaining_tiles": 69
}
```

#### 4. `discard_ack`
Sent only to the player who just discarded a tile (including auto-discards on timeout), confirming the discard.
```json
{
  "type": "discard_ack",
  "seat": 0,
  "tile": "7z",
  "is_tsumogiri": false,
  "is_riichi": false
}
```

#### 5. `riichi_accepted`
Broadcast when a player's Riichi declaration discard passes without being Ron'd. At this point `1,000` points are deducted from `seat` and `kyoutaku` increases by `1`.
```json
{
  "type": "riichi_accepted",
  "seat": 2,
  "is_double_riichi": false,
  "kyoutaku": 1,
  "points": [25000, 25000, 24000, 25000]
}
```

#### 6. `call_notify`
Broadcast when any player completes a meld (`chi`, `pon`, `daiminkan`, `ankan`, or `kakan` after passing Chankan check).
```json
{
  "type": "call_notify",
  "seat": 0,
  "meld_type": "pon",
  "tiles": ["7z", "7z", "7z"],
  "called_tile": "7z",
  "from_seat": 2,
  "mpsz": "777z"
}
```
- `meld_type`: One of `"chi"`, `"pon"`, `"daiminkan"`, `"ankan"`, `"kakan"`.
- `called_tile`: The called discard for `"chi"`/`"pon"`/`"daiminkan"`, the added 4th tile for `"kakan"`, or `null` for `"ankan"`.
- `from_seat`: Integer seat from which the tile was called (for `"kakan"`, the original `"pon"`'s source seat; `null` for `"ankan"`).

#### 7. `dora_notify`
Broadcast whenever a new Kan-dora indicator is revealed (immediately upon `ankan`; right before discard or a consecutive kan's Rinshan draw for `daiminkan` and `kakan`).
```json
{
  "type": "dora_notify",
  "dora_indicator": "5s",
  "dora_indicators": ["3p", "5s"]
}
```

#### 8. `round_end`
Broadcast at the conclusion of a round. Below are the full schemas for each outcome subtype:

- **Tsumo (`"result": "tsumo"` / top-level fields)**:
  ```json
  {
    "type": "round_end",
    "winner": 0,
    "winning_tile": "4p",
    "hand_mpsz": "123m067p234s11z",
    "melds_mpsz": ["777z"],
    "han": 2,
    "fu": 30,
    "yaku": [{"name": "役牌:中", "han": 1}, {"name": "赤ドラ", "han": 1}],
    "yakuman": [],
    "pao_seats": {},
    "dora_indicators": ["3p"],
    "ura_dora_indicators": [],
    "score_deltas": [2000, -500, -1000, -500],
    "scores_after": [27000, 24500, 24000, 24500]
  }
  ```
- **Ron (`"wins"` list contains 1 or 2 winner objects)**:
  - Note: On Double Ron, `"wins"` is ordered counterclockwise starting from `"loser"`; only `wins[0]` receives the round's `honba` bonus and `kyoutaku` sticks. If a player's Riichi declaration tile is Ron'd, the `1,000`-point Riichi stick is **not** deducted.
  ```json
  {
    "type": "round_end",
    "loser": 2,
    "winning_tile": "7z",
    "wins": [
      {
        "winner": 0,
        "loser": 2,
        "winning_tile": "7z",
        "hand_mpsz": "123m406p234s1177z",
        "melds_mpsz": [],
        "han": 2,
        "fu": 40,
        "yaku": [{"name": "役牌:中", "han": 1}, {"name": "赤ドラ", "han": 1}],
        "yakuman": [],
        "pao_seats": {},
        "ura_dora_indicators": []
      }
    ],
    "dora_indicators": ["3p"],
    "score_deltas": [2600, 0, -2600, 0],
    "scores_after": [27600, 25000, 22400, 25000]
  }
  ```
- **Exhaustive Draw (`荒牌平局`)**:
  - Note: Keys in `"tenpai_hands"` are JSON string seat numbers (`"0"`, `"1"`, etc.).
  ```json
  {
    "type": "round_end",
    "tenpai_seats": [0, 2],
    "tenpai_hands": {"0": "123m456p789s1177z", "2": "234m567p23s55566z"},
    "score_deltas": [1500, -1500, 1500, -1500],
    "scores_after": [26500, 23500, 26500, 23500]
  }
  ```
- **Nagashi Mangan (`流し満貫`)**:
  ```json
  {
    "type": "round_end",
    "nagashi_seats": [1],
    "tenpai_seats": [0],
    "score_deltas": [-2000, 8000, -4000, -2000],
    "scores_after": [23000, 33000, 21000, 23000]
  }
  ```
- **Abortive Draw (`途中流局`)**:
  - `"reason"` is one of `"ryuukyoku_kyuushu_kyuuhai"`, `"ryuukyoku_suufon_renda"`, `"ryuukyoku_suuchariichi"`, `"ryuukyoku_suukaikan"`, or `"ryuukyoku_sanchahou"` (which also includes `"ron_seats": [1, 2, 3]` and `"tile"`).
  ```json
  {
    "type": "round_end",
    "reason": "ryuukyoku_suufon_renda",
    "actor_seat": 3,
    "score_deltas": [0, 0, 0, 0],
    "scores_after": [25000, 25000, 25000, 25000]
  }
  ```
- **Chombo (`錯和` — Noten Riichi at Exhaustive Draw)**:
  ```json
  {
    "type": "round_end",
    "offender": 1,
    "reason": "noten riichi (ノーテンリーチ) revealed at exhaustive draw",
    "penalty_deltas": [4000, -8000, 2000, 2000],
    "score_deltas": [4000, -8000, 2000, 2000],
    "scores_after": [29000, 17000, 27000, 27000]
  }
  ```

#### 9. `error`
Sent privately to a player when their submitted action is illegal or malformed. Do **not** send a response to `error`.
```json
{
  "type": "error",
  "seat": 0,
  "message": "invalid discard tile token: '9z'"
}
```
- If the illegal action occurred on `turn_tsumo` or `turn_after_call`, the server ignores the illegal action and immediately performs auto-tsumogiri (or discards the rightmost legal non-kuikae tile after a call), followed by a `discard_ack`.
- If the illegal action occurred on `discard_notify` or `kakan_notify`, the server ignores the illegal action and treats it as `"pass"`.

#### 10. `match_end`
Broadcast when the Hanchan finishes. Upon receiving `match_end`, the client should exit cleanly (`exit(0)`).
- `"end_reason"`: One of `"all_last"` (normal South 4 / West completion), `"tobi"` (a player dropped below `0` points), or `"chombo_cap"` (`16` consecutive Chombos reached).
- In `"normal"` ranking mode, `"final_pt"` is a float rounded to 1 decimal place.
- In `"plus_minus_zero"` (`"正負0"`) ranking mode, `"final_pt"` is the Gosha-Rokuiri integer, `"raw_pt"` holds the unrounded 1-decimal pt, and `"rank"` allows ties.
```json
{
  "type": "match_end",
  "end_reason": "all_last",
  "final_results": [
    {"rank": 1, "seat": 1, "raw_score": 35400, "final_pt": 45.4},
    {"rank": 2, "seat": 0, "raw_score": 28200, "final_pt": 8.2},
    {"rank": 3, "seat": 2, "raw_score": 21000, "final_pt": -19.0},
    {"rank": 4, "seat": 3, "raw_score": 15400, "final_pt": -34.6}
  ],
  "paifu_path": "paifu.json"
}
```

---

### 4.2 Action Requests (Response Required Within `5.0` Seconds)

#### 1. `turn_tsumo` (Your Draw Turn)
Sent when you draw a tile from the live wall or dead wall (Rinshan).
```json
{
  "type": "turn_tsumo",
  "seq": 15,
  "tile": "4p",
  "is_rinshan": false,
  "remaining_tiles": 69,
  "dora_indicators": ["3p"],
  "hand": ["1m", "2m", "3m", "0p", "6p", "7p", "2s", "3s", "4s", "1z", "1z", "7z", "7z"]
}
```
- **Note**:
  - `"hand"` is your 13-tile closed hand *prior* to drawing `"tile"`. Your full closed hand for this turn is `hand + [tile]` (14 tiles, or `14 - 3 * len(melds)` tiles).
  - After declaring `"ankan"`, `"kakan"`, or `"daiminkan"`, you receive a new `turn_tsumo` with `"is_rinshan": true`. This is a full action request (`"tsumo"`, `"riichi"`, another kan, or `"discard"` are all allowed if legal). Note that if you declare a kan when `remaining_tiles == 1`, the Rinshan draw reduces `remaining_tiles` to `0` (not `海底摸月` because `is_rinshan` is `true`, and `riichi` is forbidden since `remaining_tiles < 4`; your subsequent discard will be `河底撈魚` if Ron'd).

**Valid Responses to `turn_tsumo`**:
| Action | JSON Format | When Legal |
| :--- | :--- | :--- |
| **Discard** | `{"seq": 15, "action": "discard", "tile": "7z"}` | `"tile"` must be in `hand + [drawn_tile]`. If you are already in Riichi, `"tile"` **must** equal the just-drawn `drawn_tile` (tsumogiri). |
| **Riichi** | `{"seq": 15, "action": "riichi", "tile": "7z"}` | Must not already be in Riichi; must be **Menzen** (no open `chi`/`pon`/`daiminkan` melds; `ankan` is allowed); your current score must be `>= 1000` (applies to all seats, including immortal Kamicha in 2v2 mode — negative or `< 1000` points cannot declare Riichi); `remaining_tiles >= 4`; `"tile"` must be in `hand + [drawn_tile]`. *(See Section 5.3 on Noten Riichi.)* |
| **Tsumo Win** | `{"seq": 15, "action": "tsumo"}` | `hand + [drawn_tile]` + your melds must form a complete winning hand with `>= 1` han of non-bonus yaku (or Yakuman). |
| **Ankan (暗槓)** | `{"seq": 15, "action": "ankan", "tile": "1z"}` | `remaining_tiles > 0`; fewer than 4 kans total have been declared in the round; `hand + [drawn_tile]` contains all 4 copies of `"tile"`. If in Riichi, see strict Riichi Ankan rules in Section 5.4. |
| **Kakan (加槓)** | `{"seq": 15, "action": "kakan", "tile": "0p"}` | Not in Riichi; `remaining_tiles > 0`; fewer than 4 kans total in the round; `"tile"` is in `hand + [drawn_tile]`; you have an existing `"pon"` meld of the same tile kind. |
| **Kyuushu (九種九牌)** | `{"seq": 15, "action": "kyuushu"}` | Only on your **very first turn of the round**, provided no calls/kans have been made by any player (`uncalled_first_round`), and `hand + [drawn_tile]` contains **at least 9 distinct kinds** of terminal/honor tiles (`1m, 9m, 1p, 9p, 1s, 9s, 1z..7z`). |

---

#### 2. `turn_after_call` (Your Discard Turn After `chi` or `pon`)
Sent immediately after your `chi` or `pon` is executed.
```json
{
  "type": "turn_after_call",
  "seq": 18,
  "forbidden_discards": ["4m", "7m"],
  "hand": ["2m", "3m", "4m", "0p", "6p", "7p", "2s", "3s", "4s", "7z", "7z"]
}
```
- `"hand"` is your remaining closed hand after removing the 2 tiles used in the meld.
- `"forbidden_discards"` lists all tile tokens forbidden on this turn by the **Kuikae (`喰い替えなし`)** rule.

**Valid Response to `turn_after_call`**:
- **Discard**: `{"seq": 18, "action": "discard", "tile": "2m"}`
  - `"tile"` must exist in `"hand"` and must **not** be in `"forbidden_discards"`. (Tsumo, Riichi, Ankan, Kakan, and Kyuushu are **illegal** on `turn_after_call`.)

---

#### 3. `discard_notify` (Reaction to Another Player's Discard)
Sent in parallel to all 3 players other than the discarder `seat`.
```json
{
  "type": "discard_notify",
  "seq": 16,
  "seat": 2,
  "tile": "0p",
  "is_tsumogiri": false,
  "is_riichi": false,
  "remaining_tiles": 50
}
```

**Valid Responses to `discard_notify`**:
| Action | JSON Format | When Legal |
| :--- | :--- | :--- |
| **Pass** | `{"seq": 16, "action": "pass"}` | Always legal. |
| **Ron (榮和)** | `{"seq": 16, "action": "ron"}` | Your 13-tile hand + `"tile"` forms a valid winning hand with `>= 1` non-bonus yaku (or Yakuman), and you are **not in Furiten** (Section 5.2). |
| **Pon (碰)** | `{"seq": 16, "action": "pon", "tiles": ["5p", "5p"]}` | Not in Riichi; `remaining_tiles > 0` (`海底牌は鳴けない`); `"tiles"` is a 2-element list of exact tokens in your closed hand matching the kind of `"tile"` (if `"tiles"` is omitted, the server auto-selects the first 2 matching tiles in sorted order, which includes `"0m"/"0p"/"0s"` before `"5m"/"5p"/"5s"`); after removing `"tiles"`, your closed hand still contains at least 1 tile not of that kind (cannot leave only kuikae-forbidden tiles). |
| **Daiminkan (大明槓)** | `{"seq": 16, "action": "daiminkan"}` | Not in Riichi; `remaining_tiles > 0`; fewer than 4 kans total in the round; your closed hand contains all 3 remaining copies of `"tile"`'s kind. |
| **Chi (吃)** | `{"seq": 16, "action": "chi", "tiles": ["4p", "6p"]}` | Only from **Kamicha** (`(seat + 1) % 4 == my_seat`); not in Riichi; `remaining_tiles > 0`; `"tile"` is a number tile (`m`, `p`, `s`); `"tiles"` is a 2-element list of exact tokens in your closed hand that form a 3-tile consecutive sequence with `"tile"`; after removing `"tiles"`, your closed hand still contains at least 1 tile not forbidden by Kuikae (Section 5.1). |

*Call Priority Resolution*: When multiple players respond to `discard_notify`, the server resolves actions in priority order: **`ron` > abortive draw checks (`四家立直`, `四風連打`, `四槓散了`) > `pon` / `daiminkan` > `chi` > `pass`**.

---

#### 4. `kakan_notify` (Reaction to Another Player's Added Kan / Chankan Query)
Sent in parallel to all 3 players other than `seat` when `seat` declares `kakan`.
```json
{
  "type": "kakan_notify",
  "seq": 22,
  "seat": 1,
  "tile": "5s"
}
```

**Valid Responses to `kakan_notify`**:
| Action | JSON Format | When Legal |
| :--- | :--- | :--- |
| **Pass** | `{"seq": 22, "action": "pass"}` | Always legal. |
| **Ron (搶槓 / Chankan)** | `{"seq": 22, "action": "ron"}` | Your hand + `"tile"` forms a valid winning hand (note: `槍槓` itself provides 1 han!), and you are **not in Furiten**. |

*(Responding with `pon`, `chi`, or `daiminkan` to `kakan_notify` is illegal and triggers Chombo.)*

---

## 5. Illegal Operations & Chombo (チョンボ) Conditions

To prevent intentional point-dumping in `"plus_minus_zero"` (`"正負0"`) mode:
1. **Illegal Turn / Reaction Operations (Ignored with `error` Notification, No Penalty)**:
   - Any illegal action submitted during a round (e.g., invalid discard token, discarding a tile not in hand, Kuikae violation, false `tsumo`/`ron`, Furiten `ron`, illegal `chi`/`pon`/`daiminkan`/`ankan`/`kakan`, or malformed JSON) is **ignored by the server**.
   - The server sends a private `{"type": "error", "seat": N, "message": "..."}` notification to the offending client:
     - On `turn_tsumo` / `turn_after_call`: the server immediately falls back to auto-tsumogiri (or rightmost legal non-kuikae tile after a call) and sends `discard_ack`.
     - On `discard_notify` / `kakan_notify`: the server treats the response as `"pass"`.
2. **Noten Riichi (`ノーテンリーチ`) — The Only Chombo Penalty**:
   - Revealing a Noten hand at **Exhaustive Draw (`荒牌平局`)** after declaring Riichi immediately ends the round as a **Chombo (錯和)**:
     - **Dealer (親 / East) Chombo**: Pays `4,000` points to each of the 3 non-dealers (`-12,000` total).
     - **Non-Dealer (子 / South, West, North) Chombo**: Pays `4,000` points to the dealer and `2,000` points to each of the other 2 non-dealers (`-8,000` total).
     - Riichi sticks declared during the round are refunded; `honba` does not increase, and the same round is replayed (up to a safety cap of `16` consecutive Chombos).

Your client should enforce the following legality rules before sending any non-`pass` / non-tsumogiri action:

### 5.1 Kuikae Prohibition (`喰い替えなし`)
Immediately after calling `chi` or `pon`, you are prohibited from discarding:
1. **Same-Kind Kuikae (現物喰い替え)**: Any tile of the same kind as the called tile (e.g., calling `"0m"` forbids discarding `"5m"` and `"0m"`).
2. **Suji Kuikae (筋喰い替え)**: If you called `chi` using two adjacent tiles in your hand (a two-sided ryanmen shape), you also cannot discard the tile on the opposite end of those two hand tiles.
   - *Example*: Holding `"5m", "6m"` and calling `chi` on `"4m"` forbids discarding both `"4m"` (same-kind) and `"7m"` (suji).
   - *Example*: Holding `"4m", "6m"` (kanchan) and calling `chi` on `"0m"` only forbids `"5m"`/`"0m"`.
- **Pre-call Check**: If calling `chi` or `pon` would leave your closed hand with **zero** legal tiles to discard (all remaining closed tiles are forbidden by Kuikae), then declaring that `chi` or `pon` is itself illegal (Chombo).

### 5.2 Furiten (`振聴`) — Blocks `ron` (Does Not Block `tsumo`)
You are in **Furiten** (and therefore **cannot** declare `ron`) if **any** of the following three conditions holds:
1. **Discard Furiten (`捨牌フリテン`)**: Any tile kind in your current Tenpai wait set (`待ち`) has ever been discarded by you into your river (including tiles that were called by other players, **excluding** tiles added to a Kakan or Ankan).
   - *Note*: For 13-way Kokushi Musou (`国士無双13面待ち`), if **any** of the 13 terminal/honor kinds is in your river, you are in Discard Furiten.
2. **Same-Turn Temporary Furiten (`同巡フリテン`)**: Since your last discard, another player discarded (or declared `kakan` with) a tile kind that was in your Tenpai wait set (even if that tile would not have given you a yaku!), and you did not win on it. Temporary Furiten is cleared **only when you discard a tile** (`逆巡副露では解消しない`).
3. **Post-Riichi Permanent Furiten (`リーチ後フリテン`)**: While in Riichi, if any tile kind in your Tenpai wait set is discarded or added via `kakan` (or drawn by you without declaring `tsumo`) and you pass on it, you are permanently in Furiten for the remainder of the round.

### 5.3 Tenpai, 5th-Tile Wait, and Noten Riichi (`ノーテンリーチ`)
- **5th-Tile Wait Rule (`純手牌で４枚使用していなければ成立`)**:
  - A 13-tile hand is in Tenpai if there is at least one tile kind `w` (`0..33`) such that:
    1. You hold **fewer than 4 copies** of `w` in your **closed hand** (copies in open/closed melds do *not* disqualify `w`); and
    2. Adding `w` to your closed hand forms a valid winning shape (4 melds + 1 pair, 7 distinct pairs `七対子`, or 13 orphans `国士無双`).
  - *Trap*: If your *only* winning shape wait is a tile kind `w` of which all 4 copies are already in your closed hand (e.g., holding `"1111z"` in your closed hand and waiting solely on a tanki `"1z"`), your hand is **Noten**!
- **Noten Riichi (`ノーテンリーチ`, Option B)**:
  - At the moment you send `{"action": "riichi", "tile": ...}`, the server only checks public conditions (Menzen, score `>= 1000`, `remaining_tiles >= 4`).
  - If the round ends by an **Agari (`tsumo`/`ron`)** or an **Abortive Draw (`途中流局`)**, hands are not opened for tenpai inspection and Noten Riichi is not penalized.
  - However, if the round reaches **Exhaustive Draw (`荒牌平局`)**, all Riichi players' hands are checked in counterclockwise order starting from the dealer; the first Riichi player found to be Noten is immediately penalized with **Chombo**!

### 5.4 Ankan While in Riichi (`リーチ後の暗槓`)
When you are in Riichi and receive `turn_tsumo` with `"tile": T`:
- You may only declare `ankan` on `T` if **all** of the following hold:
  1. **No Okurikan (`送り槓不可`)**: The Ankan tile kind must be the exact tile kind of the just-drawn `T` (you held 3 copies in your 13-tile hand and just drew the 4th).
  2. **Not a Winning Tile**: `T` itself is not in your Tenpai wait set `waits_before`.
  3. **Unchanged Waits (`待ちが変わらない場合のみ`)**: Your 13-tile hand before drawing `T` was in Tenpai with non-empty wait set `waits_before`, and after forming the Ankan of `T`, the 10-tile hand + melds has the **exact same wait set** (`waits_after == waits_before`).
- *Note*: Nine Gates (`九蓮宝燈`) cannot be scored if the hand contains an Ankan (`九蓮宝燈は暗槓不可`).

---

## 6. Match Rules & Yaku Summary (`tenhou-rules.txt`)

### 6.1 Match Structure & Settlement
- **Format**: 4-player Hanchan (East 1–4, South 1–4, with West 1–4 Sudden Death if no player has `>= 30,000` points at the end of South 4).
- **Starting / Target Points**: Start with `25,000` points; `30,000` return (`+20.0` pt Oka to raw-score 1st place); Uma `+20 / +10 / -10 / -20` assigned by raw-score rank (ties broken by East 1 wind order).
- **Final Ranking Modes (`rules.ranking_mode`)**:
  - `"normal"` (default): Standard ranking (`1` to `4`) ordered by final raw score descending (ties broken by East 1 wind order), with `final_pt` rounded to 1 decimal place.
  - `"plus_minus_zero"` (or `"正負0"`): All in-game rules and Uma/Oka calculations remain identical, but each player's post-Uma/Oka score is first rounded to an integer via **Gosha-Rokuiri (五捨六入)** (fractional `.1`–`.5` rounds toward `0`; `.6`–`.9` rounds away from `0`), and final standings are ranked by **`abs(final_pt)` ascending** (closest to `0` wins), **allowing tied ranks** (e.g., two players with `+3` and `-3` both have `abs == 3` and share the same `rank`).
- **Tobi (`飛び終了`)**: If any player's score becomes strictly negative (`< 0`), the match ends immediately (`0` points continues).
- **All-Last (`南4局` / `西1~4局`) Agari-yame & Tenpai-yame**: If the dealer in South 4 (or West 1–4) wins a hand or is Tenpai at exhaustive draw and is in **1st place with `>= 30,000` points** (excluding uncollected `kyoutaku`), the match ends automatically.
- **Abortive Draws (`途中流局` — all cause dealer renchan and `honba + 1`)**:
  - `九種九牌` (9 terminals/honors on 1st uncalled turn)
  - `四風連打` (All 4 players discard the exact same wind tile on the 1st uncalled turn)
  - `四家立直` (All 4 players declare Riichi and the 4th Riichi discard is not Ron'd)
  - `四槓散了` (4 kans declared across 2 or more players, and the 4th kan's subsequent discard is not Ron'd; if 1 player alone declares 4 kans, play continues and a 5th kan is forbidden)
  - `三家和了` (All 3 other players validly declare `ron` on the same discard or `kakan` tile)
- **Exhaustive Draw & Nagashi Mangan (`流し満貫`)**:
  - If `remaining_tiles == 0` and the last discard is not Ron'd:
    - After verifying no Riichi player is in Noten Riichi, if any player's river consists solely of terminal/honor tiles (`1, 9, z`) and **none of their discards were called by others**, they receive a Mangan Tsumo payment (`4000 all` for dealer, `2000/4000` for non-dealer) instead of normal Tenpai settlement.
    - Otherwise, Tenpai players split `3,000` points paid by Noten players (`1` Tenpai: `+3000 / -1000×3`; `2` Tenpai: `+1500×2 / -1500×2`; `3` Tenpai: `+1000×3 / -3000`).

### 6.2 Yaku & Yakuman Reference
**1-Han Minimum (`1飜縛り`)**: Every winning hand must have at least 1 han from standard Yaku (or a Yakuman). **Bonus han (`一発`, `ドラ`, `赤ドラ`, `裏ドラ`) do NOT count toward the 1-han minimum.**

| Category | Yaku Name | Han (Closed / Open) | Notes |
| :--- | :--- | :--- | :--- |
| **1 Han** | `門前清自摸和` (Menzen Tsumo) | 1 / — | Closed hand Tsumo win. |
| | `立直` (Riichi) | 1 / — | Declared Riichi. |
| | `槍槓` (Chankan) | 1 / 1 | Ron on another player's `kakan` (cannot rob `ankan`, even with Kokushi). Combines with `一発` on the 1st kan. |
| | `嶺上開花` (Rinshan Kaihou) | 1 / 1 | Tsumo on a Rinshan tile (`is_rinshan: true`). Adds +2 fu. Does not combine with `一発` or `海底摸月`. |
| | `海底摸月` (Haitei Raoyue) | 1 / 1 | Tsumo on the last tile (`remaining_tiles == 0` and `is_rinshan == false`). |
| | `河底撈魚` (Houtei Raoyui) | 1 / 1 | Ron on the last discard (`remaining_tiles == 0`). |
| | `役牌:場風牌` / `役牌:門風牌` | 1 / 1 | Triplet/Kan of round wind or seat wind (both stack for 2 han if double wind). |
| | `役牌:白` / `役牌:發` / `役牌:中` | 1 / 1 | Triplet/Kan of dragons (`5z`, `6z`, `7z`). |
| | `断幺九` (Tanyao) | 1 / 1 | Only simples (`2`–`8` of `m`, `p`, `s`). Open Tanyao (`喰い断`) is enabled by default. |
| | `平和` (Pinfu) | 1 / — | Closed hand, 4 sequences, non-yakuhai pair, two-sided (`ryanmen`) wait. Pinfu Tsumo is `20 fu`. |
| | `一盃口` (Iipeikou) | 1 / — | Closed hand, two identical sequences in the same suit. |
| **2 Han** | `両立直` (Double Riichi) | 2 / — | Riichi declared on the player's 1st uncalled turn. |
| | `七対子` (Chiitoitsu) | 2 / — | Closed hand, 7 distinct pairs (4 identical tiles do **not** count as 2 pairs). Fixed `25 fu`. |
| | `対々和` (Toitoi) | 2 / 2 | 4 triplets/kans. |
| | `三暗刻` (Sanankou) | 2 / 2 | 3 concealed triplets/kans (a triplet completed by Ron counts as open unless the winning tile also completes a sequence). |
| | `三槓子` (Sankantsu) | 2 / 2 | 3 kans. |
| | `三色同刻` (Sanshoku Doukou) | 2 / 2 | Triplets/kans of the same number across all 3 suits (`m`, `p`, `s`). |
| | `三色同順` (Sanshoku Doujun) | 2 / 1 | Sequences of the same numbers across all 3 suits (`m`, `p`, `s`). |
| | `一気通貫` (Ittsuu) | 2 / 1 | `123`, `456`, `789` sequences in the same suit. |
| | `混全帯幺九` (Chanta) | 2 / 1 | Every meld and pair contains a terminal or honor; has `>= 1` sequence and `>= 1` honor. |
| | `混老頭` (Honroutou) | 2 / 2 | All tiles are terminals and honors (`1, 9, z`), with both terminals and honors present. |
| | `小三元` (Shousangen) | 2 / 2 | 2 dragon triplets/kans + pair of the 3rd dragon. |
| **3 Han** | `純全帯幺九` (Junchan) | 3 / 2 | Every meld and pair contains a terminal (`1, 9`); has `>= 1` sequence. |
| | `混一色` (Honitsu) | 3 / 2 | Single number suit (`m`, `p`, or `s`) + honor tiles. |
| | `二盃口` (Ryanpeikou) | 3 / — | Closed hand, two pairs of identical sequences. |
| **6 Han** | `清一色` (Chinitsu) | 6 / 5 | Single number suit (`m`, `p`, or `s`) only, no honors. |
| **Yakuman** | `天和` / `地和` | 13 | Tsumo on 1st uncalled turn with 0 melds (Dealer / Non-dealer). |
| *(All single* | `国士無双` / `国士無双13面` | 13 | Closed hand, one of each of the 13 terminals/honors + 1 duplicate. |
| *yakuman;* | `四暗刻` / `四暗刻単騎` | 13 | 4 concealed triplets/kans (shabo requires Tsumo; tanki allows Ron). |
| *multiple* | `大三元` | 13 | Triplets/kans of all 3 dragons (`5z, 6z, 7z`). Has **Pao** on 3rd open dragon meld. |
| *yakuman* | `小四喜` / `大四喜` | 13 | 3 wind triplets + 4th wind pair / 4 wind triplets. `大四喜` has **Pao** on 4th open wind meld. |
| *stack)* | `字一色` | 13 | All honor tiles (`1z..7z`), valid with 4 melds + 1 pair **or** 7 distinct pairs (`七対子`). |
| | `緑一色` | 13 | Only `2s, 3s, 4s, 6s, 8s, 6z(發)` (`6z` not required). |
| | `清老頭` | 13 | Only terminal tiles (`1m, 9m, 1p, 9p, 1s, 9s`), 4 triplets + 1 pair. |
| | `四槓子` | 13 | 4 kans (no Pao). |
| | `九蓮宝燈` / `純正九蓮宝燈` | 13 | Closed hand (`0` melds, no `ankan`), single suit `1112345678999` + 1 more of that suit. |
| | `数え役満` (Kazoe Yakuman) | 13+ | `13` or more total han (including dora/bonus han, with `>= 1` regular yaku). |

---

## 7. Minimal Client Template (Python)

Below is a minimal, compliant event-loop skeleton demonstrating proper `stdin`/`stdout` JSONL handling and `seq` echoing:

```python
import json
import sys

def send_response(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        msg = json.loads(line)
        msg_type = msg.get("type")
        seq = msg.get("seq")

        if msg_type == "turn_tsumo":
            # Safe baseline: discard the just-drawn tile (tsumogiri)
            send_response({"seq": seq, "action": "discard", "tile": msg["tile"]})

        elif msg_type == "turn_after_call":
            # Choose a tile from msg["hand"] that is not in msg["forbidden_discards"]
            forbidden = set(msg.get("forbidden_discards", []))
            legal_tiles = [t for t in msg["hand"] if t not in forbidden]
            send_response({"seq": seq, "action": "discard", "tile": legal_tiles[-1]})

        elif msg_type in ("discard_notify", "kakan_notify"):
            # Safe baseline: pass on calls/ron unless verified legal
            send_response({"seq": seq, "action": "pass"})

        elif msg_type == "match_end":
            break

if __name__ == "__main__":
    main()
```
