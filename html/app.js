/**
 * Japanese Mahjong Replay Viewer (MPSZ Paifu)
 * Supports step forward, step back, and 1s/step autoplay.
 */

const SUIT_ORDER = { m: 0, p: 1, s: 2, z: 3 };
const WIND_KANJI = { E: "東", S: "南", W: "西", N: "北" };
const REL_WIND_KANJI = ["東", "南", "西", "北"];

let paifuData = null;
let currentRoundIdx = 0;
let currentStepIdx = 0;
let roundSnapshots = [];
let autoplayTimer = null;
let activeFlyingCleanup = null;

function cleanupActiveAnimation() {
  if (activeFlyingCleanup !== null) {
    activeFlyingCleanup();
    activeFlyingCleanup = null;
  }
}

function parseMpsz(mpsz) {
  if (!mpsz) return [];
  const tokens = [];
  let digits = [];
  for (const ch of mpsz.trim()) {
    if (ch >= "0" && ch <= "9") {
      digits.push(ch);
    } else if (SUIT_ORDER[ch] !== undefined) {
      for (const d of digits) {
        tokens.push(`${d}${ch}`);
      }
      digits = [];
    }
  }
  return tokens;
}

function tokenSortKey(tok) {
  const suit = tok[1];
  const d = tok[0];
  const rank = d === "0" ? 4.9 : parseInt(d, 10);
  return SUIT_ORDER[suit] * 100 + rank;
}

function sortTokens(tokens) {
  return [...tokens].sort((a, b) => tokenSortKey(a) - tokenSortKey(b));
}

function getSeatWindKanji(seat, dealerSeat) {
  const idx = (seat - dealerSeat + 4) % 4;
  return REL_WIND_KANJI[idx];
}

function extractExecutableName(cmdStr, fallbackSeat) {
  if (!cmdStr || typeof cmdStr !== "string") {
    return `Player ${fallbackSeat}`;
  }
  const tokens = cmdStr.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) {
    return `Player ${fallbackSeat}`;
  }
  const interpreters =
    /^(python\d*(\.\d+)*|pypy\d*|node|nodejs|deno|bun|bash|sh|zsh|ruby|perl|java|luajit|lua)$/i;
  const base0 = tokens[0].split(/[\\/]/).pop();
  if (interpreters.test(base0)) {
    for (let i = 1; i < tokens.length; i++) {
      const tok = tokens[i];
      if (tok === "-m" && i + 1 < tokens.length) {
        return tokens[i + 1];
      }
      if (tok === "-c") {
        return base0;
      }
      if (tok.startsWith("-")) {
        continue;
      }
      return tok.split(/[\\/]/).pop();
    }
  }
  return base0;
}

function getPlayerName(seat) {
  if (paifuData && Array.isArray(paifuData.seating)) {
    const info = paifuData.seating.find((item) => item.seat === seat);
    if (info && info.client_cmd) {
      return extractExecutableName(info.client_cmd, seat);
    }
  }
  return `Player ${seat}`;
}

function formatRoundTitle(roundStr) {
  if (!roundStr || roundStr.length < 2) return roundStr || "";
  const w = WIND_KANJI[roundStr[0]] || roundStr[0];
  const n = roundStr.slice(1);
  return `${w}${n}局`;
}

function formatRoundWithHonba(roundStr, honba) {
  const base = formatRoundTitle(roundStr);
  return honba > 0 ? `${base} ${honba}本場` : base;
}

function cloneSnapshot(snap) {
  return {
    hands: snap.hands.map((h) => [...h]),
    drawn: [...snap.drawn],
    melds: snap.melds.map((ms) => ms.map((m) => ({ ...m, tiles: [...m.tiles] }))),
    rivers: snap.rivers.map((rv) => rv.map((item) => ({ ...item }))),
    riichi: [...snap.riichi],
    scores: [...snap.scores],
    kyoutaku: snap.kyoutaku,
    honba: snap.honba,
    remainingTiles: snap.remainingTiles,
    doraCount: snap.doraCount,
    activeSeat: snap.activeSeat,
    latestDiscard: snap.latestDiscard ? { ...snap.latestDiscard } : null,
    discardAnim: null,
    description: snap.description,
    shortLabel: snap.shortLabel,
    isResultStep: snap.isResultStep,
  };
}

function removeOneTile(arr, tile) {
  const idx = arr.indexOf(tile);
  if (idx !== -1) {
    arr.splice(idx, 1);
    return true;
  }
  return false;
}

function buildRoundSnapshots(roundObj) {
  const dealer = roundObj.dealer;
  const initialHands = [0, 1, 2, 3].map((s) =>
    sortTokens(parseMpsz(roundObj.initial_hands_mpsz[s]))
  );

  const snap0 = {
    hands: initialHands,
    drawn: [null, null, null, null],
    melds: [[], [], [], []],
    rivers: [[], [], [], []],
    riichi: [false, false, false, false],
    scores: [...(roundObj.initial_scores || [25000, 25000, 25000, 25000])],
    kyoutaku: roundObj.kyoutaku || 0,
    honba: roundObj.honba || 0,
    remainingTiles: 70,
    doraCount: 1,
    activeSeat: dealer,
    latestDiscard: null,
    description: `${formatRoundWithHonba(roundObj.round, roundObj.honba || 0)} 配牌 — 親家: ${getPlayerName(dealer)} (${getSeatWindKanji(dealer, dealer)})`,
    shortLabel: "0. 配牌 (Initial Deal)",
    isResultStep: false,
  };

  const snapshots = [snap0];
  const events = roundObj.events || [];

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const next = cloneSnapshot(snapshots[snapshots.length - 1]);
    const s = ev.seat;
    const wKanji = s !== undefined ? getSeatWindKanji(s, dealer) : "";
    const pName = s !== undefined ? getPlayerName(s) : "";

    if (ev.type === "draw") {
      next.drawn[s] = ev.tile;
      next.remainingTiles = Math.max(0, next.remainingTiles - 1);
      next.activeSeat = s;
      next.latestDiscard = null;
      const rinshanTag = ev.rinshan ? " [嶺上]" : "";
      next.description = `${pName} (${wKanji}) 摸牌 ${ev.tile}${rinshanTag}`;
      next.shortLabel = `${i + 1}. ${pName}(${wKanji}) 摸 ${ev.tile}${rinshanTag}`;
    } else if (ev.type === "discard") {
      const prevSnap = snapshots[snapshots.length - 1];
      let sourceHandIndex = -1;
      if (ev.tsumogiri && prevSnap.drawn[s] === ev.tile) {
        sourceHandIndex = prevSnap.hands[s].length;
      } else {
        sourceHandIndex = prevSnap.hands[s].lastIndexOf(ev.tile);
        if (sourceHandIndex === -1) {
          sourceHandIndex = prevSnap.hands[s].length;
        }
      }

      if (next.drawn[s] !== null) {
        if (ev.tsumogiri && next.drawn[s] === ev.tile) {
          next.drawn[s] = null;
        } else {
          next.hands[s].push(next.drawn[s]);
          next.drawn[s] = null;
          removeOneTile(next.hands[s], ev.tile);
          next.hands[s] = sortTokens(next.hands[s]);
        }
      } else {
        removeOneTile(next.hands[s], ev.tile);
        next.hands[s] = sortTokens(next.hands[s]);
      }

      next.rivers[s].push({
        tile: ev.tile,
        tsumogiri: Boolean(ev.tsumogiri),
        riichi: Boolean(ev.riichi),
        calledBy: null,
      });
      next.latestDiscard = { seat: s, index: next.rivers[s].length - 1 };
      next.discardAnim = {
        seat: s,
        tile: ev.tile,
        tsumogiri: Boolean(ev.tsumogiri),
        riichi: Boolean(ev.riichi),
        sourceHandIndex,
      };
      next.activeSeat = s;

      // Check if riichi deposit is accepted (not Ron'd at the very end of the round)
      const isLastDiscardBeforeRon =
        i === events.length - 1 &&
        roundObj.result &&
        roundObj.result.type === "ron";
      if (ev.riichi && !isLastDiscardBeforeRon) {
        next.riichi[s] = true;
        next.scores[s] -= 1000;
        next.kyoutaku += 1;
      }

      const riichiTag = ev.riichi ? " [立直宣言]" : "";
      const modeTag = ev.tsumogiri ? "ツモ切り" : "手出し";
      next.description = `${pName} (${wKanji}) 打 ${ev.tile}${riichiTag} (${modeTag})`;
      next.shortLabel = `${i + 1}. ${pName}(${wKanji}) 打 ${ev.tile}${riichiTag}`;
    } else if (
      ev.type === "chi" ||
      ev.type === "pon" ||
      ev.type === "daiminkan"
    ) {
      for (const ht of ev.hand_tiles || []) {
        removeOneTile(next.hands[s], ht);
      }
      next.hands[s] = sortTokens(next.hands[s]);
      const fromRv = next.rivers[ev.from_seat];
      if (fromRv && fromRv.length > 0) {
        fromRv[fromRv.length - 1].calledBy = s;
      }
      next.melds[s].push({
        type: ev.type,
        tiles: sortTokens([...(ev.hand_tiles || []), ev.called_tile]),
        calledTile: ev.called_tile,
        fromSeat: ev.from_seat,
      });
      next.activeSeat = s;
      next.latestDiscard = null;
      const labelMap = { chi: "吃", pon: "碰", daiminkan: "大明槓" };
      next.description = `${pName} (${wKanji}) ${labelMap[ev.type]} ${ev.mpsz} (來自 ${getPlayerName(ev.from_seat)})`;
      next.shortLabel = `${i + 1}. ${pName}(${wKanji}) ${labelMap[ev.type]} ${ev.mpsz}`;
    } else if (ev.type === "ankan") {
      if (next.drawn[s] !== null) {
        next.hands[s].push(next.drawn[s]);
        next.drawn[s] = null;
      }
      for (const t of ev.tiles || []) {
        removeOneTile(next.hands[s], t);
      }
      next.hands[s] = sortTokens(next.hands[s]);
      next.melds[s].push({
        type: "ankan",
        tiles: [...(ev.tiles || [])],
      });
      next.activeSeat = s;
      next.latestDiscard = null;
      next.description = `${pName} (${wKanji}) 暗槓 ${ev.mpsz}`;
      next.shortLabel = `${i + 1}. ${pName}(${wKanji}) 暗槓 ${ev.mpsz}`;
    } else if (ev.type === "kakan") {
      if (next.drawn[s] !== null) {
        next.hands[s].push(next.drawn[s]);
        next.drawn[s] = null;
      }
      removeOneTile(next.hands[s], ev.tile);
      next.hands[s] = sortTokens(next.hands[s]);
      for (const m of next.melds[s]) {
        if (m.type === "pon" && ev.tiles && ev.tiles.length === 4) {
          m.type = "kakan";
          m.tiles = [...ev.tiles];
          break;
        }
      }
      next.activeSeat = s;
      next.latestDiscard = null;
      next.description = `${pName} (${wKanji}) 加槓 ${ev.tile} (${ev.mpsz})`;
      next.shortLabel = `${i + 1}. ${pName}(${wKanji}) 加槓 ${ev.tile}`;
    } else if (ev.type === "dora") {
      next.doraCount = Math.min(5, next.doraCount + 1);
      next.description = `翻開新寶牌指示牌: ${ev.dora_indicator}`;
      next.shortLabel = `${i + 1}. 新ドラ指示牌 ${ev.dora_indicator}`;
    } else if (ev.type === "chombo") {
      next.activeSeat = s;
      next.description = `${pName} (${wKanji}) 錯和 (チョンボ): ${ev.reason}`;
      next.shortLabel = `${i + 1}. ${pName} チョンボ`;
    }

    snapshots.push(next);
  }

  // Final Result Snapshot
  if (roundObj.result) {
    const finalSnap = cloneSnapshot(snapshots[snapshots.length - 1]);
    if (roundObj.result.scores_after) {
      finalSnap.scores = [...roundObj.result.scores_after];
    }
    finalSnap.isResultStep = true;
    finalSnap.description = formatResultSummaryText(roundObj);
    finalSnap.shortLabel = `${events.length + 1}. 局結算 (${roundObj.result.type})`;
    snapshots.push(finalSnap);
  }

  return snapshots;
}

function formatResultSummaryText(roundObj) {
  const res = roundObj.result;
  if (!res) return "本局結束";
  if (res.type === "tsumo") {
    const yakuStr = (res.yaku || []).map((y) => `${y.name}(${y.han})`).join(", ");
    return `自摸和了 — ${getPlayerName(res.winner)} 和 ${res.winning_tile} | ${res.fu}符 ${res.han}飜 | ${yakuStr}`;
  }
  if (res.type === "ron") {
    const parts = (res.wins || []).map((w) => {
      const yakuStr = (w.yaku || []).map((y) => `${y.name}(${y.han})`).join(", ");
      return `${getPlayerName(w.winner)} 榮和 ${getPlayerName(res.loser)} (${res.winning_tile}) [${w.fu}符 ${w.han}飜: ${yakuStr}]`;
    });
    return parts.join(" ; ");
  }
  if (res.type === "chombo") {
    return `錯和 (チョンボ) — ${getPlayerName(res.offender)}: ${res.reason}`;
  }
  if (res.type === "ryuukyoku_exhaustive") {
    const tp = (res.tenpai_seats || []).map((s) => getPlayerName(s)).join(", ") || "無";
    return `荒牌平局 (流局) — 聽牌: ${tp}`;
  }
  if (res.type === "ryuukyoku_nagashi_mangan") {
    const nm = (res.nagashi_seats || []).map((s) => getPlayerName(s)).join(", ");
    return `流し満貫 — 達成者: ${nm}`;
  }
  if (res.type === "ryuukyoku_abortive") {
    return `途中流局 (${res.reason})`;
  }
  return `本局結束 (${res.type})`;
}

function createTileImg(token, extraClass = "") {
  const img = document.createElement("img");
  img.src = `assets/${token}.png`;
  img.alt = token;
  img.title = token;
  img.className = `tile ${extraClass}`.trim();
  return img;
}

function playDiscardAnimation(discardInfo, fromRect, targetTileEl) {
  if (!fromRect || !targetTileEl) return;
  const toRect = targetTileEl.getBoundingClientRect();
  if (toRect.width === 0 || toRect.height === 0) return;

  const endW = targetTileEl.offsetWidth || toRect.width;
  const endH = targetTileEl.offsetHeight || toRect.height;

  const fromCenterX = fromRect.left + fromRect.width / 2;
  const fromCenterY = fromRect.top + fromRect.height / 2;
  const toCenterX = toRect.left + toRect.width / 2;
  const toCenterY = toRect.top + toRect.height / 2;

  const dx = toCenterX - fromCenterX;
  const dy = toCenterY - fromCenterY;
  const endRot = discardInfo.riichi ? -90 : 0;

  const flyingImg = createTileImg(discardInfo.tile, "flying-discard-tile");
  flyingImg.style.left = `${fromCenterX - fromRect.width / 2}px`;
  flyingImg.style.top = `${fromCenterY - fromRect.height / 2}px`;
  flyingImg.style.width = `${fromRect.width}px`;
  flyingImg.style.height = `${fromRect.height}px`;
  document.body.appendChild(flyingImg);

  targetTileEl.style.visibility = "hidden";

  let cleaned = false;
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    if (flyingImg.parentNode) {
      flyingImg.parentNode.removeChild(flyingImg);
    }
    targetTileEl.style.visibility = "visible";
    if (activeFlyingCleanup === cleanup) {
      activeFlyingCleanup = null;
    }
  };
  activeFlyingCleanup = cleanup;

  if (typeof flyingImg.animate === "function") {
    const anim = flyingImg.animate(
      [
        {
          transform: "translate(0px, 0px) scale(1.08) rotate(0deg)",
          width: `${fromRect.width}px`,
          height: `${fromRect.height}px`,
        },
        {
          transform: `translate(${dx * 0.55}px, ${dy * 0.55 - 16}px) scale(1.16) rotate(${endRot * 0.5}deg)`,
          offset: 0.5,
        },
        {
          transform: `translate(${dx}px, ${dy}px) scale(1) rotate(${endRot}deg)`,
          width: `${endW}px`,
          height: `${endH}px`,
        },
      ],
      {
        duration: 320,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        fill: "forwards",
      }
    );
    anim.onfinish = cleanup;
    anim.oncancel = cleanup;
  } else {
    setTimeout(cleanup, 320);
  }
}

function renderTable(options = {}) {
  if (!paifuData || !roundSnapshots.length) return;
  cleanupActiveAnimation();

  const roundObj = paifuData.rounds[currentRoundIdx];
  const snap = roundSnapshots[currentStepIdx];
  const dealer = roundObj.dealer;

  // Capture source tile bounding rect from hand before DOM update if animating a discard
  let discardFromRect = null;
  if (options.animate && snap.discardAnim) {
    const animSeat = snap.discardAnim.seat;
    const handContainer = document.getElementById(`seat-hand-${animSeat}`);
    if (handContainer && handContainer.children.length > 0) {
      const idx = Math.min(
        Math.max(0, snap.discardAnim.sourceHandIndex),
        handContainer.children.length - 1
      );
      const srcTileEl = handContainer.children[idx];
      if (srcTileEl) {
        discardFromRect = srcTileEl.getBoundingClientRect();
      }
    } else if (handContainer) {
      discardFromRect = handContainer.getBoundingClientRect();
    }
  }

  // Update Header & Controls
  document.getElementById("step-slider").max = String(roundSnapshots.length - 1);
  document.getElementById("step-slider").value = String(currentStepIdx);
  document.getElementById("step-counter").textContent = `${currentStepIdx} / ${roundSnapshots.length - 1}`;
  document.getElementById("btn-first").disabled = currentStepIdx === 0;
  document.getElementById("btn-prev").disabled = currentStepIdx === 0;
  document.getElementById("btn-next").disabled =
    currentStepIdx >= roundSnapshots.length - 1;
  document.getElementById("btn-last").disabled =
    currentStepIdx >= roundSnapshots.length - 1;

  // Center HUD
  document.getElementById("hud-round-title").textContent = formatRoundTitle(
    roundObj.round
  );
  document.getElementById("hud-wall-remain").textContent = `残 ${snap.remainingTiles}`;
  const honbaEl = document.getElementById("hud-honba");
  if (snap.honba > 0) {
    honbaEl.textContent = `${snap.honba} 本場`;
    honbaEl.style.display = "inline";
  } else {
    honbaEl.textContent = "";
    honbaEl.style.display = "none";
  }
  document.getElementById("hud-kyoutaku").textContent = `供託 ${snap.kyoutaku}`;
  const dice = roundObj.dice_wall || [1, 1];
  document.getElementById("hud-dice").textContent = `🎲 ${dice[0]}+${dice[1]}=${dice[0] + dice[1]}`;

  // Dora Indicators
  const doraContainer = document.getElementById("hud-dora-tiles");
  doraContainer.innerHTML = "";
  const allDoraInds = roundObj.dora_indicators || [];
  for (let i = 0; i < 5; i++) {
    if (i < snap.doraCount && i < allDoraInds.length) {
      doraContainer.appendChild(createTileImg(allDoraInds[i]));
    } else {
      doraContainer.appendChild(createTileImg("back"));
    }
  }

  // Render 4 Seats & Rivers
  for (let s = 0; s < 4; s++) {
    const zoneEl = document.getElementById(`seat-zone-${s}`);
    zoneEl.classList.toggle("active-turn", snap.activeSeat === s);

    const wKanji = getSeatWindKanji(s, dealer);
    const windEl = document.getElementById(`seat-wind-${s}`);
    windEl.textContent = wKanji;
    windEl.classList.toggle("dealer", s === dealer);

    document.getElementById(`seat-score-${s}`).textContent =
      snap.scores[s].toLocaleString();
    const riichiEl = document.getElementById(`seat-riichi-${s}`);
    riichiEl.style.display = snap.riichi[s] ? "inline-block" : "none";

    // Closed Hand + Drawn Tile
    const handEl = document.getElementById(`seat-hand-${s}`);
    handEl.innerHTML = "";
    for (const tok of snap.hands[s]) {
      handEl.appendChild(createTileImg(tok));
    }
    if (snap.drawn[s]) {
      handEl.appendChild(
        createTileImg(snap.drawn[s], "drawn-gap highlight-drawn")
      );
    }

    // Melds
    const meldsEl = document.getElementById(`seat-melds-${s}`);
    meldsEl.innerHTML = "";
    for (const m of snap.melds[s]) {
      const mBox = document.createElement("div");
      mBox.className = "meld-box";
      if (m.type === "ankan") {
        mBox.appendChild(createTileImg("back"));
        mBox.appendChild(createTileImg(m.tiles[1] || m.tiles[0]));
        mBox.appendChild(createTileImg(m.tiles[2] || m.tiles[0]));
        mBox.appendChild(createTileImg("back"));
      } else {
        for (const mt of m.tiles) {
          mBox.appendChild(createTileImg(mt));
        }
      }
      meldsEl.appendChild(mBox);
    }

    // River
    const riverEl = document.getElementById(`river-${s}`);
    riverEl.innerHTML = "";
    snap.rivers[s].forEach((rvItem, rIdx) => {
      const wrap = document.createElement("div");
      wrap.className = "river-tile-wrap";
      if (rvItem.tsumogiri) wrap.classList.add("tsumogiri");
      if (rvItem.riichi) wrap.classList.add("riichi-tile");
      if (rvItem.calledBy !== null) wrap.classList.add("called-tile");
      if (
        snap.latestDiscard &&
        snap.latestDiscard.seat === s &&
        snap.latestDiscard.index === rIdx
      ) {
        wrap.classList.add("latest-discard");
      }
      wrap.appendChild(createTileImg(rvItem.tile));
      riverEl.appendChild(wrap);
    });
  }

  // Sidebar Action Banner & Result Box
  document.getElementById("action-banner").textContent = snap.description;
  const resBox = document.getElementById("round-result-box");
  if (snap.isResultStep && roundObj.result) {
    resBox.classList.add("visible");
    resBox.innerHTML = buildResultDetailsHtml(roundObj);
  } else {
    resBox.classList.remove("visible");
    resBox.innerHTML = "";
  }

  // Highlight Active Event in Sidebar List
  const evItems = document.querySelectorAll(".event-item");
  evItems.forEach((el, idx) => {
    const isActive = idx === currentStepIdx;
    el.classList.toggle("active", isActive);
    if (isActive) {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });

  // Trigger discard flight animation from hand to river if applicable
  if (options.animate && snap.discardAnim && discardFromRect) {
    const riverContainer = document.getElementById(`river-${snap.discardAnim.seat}`);
    const latestWrap = riverContainer
      ? riverContainer.querySelector(".river-tile-wrap.latest-discard .tile")
      : null;
    if (latestWrap) {
      playDiscardAnimation(snap.discardAnim, discardFromRect, latestWrap);
    }
  }
}

function buildResultDetailsHtml(roundObj) {
  const res = roundObj.result;
  if (!res) return "";
  const deltas = (res.score_deltas || [0, 0, 0, 0])
    .map((d, s) => {
      const sign = d > 0 ? `+${d}` : `${d}`;
      return `${getPlayerName(s)}: <b>${sign}</b>`;
    })
    .join(" &nbsp;|&nbsp; ");
  return `<div><strong>結算明細 (${res.type})</strong></div>
          <div style="margin-top:4px;">${formatResultSummaryText(roundObj)}</div>
          <div style="margin-top:6px;font-size:0.78rem;color:#cbd5e1;">點數變動：${deltas}</div>`;
}

function populateEventList() {
  const listEl = document.getElementById("event-list");
  listEl.innerHTML = "";
  roundSnapshots.forEach((snap, idx) => {
    const item = document.createElement("div");
    item.className = "event-item";
    item.innerHTML = `<span>${snap.shortLabel}</span><span>#${idx}</span>`;
    item.addEventListener("click", () => {
      stopAutoplay();
      currentStepIdx = idx;
      renderTable({ animate: true });
    });
    listEl.appendChild(item);
  });
}

function selectRound(roundIdx) {
  stopAutoplay();
  currentRoundIdx = roundIdx;
  currentStepIdx = 0;
  document.getElementById("round-select").value = String(roundIdx);
  roundSnapshots = buildRoundSnapshots(paifuData.rounds[roundIdx]);
  populateEventList();
  renderTable({ animate: false });
}

function stepForward() {
  if (currentStepIdx < roundSnapshots.length - 1) {
    currentStepIdx += 1;
    renderTable({ animate: true });
  } else {
    stopAutoplay();
  }
}

function stepBack() {
  if (currentStepIdx > 0) {
    currentStepIdx -= 1;
    renderTable({ animate: false });
  }
}

function toggleAutoplay() {
  if (autoplayTimer !== null) {
    stopAutoplay();
  } else {
    if (currentStepIdx >= roundSnapshots.length - 1) {
      currentStepIdx = 0;
      renderTable({ animate: false });
    }
    const btn = document.getElementById("btn-autoplay");
    btn.textContent = "⏸ Pause (1s)";
    btn.classList.add("playing");
    autoplayTimer = window.setInterval(() => {
      if (currentStepIdx < roundSnapshots.length - 1) {
        currentStepIdx += 1;
        renderTable({ animate: true });
      } else {
        stopAutoplay();
      }
    }, 1000);
  }
}

function stopAutoplay() {
  if (autoplayTimer !== null) {
    clearInterval(autoplayTimer);
    autoplayTimer = null;
  }
  const btn = document.getElementById("btn-autoplay");
  if (btn) {
    btn.textContent = "▶ Autoplay (1s)";
    btn.classList.remove("playing");
  }
}

function goshaRokuiri(pt) {
  const tenths = Math.round(pt * 10);
  const absTenths = Math.abs(tenths);
  const absRounded = Math.floor((absTenths + 4) / 10);
  return tenths >= 0 ? absRounded : -absRounded;
}

function renderFinalStandings(mode) {
  if (!paifuData) return;
  const tbody = document.getElementById("standings-tbody");
  const ptHeader = document.getElementById("standings-pt-header");
  if (!tbody) return;
  tbody.innerHTML = "";

  const dealerFirstSeat = typeof paifuData.dealer_first_seat === "number" ? paifuData.dealer_first_seat : 0;
  const baseRows = (paifuData.final_results || []).map((r) => {
    const rawPt = typeof r.raw_pt === "number" ? r.raw_pt : Number(r.final_pt);
    return {
      seat: r.seat,
      raw_score: r.raw_score,
      raw_pt: rawPt,
    };
  });

  // Sort by raw_score descending, then East 1 seat order to recover standard rank
  baseRows.sort((a, b) => {
    if (b.raw_score !== a.raw_score) return b.raw_score - a.raw_score;
    return ((a.seat - dealerFirstSeat + 4) % 4) - ((b.seat - dealerFirstSeat + 4) % 4);
  });

  let displayRows = [];
  const isPm0 = mode === "plus_minus_zero" || mode === "正負0" || mode === "pm0";
  if (ptHeader) {
    ptHeader.textContent = isPm0 ? "五捨六入 (原pt)" : "得點 (pt)";
  }

  if (isPm0) {
    displayRows = baseRows.map((r) => ({
      ...r,
      final_pt: goshaRokuiri(r.raw_pt),
    }));
    displayRows.sort((a, b) => {
      const absDiff = Math.abs(a.final_pt) - Math.abs(b.final_pt);
      if (absDiff !== 0) return absDiff;
      return ((a.seat - dealerFirstSeat + 4) % 4) - ((b.seat - dealerFirstSeat + 4) % 4);
    });
    displayRows.forEach((r, idx) => {
      if (idx === 0) {
        r.rank = 1;
      } else if (Math.abs(r.final_pt) === Math.abs(displayRows[idx - 1].final_pt)) {
        r.rank = displayRows[idx - 1].rank;
      } else {
        r.rank = idx + 1;
      }
    });
  } else {
    displayRows = baseRows.map((r, idx) => ({
      ...r,
      rank: idx + 1,
      final_pt: r.raw_pt,
    }));
  }

  displayRows.forEach((row) => {
    const tr = document.createElement("tr");
    const ptClass = row.final_pt >= 0 ? "pt-pos" : "pt-neg";
    let ptDisplay = "";
    if (isPm0) {
      const goshaSign = row.final_pt > 0 ? `+${row.final_pt}` : String(row.final_pt);
      const rawSign = row.raw_pt > 0 ? `+${row.raw_pt.toFixed(1)}` : row.raw_pt.toFixed(1);
      ptDisplay = `${goshaSign} <span style="font-size:0.74rem;color:var(--text-secondary);">(${rawSign})</span>`;
    } else {
      ptDisplay = row.final_pt > 0 ? `+${row.final_pt.toFixed(1)}` : row.final_pt.toFixed(1);
    }
    tr.innerHTML = `
      <td>#${row.rank}</td>
      <td>${getPlayerName(row.seat)}</td>
      <td>${row.raw_score.toLocaleString()}</td>
      <td class="${ptClass}">${ptDisplay}</td>
    `;
    tbody.appendChild(tr);
  });
}

function loadPaifuObject(data) {
  paifuData = data;
  const rawMode = (data.rules && data.rules.ranking_mode) || "plus_minus_zero";
  const isPm0 = rawMode === "plus_minus_zero" || rawMode === "正負0" || rawMode === "pm0";
  const modeLabel = isPm0 ? "正負0" : "一般";
  const seedTag = data.seed !== undefined && data.seed !== null ? ` | Seed: ${data.seed}` : "";
  document.getElementById("match-id-badge").textContent = `Match: ${data.match_id || "Loaded"}${seedTag} (${modeLabel})`;

  // Update player executable names on seat headers
  for (let s = 0; s < 4; s++) {
    const nameEl = document.getElementById(`seat-name-${s}`);
    if (nameEl) {
      nameEl.textContent = getPlayerName(s);
      const seatInfo = (data.seating || []).find((item) => item.seat === s);
      if (seatInfo && seatInfo.client_cmd) {
        nameEl.title = seatInfo.client_cmd;
      }
    }
  }

  // Populate Round Selector
  const sel = document.getElementById("round-select");
  sel.innerHTML = "";
  (data.rounds || []).forEach((r, idx) => {
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = `${idx + 1}. ${formatRoundWithHonba(r.round, r.honba || 0)} — ${r.result ? r.result.type : ""}`;
    sel.appendChild(opt);
  });

  // Sync ranking mode selector and populate Final Standings Table
  const modeSelect = document.getElementById("ranking-mode-select");
  const normalizedMode = isPm0 ? "plus_minus_zero" : "normal";
  if (modeSelect) {
    modeSelect.value = normalizedMode;
  }
  renderFinalStandings(normalizedMode);

  if (data.rounds && data.rounds.length > 0) {
    selectRound(0);
  }
}

async function initViewer() {
  // Bind controls
  const modeSelect = document.getElementById("ranking-mode-select");
  if (modeSelect) {
    modeSelect.addEventListener("change", (e) => {
      renderFinalStandings(e.target.value);
    });
  }
  document.getElementById("round-select").addEventListener("change", (e) => {
    selectRound(parseInt(e.target.value, 10));
  });
  document.getElementById("btn-first").addEventListener("click", () => {
    stopAutoplay();
    currentStepIdx = 0;
    renderTable();
  });
  document.getElementById("btn-prev").addEventListener("click", () => {
    stopAutoplay();
    stepBack();
  });
  document.getElementById("btn-next").addEventListener("click", () => {
    stopAutoplay();
    stepForward();
  });
  document.getElementById("btn-last").addEventListener("click", () => {
    stopAutoplay();
    currentStepIdx = Math.max(0, roundSnapshots.length - 1);
    renderTable();
  });
  document.getElementById("btn-autoplay").addEventListener("click", () => {
    toggleAutoplay();
  });
  document.getElementById("step-slider").addEventListener("input", (e) => {
    stopAutoplay();
    currentStepIdx = parseInt(e.target.value, 10);
    renderTable({ animate: true });
  });

  // File input fallback
  document.getElementById("file-input").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        loadPaifuObject(parsed);
      } catch (err) {
        alert(`Failed to parse JSON file: ${err}`);
      }
    };
    reader.readAsText(file);
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.target && ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) {
      return;
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      stopAutoplay();
      stepForward();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      stopAutoplay();
      stepBack();
    } else if (e.key === " ") {
      e.preventDefault();
      toggleAutoplay();
    }
  });

  // Automatically fetch ./paifu.json from the same directory
  try {
    const resp = await fetch("./paifu.json", { cache: "no-store" });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    loadPaifuObject(data);
  } catch (err) {
    document.getElementById("action-banner").innerHTML =
      `無法自動讀取同目錄下的 <code>paifu.json</code> (${err.message})。<br>` +
      `請透過右上角「📂 載入牌譜 JSON」選擇 <code>paifu.json</code>，或啟動本地 HTTP Server (如 <code>python3 -m http.server</code>)。`;
  }
}

window.addEventListener("DOMContentLoaded", initViewer);
