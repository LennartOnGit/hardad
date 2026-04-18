// Härdad chat UI — React 18 via Babel-standalone.
//
// Every data source is a real backend endpoint:
//
//   GET  /me              — bootstrap: user, CEFR state, budget, message history
//   POST /messages        — tutor turn (structured: reply + CEFR + corrections)
//   POST /translate       — EN translation for a phrase or full tutor bubble
//   GET  /dict/{word}     — Swedish dictionary lookup
//   GET  /news            — three topic cards tuned to the user's CEFR level
//
// All LLM-backed calls are counted against the caller's daily token budget;
// the TokenCounter at the bottom shows the remaining quota and turns amber
// when the user is nearly out, red at zero.

const { useState, useEffect, useRef, useMemo, useCallback } = React;

const ROOT = (window.HARDAD && window.HARDAD.rootPath) || "";
const api = (path) => ROOT + path;

async function apiJson(path, opts = {}) {
  const res = await fetch(api(path), {
    credentials: "same-origin",
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) {
    const err = new Error((body && body.error) || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// ========== CEFR progress bar ==========
const CEFR_ORDER = ["A1","A2","B1","B2","C1","C2"];

function cefrLabel(score) {
  // 0..100 → band, matching the backend's app/cefr.py
  const idx = Math.min(5, Math.max(0, Math.floor(score / (100 / 6))));
  return CEFR_ORDER[idx];
}

// InfoTip — styled hover tooltip reusing the design-system `.tooltip` class.
// Replaces native `title=""` tooltips so the UI feel stays consistent and
// the delay/styling is predictable across browsers.
function InfoTip({ children, text, className = "", style }) {
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const onEnter = () => {
    if (!ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const x = Math.max(12, Math.min(window.innerWidth - 300, r.left + r.width / 2 - 140));
    const y = r.bottom + 8;
    setPos({ x, y });
  };
  const onLeave = () => setPos(null);
  return (
    <>
      <span
        ref={ref}
        className={className}
        style={style}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
      >
        {children}
      </span>
      {pos && ReactDOM.createPortal(
        <div
          className="tooltip visible"
          style={{ left: pos.x, top: pos.y, pointerEvents: "none" }}
        >
          <div className="t-en" style={{ marginTop: 0, lineHeight: 1.5 }}>{text}</div>
        </div>,
        document.body
      )}
    </>
  );
}

function CEFRBar({ score, prevScore, sampleSize, level, annotations }) {
  const pct = Math.min(100, Math.max(0, score));
  const shownLevel = level || cefrLabel(score);
  const delta = score - prevScore;
  const trendClass = delta > 0.1 ? "up" : delta < -0.1 ? "down" : "";
  const trendArrow = delta > 0.1 ? "▲" : delta < -0.1 ? "▼" : "─";

  return (
    <div className="cefr-bar">
      {annotations && <Hotspot id="E" style={{top:"10px", right:"10px", position:"absolute"}} title="CEFR progress bar" note="Rolling average of the last 20 per-message CEFR scores returned by the tutor. A1→C2 segmented gradient with animated fill + marker." />}
      <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:"10px"}}>
        <div className="cefr-brand">Härdad</div>
        <div style={{display:"flex", gap:"10px", alignItems:"center"}}>
          <InfoTip
            className={"cefr-trend " + trendClass}
            text="Change vs. previous rolling average (delta of the 20-sample mean between this turn and the last)."
          >
            {trendArrow}{" "}{Math.abs(delta).toFixed(1)}
          </InfoTip>
          <InfoTip
            className="cefr-badge metric"
            text={`Current CEFR level (A1→C2) — derived from the rolling-average score. Score ${score.toFixed(1)}/100 lands in the ${shownLevel} band.`}
          >
            {shownLevel}
          </InfoTip>
        </div>
      </div>

      <div className="cefr-row">
        <div className="cefr-track">
          <div className="cefr-segments">
            {CEFR_ORDER.map(l => <span key={l}/>)}
          </div>
          <div className="cefr-fill" style={{width: pct + "%"}} />
          <div className="cefr-marker" style={{left: pct + "%"}} />
        </div>
      </div>
      <div className="cefr-labels">
        {CEFR_ORDER.map(l => (
          <span key={l} className={l === shownLevel ? "is-current" : ""}>{l}</span>
        ))}
      </div>
      <div className="cefr-meta">
        <InfoTip
          text="Each user message gets a 0-100 score from the tutor. We average the most recent 20 — short enough to react to progress, long enough to smooth noise from one-off easy/hard prompts."
        >
          rolling avg · last 20 messages · ⓘ
        </InfoTip>
        <InfoTip
          text="Composite score mapped to CEFR bands of ~16.7 points: A1 0-16.6, A2 16.7-33.3, B1 33.4-50, B2 50.1-66.6, C1 66.7-83.3, C2 83.4-100."
        >
          score: {score.toFixed(1)} / 100 · {sampleSize || 0} samples · ⓘ
        </InfoTip>
      </div>
    </div>
  );
}

// ========== Tokenize a tutor paragraph ==========
// Splits into { type: 'word' | 'space' | 'punct', text }. The tutor bubble
// wraps each 'word' in <span class="w"> so hover/drag-select can hang off
// DOM events.
function tokenize(text) {
  const tokens = [];
  const re = /([A-Za-zÅÄÖåäö]+)|(\s+)|([^\sA-Za-zÅÄÖåäö]+)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[1]) tokens.push({ type: "word", text: m[1] });
    else if (m[2]) tokens.push({ type: "space", text: m[2] });
    else tokens.push({ type: "punct", text: m[3] });
  }
  return tokens;
}

// Split a tutor reply into paragraphs for rendering. Matches the design's
// `paragraphs` field on seed messages.
function splitParagraphs(text) {
  return String(text || "")
    .split(/\n{2,}|\n/)
    .map(s => s.trim())
    .filter(Boolean);
}

// Build user-message segments from content + corrections list. The tutor
// returns spell/gram corrections with the ORIGINAL substring as it appears
// verbatim in the user's text; we walk the text once, splitting at each hit.
function userSegments(content, corrections) {
  const segments = [];
  if (!corrections || corrections.length === 0) {
    if (content) segments.push({ type: "plain", text: content });
    return segments;
  }
  let cursor = 0;
  const sorted = [];
  for (const c of corrections) {
    if (!c || !c.original) continue;
    const idx = content.indexOf(c.original, cursor);
    if (idx === -1) continue;
    sorted.push({ start: idx, end: idx + c.original.length, c });
    cursor = idx + c.original.length;
  }
  sorted.sort((a, b) => a.start - b.start);
  cursor = 0;
  for (const s of sorted) {
    if (s.start > cursor) {
      segments.push({ type: "plain", text: content.slice(cursor, s.start) });
    }
    const kind = s.c.type === "gram" ? "gram" : "spell";
    if (kind === "spell") {
      segments.push({ type: "spell", text: s.c.original, fix: s.c.fix || "" });
    } else {
      segments.push({ type: "gram", text: s.c.fix || s.c.original });
    }
    cursor = s.end;
  }
  if (cursor < content.length) {
    segments.push({ type: "plain", text: content.slice(cursor) });
  }
  return segments;
}

// ========== Tutor message ==========
function TutorMessage({ msg, language, onToggleLang, onWordHover, onWordLeave, selection, onSelectChange, annotations, translating }) {
  const paragraphs = language === "en" ? (msg.englishParagraphs || [msg.content]) : (msg.paragraphs || splitParagraphs(msg.content));
  const isSelectedInMsg = selection.msgId === msg.id;

  const handleDown = (e, pIdx, wIdx) => {
    if (language !== "sv") return;
    if (e.target.classList.contains("w")) {
      onSelectChange({ msgId: msg.id, startP: pIdx, startW: wIdx, endP: pIdx, endW: wIdx, dragging: true });
    }
  };
  const handleEnter = (e, pIdx, wIdx) => {
    if (selection.dragging && selection.msgId === msg.id) {
      onSelectChange({ ...selection, endP: pIdx, endW: wIdx });
    }
  };

  const inSelection = (pIdx, wIdx) => {
    if (!isSelectedInMsg) return false;
    const a = [selection.startP, selection.startW];
    const b = [selection.endP, selection.endW];
    const lo = a[0] < b[0] || (a[0]===b[0] && a[1]<=b[1]) ? a : b;
    const hi = lo === a ? b : a;
    if (pIdx < lo[0] || pIdx > hi[0]) return false;
    if (pIdx === lo[0] && wIdx < lo[1]) return false;
    if (pIdx === hi[0] && wIdx > hi[1]) return false;
    return true;
  };

  return (
    <div className="msg-wrap tutor">
      <div className="msg tutor" data-msg-id={msg.id}>
        {paragraphs.map((p, pIdx) => {
          const tokens = tokenize(p);
          let wordIdx = -1;
          return (
            <p key={pIdx}>
              {tokens.map((tok, i) => {
                if (tok.type === "word" && language === "sv") {
                  wordIdx++;
                  const wi = wordIdx;
                  const selected = inSelection(pIdx, wi);
                  return (
                    <span
                      key={i}
                      className={"w" + (selected ? " selected" : "")}
                      onMouseEnter={(e) => { onWordHover(e, tok.text); handleEnter(e, pIdx, wi); }}
                      onMouseLeave={onWordLeave}
                      onMouseDown={(e) => handleDown(e, pIdx, wi)}
                    >{tok.text}</span>
                  );
                }
                return <React.Fragment key={i}>{tok.text}</React.Fragment>;
              })}
            </p>
          );
        })}

        {annotations && <Hotspot id="A" style={{top:"-11px", left:"-11px", right:"auto"}} title="Per-word dictionary" note="Hovering a word fires a lookup: first the in-memory seed, then /dict/{word} which falls through to the LLM. Resolved entries are cached in DictCache server-side." />}
        {annotations && <Hotspot id="B" style={{top:"-11px", left:"50%", right:"auto", transform:"translateX(-50%)"}} title="Drag-select phrase translation" note="Click + drag across words. On mouseup the selection is POSTed to /translate (Haiku) and the result shown in a popover near the cursor." />}

        <div className="msg-translate" onClick={(e) => e.stopPropagation()}>
          <button className={language === "sv" ? "active" : ""} onClick={() => onToggleLang(msg, "sv")}>
            <span className="flag">🇸🇪</span> SV
          </button>
          <button className={language === "en" ? "active" : ""} onClick={() => onToggleLang(msg, "en")} disabled={translating}>
            <span className="flag">🇬🇧</span> {translating ? "..." : "EN"}
          </button>
        </div>

        {annotations && <Hotspot id="C" style={{bottom:"-11px", right:"-11px", top:"auto"}} title="Per-message translate" note="The SV/EN pill POSTs the full message to /translate. The English rendering is cached per-message so toggling back and forth is free." />}
      </div>
    </div>
  );
}

// ========== User message ==========
function UserMessage({ msg, annotations }) {
  const segments = msg.segments && msg.segments.length
    ? msg.segments
    : [{ type: "plain", text: msg.content || "" }];
  const hasCorrections = segments.some(s => s.type !== "plain");

  // Render *all* correction notes below the message, ordered by where each
  // `original` substring actually appears in the content. Same walk as
  // `userSegments` so the inline markers and the per-note rows align.
  const orderedCorrections = useMemo(() => {
    if (!msg.corrections || msg.corrections.length === 0) return [];
    const content = msg.content || "";
    let cursor = 0;
    const placed = [];
    for (const c of msg.corrections) {
      if (!c || !c.original) continue;
      const idx = content.indexOf(c.original, cursor);
      if (idx === -1) continue;
      placed.push({ start: idx, c });
      cursor = idx + c.original.length;
    }
    placed.sort((a, b) => a.start - b.start);
    return placed.map(p => p.c);
  }, [msg.corrections, msg.content]);

  return (
    <div className="msg-wrap user">
      <div className="msg user">
        <p>
          {segments.map((seg, i) => {
            if (seg.type === "plain") return <React.Fragment key={i}>{seg.text}</React.Fragment>;
            if (seg.type === "spell") return (
              <React.Fragment key={i}>
                <span className="spell-err">{seg.text}</span>
                <span className="spell-fix">{seg.fix}</span>
              </React.Fragment>
            );
            if (seg.type === "gram") return <span key={i} className="gram-fix">{seg.text}</span>;
            return null;
          })}
        </p>
        {orderedCorrections.length > 0 && (
          <div className="correction-notes">
            {orderedCorrections.map((c, i) => c.note ? (
              <div
                key={i}
                className={"correction-note" + (c.type === "gram" ? " grammar" : "")}
              >
                <span className="dot"/>
                {(c.type === "gram" ? "grammar · " : "spelling · ") + c.note}
              </div>
            ) : null)}
          </div>
        )}
        {annotations && hasCorrections && (
          <Hotspot id="D" style={{top:"-11px", left:"-11px", right:"auto"}} title="Inline corrections" note="The tutor returns structured {type, original, fix, note} entries for every spelling/grammar error in the user's message. Spell errors: red strike-through + green replacement. Grammar: blue replacement with dashed underline." />
        )}
      </div>
    </div>
  );
}

// ========== Token counter (daily budget) ==========
function TokenCounter({ remaining, budget }) {
  // Budget can be null for admin users (unlimited).
  if (budget == null) {
    return (
      <div className="token-counter">
        <div className="tc-label">
          <span>Consumption</span>
          <span className="tc-nums"><span className="tc-dim">unlimited (admin)</span></span>
        </div>
        <div className="tc-track">
          <div className="tc-fill" style={{width: "100%", background: "var(--brand)"}}/>
        </div>
      </div>
    );
  }
  const safeRemaining = Math.max(0, remaining || 0);
  const used = Math.max(0, budget - safeRemaining);
  const pct = budget > 0 ? (used / budget) * 100 : 0;
  const color = pct > 90 ? "var(--err)" : pct > 70 ? "var(--l-c2)" : "var(--ok)";
  return (
    <div className="token-counter" title="Daily per-user token budget. Resets at midnight server-time. Every tutor reply, translation, and uncached dictionary lookup counts.">
      <div className="tc-label">
        <span>Daily budget</span>
        <span className="tc-nums" style={{color}}>
          {used.toLocaleString()} <span className="tc-dim">/ {budget.toLocaleString()} tokens · {safeRemaining.toLocaleString()} left</span>
        </span>
      </div>
      <div className="tc-track">
        <div className="tc-fill" style={{width: Math.min(100, pct) + "%", background: color}}/>
      </div>
    </div>
  );
}

// ========== News cards ==========
const CATEGORY_ACCENTS = {
  politik: "var(--l-b1)",
  liv: "var(--l-c2)",
  hogtider: "var(--brand)",
  internationellt: "var(--l-a2)",
};

function NewsCards({ topics, level, loading, onPick, annotations }) {
  return (
    <div className="news-strip" style={{position:"relative"}}>
      {annotations && <Hotspot id="F" style={{top:"18px", left:"-4px", position:"absolute", zIndex:50}} title="News hooks" note="Three topic cards tuned to the user's CEFR level (A1-C2), cached per-day on the server. Clicking one seeds the conversation with the tutor's Swedish hook line." />}
      <div className="news-head">
        <span>Diskutera dagens nyheter</span>
        <span className="news-sub">
          {loading ? "Hämtar…" : level ? `Nivå: ${level} · klicka ett kort` : "klicka ett kort"}
        </span>
      </div>
      <div className="news-grid">
        {(topics || []).map(card => {
          const accent = CATEGORY_ACCENTS[card.category] || "var(--accent)";
          return (
            <button key={card.id || card.headline_sv} className="news-card" onClick={() => onPick(card)}>
              <div className="nc-cat" style={{color: accent}}>
                <span className="nc-dot" style={{background: accent}}/>
                {card.category_sv || card.category}
              </div>
              <div className="nc-headline">{card.headline_sv}</div>
              <div className="nc-kicker">{card.kicker_sv}</div>
              <div className="nc-foot">
                <span>{card.source_sv || ""}</span>
                <span className="nc-arrow">→ chatta</span>
              </div>
            </button>
          );
        })}
        {!loading && (!topics || topics.length === 0) && (
          <div style={{gridColumn:"1 / -1", color:"var(--ink-mute)", fontSize:12, padding:"12px 0"}}>
            Inga nyhetskort tillgängliga just nu.
          </div>
        )}
      </div>
    </div>
  );
}

// ========== Hotspot (design feature annotation dot) ==========
function Hotspot({ id, title, note, style }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (open && ref.current) {
      const r = ref.current.getBoundingClientRect();
      let left = r.left + r.width / 2 - 120;
      left = Math.max(10, Math.min(left, window.innerWidth - 260));
      setPos({ top: r.bottom + 8, left });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target) && !e.target.closest(".hotspot-note")) {
        setOpen(false);
      }
    };
    setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const styleMerged = { position: "absolute", top: "-8px", right: "-8px", ...(style || {}) };
  return (
    <>
      <span
        ref={ref}
        className="hotspot"
        style={styleMerged}
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
      >{id}</span>
      {open && ReactDOM.createPortal(
        <div className="hotspot-note" style={{ position:"fixed", top: pos.top, left: pos.left, zIndex: 9999 }}>
          <div className="hn-title">Feature {id}</div>
          <div style={{fontWeight:600, marginBottom:4}}>{title}</div>
          <div style={{color:"var(--ink-dim)"}}>{note}</div>
        </div>,
        document.body
      )}
    </>
  );
}

// ========== Tooltip ==========
function WordTooltip({ data }) {
  if (!data) return null;
  if (data.loading) {
    return (
      <div className={"tooltip " + (data.visible ? "visible" : "")} style={{ left: data.x, top: data.y }}>
        <div className="t-word">{data.word} <span className="t-pos">…</span></div>
        <div className="t-en">Looking up…</div>
      </div>
    );
  }
  const entry = data.entry;
  if (!entry) {
    return (
      <div className={"tooltip " + (data.visible ? "visible" : "")} style={{ left: data.x, top: data.y }}>
        <div className="t-word">{data.word} <span className="t-pos">unknown</span></div>
        <div className="t-en">No entry.</div>
      </div>
    );
  }
  return (
    <div className={"tooltip " + (data.visible ? "visible" : "")} style={{ left: data.x, top: data.y }}>
      <div className="t-word">
        {data.word}
        <span className="t-pos">{entry.pos || "?"}</span>
      </div>
      <div className="t-en">{entry.en}</div>
      {entry.note && <div className="t-extra"><span>note</span><span>{entry.note}</span></div>}
    </div>
  );
}

// ========== Selection popover ==========
function SelectionPopover({ data }) {
  if (!data || !data.src) return null;
  return (
    <div className={"sel-popover " + (data.visible ? "visible" : "")} style={{ left: data.x, top: data.y }}>
      <div className="sp-label">Translate selection</div>
      <div className="sp-src">"{data.src}"</div>
      <div className="sp-en">{data.loading ? "→ Översätter…" : "→ " + (data.en || "(no translation)")}</div>
    </div>
  );
}

// ========== Main Chat ==========
function Chat({ annotations }) {
  const [bootstrapped, setBootstrapped] = useState(false);
  const [bootError, setBootError] = useState(null);
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [cefr, setCefr] = useState({ avg: null, level: null, samples_count: 0 });
  const [prevAvg, setPrevAvg] = useState(0);
  const [budget, setBudget] = useState({ daily_token_budget: null, remaining: null });
  const [input, setInput] = useState("");
  const inputRef = useRef(null);
  const [sending, setSending] = useState(false);
  const [quotaError, setQuotaError] = useState(null);

  const [langByMsg, setLangByMsg] = useState({});
  const [translating, setTranslating] = useState({});          // msgId → bool
  const [englishCache, setEnglishCache] = useState({});        // msgId → [paragraphs]
  const [phraseCache, setPhraseCache] = useState({});          // "sv phrase" → "en"

  const [tooltip, setTooltip] = useState(null);
  const [selection, setSelection] = useState({ msgId: null, startP: 0, startW: 0, endP: 0, endW: 0, dragging: false });
  const [selectionPopover, setSelectionPopover] = useState(null);

  const [newsTopics, setNewsTopics] = useState([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsLevel, setNewsLevel] = useState(null);

  // ---------- Dictionary seed (loaded once) ----------
  const dictSeedRef = useRef(null);
  const dictMissRef = useRef({}); // word → entry | null (network cache)
  useEffect(() => {
    fetch(api("/static/dict_sv.json"), { credentials: "same-origin" })
      .then(r => r.json())
      .then(doc => { dictSeedRef.current = doc.entries || {}; })
      .catch(() => { dictSeedRef.current = {}; });
  }, []);

  // ---------- Bootstrap from /me ----------
  useEffect(() => {
    apiJson("/me")
      .then(data => {
        setSessionId(data.session_id);
        setCefr(data.cefr);
        setPrevAvg(data.cefr.avg || 0);
        setBudget(data.budget);
        const msgs = (data.messages || []).map(m => ({
          id: m.id,
          role: m.role === "assistant" ? "tutor" : "user",
          content: m.content,
          cefr_score: m.cefr_score,
          corrections: m.corrections || [],
          paragraphs: splitParagraphs(m.content),
          segments: m.role === "user" ? userSegments(m.content, m.corrections || []) : null,
        }));
        setMessages(msgs);
        setBootstrapped(true);
      })
      .catch(e => { setBootError(e.message || "Kunde inte ladda."); setBootstrapped(true); });
  }, []);

  // ---------- News cards ----------
  useEffect(() => {
    if (!bootstrapped) return;
    setNewsLoading(true);
    apiJson("/news")
      .then(data => { setNewsTopics(data.topics || []); setNewsLevel(data.cefr_level); })
      .catch(() => { setNewsTopics([]); })
      .finally(() => setNewsLoading(false));
  }, [bootstrapped]);

  // ---------- Dictionary lookup with local+remote cache ----------
  const lookupWord = useCallback(async (raw) => {
    const key = raw.toLowerCase().replace(/[^\wåäöÅÄÖ-]/g, "");
    if (!key) return null;
    const seed = dictSeedRef.current;
    if (seed && seed[key]) return seed[key];
    if (key in dictMissRef.current) return dictMissRef.current[key];
    try {
      const data = await apiJson(`/dict/${encodeURIComponent(key)}`);
      if (data.usage) {
        setBudget(b => ({ ...b, remaining: data.usage.remaining }));
      }
      dictMissRef.current[key] = data.entry || null;
      return data.entry || null;
    } catch (e) {
      dictMissRef.current[key] = null;
      return null;
    }
  }, []);

  // ---------- Word hover handler (debounced via setTooltip key) ----------
  const hoverSeqRef = useRef(0);
  const onWordHover = useCallback((e, word) => {
    if (selection.dragging) return;
    const rect = e.target.getBoundingClientRect();
    const x = rect.left + rect.width / 2 - 120;
    const y = rect.bottom + 8;
    const seq = ++hoverSeqRef.current;
    setTooltip({ word, x, y, visible: true, loading: true, entry: null });
    lookupWord(word).then(entry => {
      if (hoverSeqRef.current !== seq) return; // stale
      setTooltip(t => t && t.word === word ? { ...t, loading: false, entry } : t);
    });
  }, [selection.dragging, lookupWord]);

  const onWordLeave = useCallback(() => {
    setTooltip(t => t ? { ...t, visible: false } : null);
  }, []);

  // ---------- Translation helpers ----------
  const translateText = useCallback(async (text) => {
    if (phraseCache[text] !== undefined) return phraseCache[text];
    try {
      const data = await apiJson("/translate", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      if (data.usage) setBudget(b => ({ ...b, remaining: data.usage.remaining }));
      setPhraseCache(c => ({ ...c, [text]: data.translation }));
      return data.translation;
    } catch (e) {
      if (e.status === 429) setQuotaError(e.body && e.body.error);
      return null;
    }
  }, [phraseCache]);

  const onToggleLang = useCallback(async (msg, lang) => {
    setLangByMsg(prev => ({ ...prev, [msg.id]: lang }));
    if (lang !== "en") return;
    if (englishCache[msg.id]) return;
    setTranslating(t => ({ ...t, [msg.id]: true }));
    const english = await translateText(msg.content);
    if (english) {
      setEnglishCache(c => ({ ...c, [msg.id]: splitParagraphs(english) }));
      setMessages(ms => ms.map(m => m.id === msg.id ? { ...m, englishParagraphs: splitParagraphs(english) } : m));
    }
    setTranslating(t => ({ ...t, [msg.id]: false }));
  }, [englishCache, translateText]);

  // ---------- Drag-select → /translate ----------
  useEffect(() => {
    const onUp = async (e) => {
      if (!selection.dragging) return;
      setSelection(s => ({ ...s, dragging: false }));
      const msg = messages.find(m => m.id === selection.msgId);
      if (!msg) return;
      const paragraphs = msg.paragraphs || splitParagraphs(msg.content);
      const startsFirst =
        selection.startP < selection.endP ||
        (selection.startP === selection.endP && selection.startW <= selection.endW);
      const lo = startsFirst ? [selection.startP, selection.startW] : [selection.endP, selection.endW];
      const hi = startsFirst ? [selection.endP, selection.endW] : [selection.startP, selection.startW];
      const selectedWords = [];
      for (let p = lo[0]; p <= hi[0]; p++) {
        const tokens = tokenize(paragraphs[p] || "");
        let wIdx = -1;
        for (const t of tokens) {
          if (t.type === "word") {
            wIdx++;
            if (p === lo[0] && wIdx < lo[1]) continue;
            if (p === hi[0] && wIdx > hi[1]) continue;
            selectedWords.push(t.text);
          }
        }
      }
      if (selectedWords.length >= 2) {
        const joined = selectedWords.join(" ");
        const x = e.clientX - 160;
        const y = e.clientY + 14;
        setSelectionPopover({ src: joined, en: null, loading: true, visible: true, x, y });
        setTooltip(null);
        const en = await translateText(joined);
        setSelectionPopover(p => p && p.src === joined
          ? { ...p, loading: false, en: en || "(ingen översättning)" }
          : p);
      }
    };
    const onEsc = (e) => {
      if (e.key === "Escape") {
        setSelectionPopover(null);
        setSelection({ msgId: null, startP: 0, startW: 0, endP: 0, endW: 0, dragging: false });
      }
    };
    const onClick = (e) => {
      if (!e.target.closest(".sel-popover") && !e.target.closest(".w")) {
        setSelectionPopover(null);
      }
    };
    window.addEventListener("mouseup", onUp);
    window.addEventListener("keydown", onEsc);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("keydown", onEsc);
      window.removeEventListener("mousedown", onClick);
    };
  }, [selection, messages, translateText]);

  // ---------- Send ----------
  const sendMessage = useCallback(async (content) => {
    const trimmed = (content || "").trim();
    if (!trimmed || sending) return;
    setSending(true);
    setQuotaError(null);
    const optimisticId = "u-local-" + Date.now();
    setMessages(ms => [...ms, {
      id: optimisticId,
      role: "user",
      content: trimmed,
      segments: [{ type: "plain", text: trimmed }],
      corrections: [],
    }]);
    try {
      const data = await apiJson("/messages", {
        method: "POST",
        body: JSON.stringify({ content: trimmed, session_id: sessionId }),
      });
      setSessionId(data.session_id);
      setPrevAvg(cefr.avg || 0);
      setCefr({
        avg: data.cefr.avg,
        level: data.cefr.level,
        samples_count: data.cefr.samples_count,
      });
      if (data.usage) {
        setBudget(b => ({ ...b, remaining: data.usage.remaining }));
      }
      const corrections = data.corrections || [];
      // Replace the optimistic message with the corrected version
      setMessages(ms => {
        const next = ms.map(m => m.id === optimisticId
          ? {
              ...m,
              id: optimisticId,
              corrections,
              segments: userSegments(trimmed, corrections),
              cefr_score: data.cefr.score,
            }
          : m);
        const tutorMsg = {
          id: "t-" + Date.now(),
          role: "tutor",
          content: data.reply,
          paragraphs: splitParagraphs(data.reply),
          cefr_score: null,
        };
        return [...next, tutorMsg];
      });
    } catch (e) {
      if (e.status === 429) {
        setQuotaError((e.body && e.body.error) || "Dagens kvot slut.");
        // Roll back the optimistic message so the user doesn't see it as sent
        setMessages(ms => ms.filter(m => m.id !== optimisticId));
      } else {
        setMessages(ms => ms.filter(m => m.id !== optimisticId).concat({
          id: "err-" + Date.now(),
          role: "tutor",
          content: "Hoppsan! " + (e.message || "Något gick fel."),
          paragraphs: ["Hoppsan! " + (e.message || "Något gick fel.")],
        }));
      }
    } finally {
      setSending(false);
    }
  }, [sending, sessionId, cefr.avg]);

  const onPickNews = useCallback((card) => {
    if (!card || !card.hook_sv) return;
    // Pre-fill the composer with the tutor's proposed opener so the user
    // can eyeball it, tweak it, or rephrase before sending. Pre-seed the
    // EN translation cache too — the user might tap SV/EN on it.
    setInput(card.hook_sv);
    if (card.hook_en) {
      setPhraseCache(c => ({ ...c, [card.hook_sv]: card.hook_en }));
    }
    // Focus + scroll the composer into view. Small timeout to let React
    // commit the state change first.
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
        // Move the cursor to the end so the user can keep typing.
        const len = card.hook_sv.length;
        try { inputRef.current.setSelectionRange(len, len); } catch (_) {}
        inputRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 40);
  }, []);

  const onSubmit = (e) => {
    e.preventDefault();
    const text = input;
    setInput("");
    sendMessage(text);
  };

  const cefrScore = cefr.avg || 0;

  if (!bootstrapped) {
    return <div style={{padding:"80px", textAlign:"center", color:"var(--ink-dim)"}}>Laddar…</div>;
  }
  if (bootError) {
    return (
      <div style={{padding:"80px", textAlign:"center", color:"var(--err)"}}>
        {bootError} — <a href={api("/")} style={{color:"var(--accent)"}}>Logga in igen</a>
      </div>
    );
  }

  return (
    <div className={"app-shell " + (annotations ? "annot-on" : "")}>
      <CEFRBar
        score={cefrScore}
        prevScore={prevAvg}
        sampleSize={cefr.samples_count}
        level={cefr.level}
        annotations={annotations}
      />

      <NewsCards
        topics={newsTopics}
        level={newsLevel}
        loading={newsLoading}
        onPick={onPickNews}
        annotations={annotations}
      />

      <div className="msg-list">
        {(() => {
          // Hoist first-index lookups so hotspots A/B/C render only on the
          // first tutor bubble, and D only on the first user bubble that
          // actually has corrections to annotate — otherwise the dots would
          // clutter every turn.
          const firstTutorIdx = messages.findIndex(mm => mm.role === "tutor");
          const firstCorrectedUserIdx = messages.findIndex(
            mm => mm.role === "user" && mm.corrections && mm.corrections.length > 0
          );
          return messages.map((m, idx) => m.role === "tutor" ? (
            <TutorMessage
              key={m.id}
              msg={m}
              language={langByMsg[m.id] || "sv"}
              onToggleLang={onToggleLang}
              onWordHover={onWordHover}
              onWordLeave={onWordLeave}
              selection={selection}
              onSelectChange={setSelection}
              translating={translating[m.id]}
              annotations={annotations && idx === firstTutorIdx}
            />
          ) : (
            <UserMessage
              key={m.id}
              msg={m}
              annotations={annotations && idx === firstCorrectedUserIdx}
            />
          ));
        })()}
      </div>

      <div className="composer">
        {quotaError && (
          <div style={{color:"var(--err)", fontSize:12, marginBottom:8, fontFamily:"var(--font-mono)"}}>
            {quotaError}
          </div>
        )}
        <form className="composer-inner" onSubmit={onSubmit}>
          <input
            ref={inputRef}
            type="text"
            placeholder={sending ? "Skickar…" : "Skriv något på svenska..."}
            value={input}
            onChange={e => setInput(e.target.value)}
            disabled={sending}
            autoFocus
          />
          <button type="submit" disabled={sending || !input.trim()}>Skicka</button>
        </form>
        <TokenCounter remaining={budget.remaining} budget={budget.daily_token_budget} />
      </div>

      <WordTooltip data={tooltip} />
      <SelectionPopover data={selectionPopover} />
    </div>
  );
}

window.Chat = Chat;
window.tokenize = tokenize;
