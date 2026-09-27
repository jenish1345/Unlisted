import { ReactNode, useRef, useState } from "react";
import { Check, Copy, LoaderCircle } from "lucide-react";
import {
  activeStage, failedStage, fmtNum, matchRepo, safeUrl,
  Fallback, PENDING_COPY, RunState, Stage, STAGE_LABEL, STAGES, VERDICT_MEANING,
} from "../lib/pipeline";

const serif = { fontFamily: "'Instrument Serif', serif" };
const secs = (ms?: number) => (ms != null ? `${(ms / 1000).toFixed(1)}s` : "");

type Props = { state: RunState; onFallback: (f: Fallback) => void; onRerun: () => void };

export default function ResultsSection({ state, onFallback, onRerun }: Props) {
  const active = activeStage(state);
  const who = state.data.extraction ? `@${state.data.extraction.username}` : state.demo ? "" : `@${state.username}`;
  const meta = [
    state.demo ? "Recorded demo" : "Live",
    who,
    state.companyName && `→ ${state.companyName}`,
    state.finished && state.elapsed != null && `finished in ${secs(state.elapsed)}`,
  ].filter(Boolean).join(" · ");

  return (
    <section id="results" className="relative bg-black pt-24 pb-16 px-6 scroll-mt-4">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(255,255,255,0.04)_0%,_transparent_60%)] pointer-events-none" />
      <div className="relative max-w-5xl mx-auto">
        <div className="flex flex-wrap items-end justify-between gap-4 mb-8">
          <div>
            <p className="label mb-3">The run</p>
            <h2 className="text-4xl md:text-6xl text-white tracking-tight" style={serif}>
              {state.finished ? "Here's what holds up." : <>Reading the <em className="italic text-white/60">evidence</em>…</>}
            </h2>
          </div>
          {state.finished && (
            <button type="button" onClick={onRerun} className="liquid-glass rounded-full px-6 py-2 text-white text-sm font-medium hover:bg-white/5 transition-colors">
              Run another
            </button>
          )}
        </div>

        <Notice state={state} onFallback={onFallback} />

        {/* Stage strip */}
        <div className="grid grid-cols-5 gap-2 md:gap-3 mb-3">
          {STAGES.map((s) => {
            const st = state.status[s];
            const isActive = s === active;
            const sub = isActive ? "running…" : st === "done" ? "done" : st === "error" ? "failed"
              : st === "unresolved" ? "unresolved" : st === "skipped" ? "not run" : "queued";
            const tone = isActive ? "text-white" : st === "done" ? "text-white/80"
              : st === "error" || st === "unresolved" ? "text-red-300" : "text-white/30";
            return (
              <div key={s} className={`liquid-glass rounded-2xl px-2 md:px-4 py-3 ${isActive ? "bg-white/10" : ""}`}>
                <div className={`text-xs md:text-sm font-medium ${tone} flex items-center gap-1.5`}>
                  {isActive && <LoaderCircle size={12} className="animate-spin shrink-0" />}
                  {st === "done" && !isActive && <Check size={12} className="shrink-0" />}
                  <span className="truncate">{STAGE_LABEL[s]}</span>
                </div>
                <div className="text-[10px] md:text-xs text-white/40 mt-0.5 truncate">{sub}</div>
              </div>
            );
          })}
        </div>
        <p className="text-white/40 text-xs mb-12">{meta}</p>

        <div className="flex flex-col gap-6">
          <StageCard n="01" label="Extraction" title="What they've actually built" when={state.status.extraction === "done" ? state.at.extraction : undefined}>
            <Extraction state={state} />
          </StageCard>
          <StageCard n="02" label="Role thesis" title="The role we'd pitch" when={state.status.thesis === "done" ? state.at.thesis : undefined}>
            <Thesis state={state} />
          </StageCard>
          <StageCard n="03" label="Independent verification" title="Does the evidence hold up?" when={state.status.verification === "done" ? state.at.verification : undefined}>
            <Verification state={state} />
          </StageCard>
          <StageCard n="04" label="Outreach" title="The pitch to send" when={state.status.pitch === "done" ? state.at.pitch : undefined}>
            <Pitch state={state} />
          </StageCard>
        </div>
      </div>
    </section>
  );
}

function StageCard({ n, label, title, when, children }: { n: string; label: string; title: string; when?: number; children: ReactNode }) {
  return (
    <article className="liquid-glass rounded-3xl p-6 md:p-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-6">
        <div>
          <p className="label mb-2">{n} · {label}</p>
          <h3 className="text-2xl md:text-4xl text-white tracking-tight" style={serif}>{title}</h3>
        </div>
        {when != null && <span className="text-white/30 text-xs font-mono">{secs(when)}</span>}
      </div>
      {children}
    </article>
  );
}

// A failed live run offers the recorded demo; a demo replay always says it is one.
function Notice({ state, onFallback }: { state: RunState; onFallback: (f: Fallback) => void }) {
  if (state.demo) {
    const f = state.fallbackFrom;
    return (
      <div className="liquid-glass rounded-2xl px-5 py-4 mb-6">
        <p className="text-white text-sm font-medium">Demo replay · recorded run, not a live result</p>
        <p className="text-white/50 text-sm mt-1">
          {f
            ? `Shown because the live run for @${f.username}${f.company ? " → " + f.company : ""} stopped at ${f.stage}: ${f.message}`
            : "The backend is replaying demo_cache.json. No GitHub, OpenAI or Gemini calls are made."}
        </p>
      </div>
    );
  }
  const failed = failedStage(state);
  if (!state.finished || !failed) return null;
  const unresolved = state.status[failed] === "unresolved";
  const message = (unresolved ? state.unresolved : state.error[failed]) || "";
  return (
    <div className="liquid-glass rounded-2xl px-5 py-4 mb-6 flex flex-wrap items-center justify-between gap-4 bg-red-500/[0.06]">
      <div className="min-w-0">
        <p className="text-red-200 text-sm font-medium">{unresolved ? "Live run finished without a verified role" : `Live run stopped at ${failed}`}</p>
        <p className="text-white/50 text-sm mt-1 break-words">{message}</p>
      </div>
      <button
        type="button"
        onClick={() => onFallback({ stage: failed, message, username: state.username, company: state.companyName })}
        className="liquid-glass rounded-full px-5 py-2 text-white text-sm font-medium hover:bg-white/5 transition-colors shrink-0"
      >
        Replay recorded demo instead
      </button>
    </div>
  );
}

function StateBlock({ state, stage }: { state: RunState; stage: Stage }) {
  const st = state.status[stage];
  const active = activeStage(state);
  if (st === "error")
    return (
      <div className="rounded-2xl bg-red-500/[0.08] px-5 py-4 text-sm text-red-100/90 break-words">
        <strong className="block mb-1 text-red-200">{stage} failed</strong>{state.error[stage]}
      </div>
    );
  if (st === "skipped") {
    const failed = STAGES.find((s) => state.status[s] === "error");
    return (
      <p className="text-white/40 text-sm">
        {!failed && state.status.verification === "unresolved"
          ? "Not drafted: no role survived verification, so there are no verified claims to pitch."
          : `Not run: the pipeline stopped when ${failed || "an earlier stage"} failed.`}
      </p>
    );
  }
  if (st === "pending") {
    if (active !== stage) return <p className="text-white/30 text-sm">Queued. Waiting on {active}.</p>;
    return (
      <div>
        <p className="flex items-center gap-2 text-white/70 text-sm mb-4">
          <LoaderCircle size={16} className="animate-spin" />{PENDING_COPY[stage]}
        </p>
        {[72, 54, 63].map((w) => <div key={w} className="h-3 rounded-full bg-white/[0.06] animate-pulse mb-2.5" style={{ width: `${w}%` }} />)}
      </div>
    );
  }
  return null;
}

const blocked = (state: RunState, stage: Stage) => ["pending", "error", "skipped"].includes(state.status[stage]);

function Chip({ children }: { children: ReactNode }) {
  return <span className="liquid-glass rounded-full px-3 py-1 text-xs text-white/80">{children}</span>;
}

function Extraction({ state }: { state: RunState }) {
  if (blocked(state, "extraction")) return <StateBlock state={state} stage="extraction" />;
  const ex = state.data.extraction!;
  const syn = state.data.synthesis;
  const repos = Array.isArray(ex.repos) ? ex.repos : [];

  const synByRepo: Record<string, string[]> = {};
  if (syn && Array.isArray(syn.evidence)) for (const e of syn.evidence) {
    const key = matchRepo(state, e.source_repo)?.name ?? e.source_repo;
    (synByRepo[key] = synByRepo[key] || []).push(e.claim);
  }
  const orphans = Object.keys(synByRepo).filter((k) => !repos.some((r) => r.name === k));
  const avatar = safeUrl(ex.avatar_url);
  const profile = safeUrl(ex.html_url);
  const synDone = state.status.synthesis === "done" && syn;

  return (
    <div>
      <div className="flex items-center gap-4 mb-8">
        {avatar && <img src={avatar} alt="" className="w-14 h-14 rounded-full ring-1 ring-white/20" />}
        <div className="min-w-0">
          <div className="text-white text-lg flex flex-wrap items-baseline gap-x-3">
            <span>{ex.name || ex.username}</span>
            {profile && <a href={profile} target="_blank" rel="noopener" className="text-white/40 text-sm hover:text-white">@{ex.username} ↗</a>}
          </div>
          {ex.bio && <p className="text-white/50 text-sm">{ex.bio}</p>}
          <p className="text-white/30 text-xs mt-1">{repos.length} repos analyzed</p>
        </div>
      </div>

      {/* Synthesis is its own pipeline stage (the model step), shown here with its own status and timer. */}
      <div className="rounded-2xl bg-white/[0.03] ring-1 ring-white/10 p-5 mb-8">
        <div className="flex justify-between gap-3 mb-4">
          <span className="label">Stage 2 · Synthesis · skills evidenced in code</span>
          {synDone && <span className="label">{secs(state.at.synthesis)}</span>}
        </div>
        {synDone
          ? <div className="flex flex-wrap gap-2">{(syn!.skills || []).map((s) => <Chip key={s}>{s}</Chip>)}</div>
          : <StateBlock state={state} stage="synthesis" />}
        {state.status.synthesis === "error" && (
          <p className="text-white/40 text-xs mt-3">Extraction succeeded: the repositories below are real GitHub data. The model step that interprets them failed.</p>
        )}
      </div>

      <p className="label mb-4">Repositories · what each one proves</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {repos.map((r) => {
          const url = safeUrl(r.html_url);
          const claims = synByRepo[r.name] || [];
          return (
            <div key={r.name} className="rounded-2xl bg-white/[0.03] ring-1 ring-white/10 p-5">
              <div className="flex items-baseline justify-between gap-3 mb-2">
                {url
                  ? <a href={url} target="_blank" rel="noopener" className="text-white font-medium hover:underline truncate">{r.name}</a>
                  : <span className="text-white font-medium truncate">{r.name}</span>}
                <span className="text-white/40 text-xs shrink-0">{[r.language, r.stars != null ? `★ ${fmtNum(r.stars)}` : null].filter(Boolean).join(" · ")}</span>
              </div>
              {r.description && <p className="text-white/50 text-sm mb-3">{r.description}</p>}
              {r.what_this_proves && (
                <p className="text-white/80 text-sm leading-relaxed"><span className="label block mb-1">What this proves</span>{r.what_this_proves}</p>
              )}
              {claims.length > 0 && (
                <ul className="mt-3 space-y-1.5 text-white/60 text-sm list-disc pl-4">{claims.map((c) => <li key={c}>{c}</li>)}</ul>
              )}
            </div>
          );
        })}
      </div>
      {orphans.length > 0 && (
        <p className="text-white/40 text-xs mt-4">
          Synthesis also cited: {orphans.map((o) => `${o} (${synByRepo[o].length})`).join(", ")}. These repos weren't in the extracted set.
        </p>
      )}
    </div>
  );
}

const CV_LABEL: Record<string, string> = {
  SUPPORTED: "✓ Verified by Gemini · used in pitch",
  PARTIALLY_SUPPORTED: "~ Partly supported · not used in pitch",
  UNSUPPORTED: "✗ Not supported · not used in pitch",
};
const CV_TONE: Record<string, string> = { SUPPORTED: "text-emerald-300", PARTIALLY_SUPPORTED: "text-amber-300", UNSUPPORTED: "text-red-300" };

function Thesis({ state }: { state: RunState }) {
  if (blocked(state, "thesis")) return <StateBlock state={state} stage="thesis" />;
  const t = state.data.thesis!;
  const ev = Array.isArray(t.supporting_evidence) ? t.supporting_evidence : [];
  const traced = ev.map((item) => ({ item, repo: matchRepo(state, item.source) }));
  const nRepo = traced.filter((x) => x.repo).length;

  // Per-claim audit for this exact thesis (cleared when the thesis is rewritten).
  const cvs: Record<number, { verdict: string; note?: string }> = {};
  for (const cv of state.data.verification?.claim_verdicts || []) cvs[cv.index] = cv;
  const nVerified = Object.values(cvs).filter((cv) => cv.verdict === "SUPPORTED").length;

  return (
    <div>
      {state.rewriting && (
        <p className="flex items-center gap-2 rounded-2xl bg-amber-400/[0.08] px-5 py-4 text-amber-100/90 text-sm mb-6">
          <LoaderCircle size={16} className="animate-spin shrink-0" />
          Gemini judged this thesis OVERREACHING. Rewriting it more conservatively, then auditing the rewrite…
        </p>
      )}
      {state.thesisNote && (
        <p className="rounded-2xl bg-amber-400/[0.08] px-5 py-4 text-amber-100/90 text-sm mb-6">
          {state.thesisNote}. Gemini judged the first thesis{" "}
          {state.originalThesis && <s>“{state.originalThesis.role_title}”</s>} as overreaching. The role below is the
          conservative rewrite, and it went back to Gemini for a second audit before any pitch was drafted.
        </p>
      )}
      {state.status.verification === "unresolved" && (
        <div className="rounded-2xl bg-red-500/[0.08] px-5 py-4 text-sm text-red-100/90 mb-6">
          <strong className="block mb-1 text-red-200">Not verified</strong>
          Treat this role as a hypothesis, not a result. {state.unresolved}
        </div>
      )}

      <h4 className="text-4xl md:text-6xl text-white tracking-tight leading-[1.05]" style={serif}>{t.role_title}</h4>
      {state.companyName && <p className="text-white/40 text-sm mt-2">at {state.companyName} · role thesis, not checked against current job postings</p>}
      <p className="text-white/70 text-base md:text-lg leading-relaxed mt-6 max-w-3xl">{t.justification}</p>

      <p className="label mt-10 mb-4">
        Evidence trail · {ev.length} claims · {nRepo} traced to repositories · {ev.length - nRepo} to company signals
        {Object.keys(cvs).length > 0 && ` · ${nVerified} individually verified`}
      </p>
      <ul className="divide-y divide-white/10">
        {traced.map(({ item, repo }, i) => {
          const url = repo && safeUrl(repo.html_url);
          const cv = cvs[i];
          return (
            <li key={i} className="py-4 grid grid-cols-1 md:grid-cols-[1fr_16rem] gap-3 md:gap-8">
              <div>
                <p className="text-white/90 text-sm md:text-base">{item.claim}</p>
                {cv && <p className={`text-xs mt-2 ${CV_TONE[cv.verdict] || "text-white/50"}`}>{CV_LABEL[cv.verdict] || cv.verdict}</p>}
                {cv?.note && <p className="text-white/40 text-xs mt-1">{cv.note}</p>}
              </div>
              <div className="text-sm">
                {repo ? (
                  <>
                    <span className="label block mb-1">Repository</span>
                    {url ? <a href={url} target="_blank" rel="noopener" className="text-white hover:underline">{repo.name} ↗</a> : <span className="text-white">{repo.name}</span>}
                    {repo.what_this_proves && <p className="text-white/40 text-xs mt-1 line-clamp-3">{repo.what_this_proves}</p>}
                  </>
                ) : (
                  <>
                    <span className="label block mb-1">Company signal{state.companyName ? ` · ${state.companyName}` : ""}</span>
                    <span className="text-white/70">{item.source || "(no source given)"}</span>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const VERDICT_TONE: Record<string, string> = {
  SUPPORTED: "text-emerald-200", PARTIALLY_SUPPORTED: "text-amber-200", OVERREACHING: "text-red-200", UNRESOLVED: "text-red-200",
};

function Verification({ state }: { state: RunState }) {
  const st = state.status.verification;
  const reaudit = !!state.firstVerification;
  if (st === "pending" && activeStage(state) === "verification") {
    return (
      <div className="text-center py-8">
        <p className="flex items-center justify-center gap-2 text-white/60 text-sm mb-4">
          <LoaderCircle size={16} className="animate-spin" />
          {reaudit ? "Gemini is auditing the conservative rewrite…" : PENDING_COPY.verification}
        </p>
        <p className="text-5xl md:text-7xl text-white/20 italic animate-pulse" style={serif}>{reaudit ? "Second audit pending" : "Verdict pending"}</p>
      </div>
    );
  }
  const unresolved = st === "unresolved";
  if (!unresolved && blocked(state, "verification")) return <StateBlock state={state} stage="verification" />;

  const v = state.data.verification || {};
  const verdict = unresolved ? "UNRESOLVED" : String(v.verdict || "").toUpperCase();
  const cvs = Array.isArray(v.claim_verdicts) ? v.claim_verdicts : [];
  const nOk = cvs.filter((cv) => cv.verdict === "SUPPORTED").length;
  const trail = reaudit
    ? [`Audit 1: ${String(state.firstVerification?.verdict || "").replace(/_/g, " ")}`, "conservative rewrite",
       `Audit 2: ${v.verdict ? v.verdict.replace(/_/g, " ") : "not completed"}`]
    : [];

  return (
    <div className="text-center">
      <div className="flex flex-wrap justify-center gap-x-6 gap-y-1 mb-6">
        <span className="label">Second-model audit · Google Gemini</span>
        {state.data.thesis && <span className="label">Thesis audited: {state.data.thesis.role_title}</span>}
      </div>
      <p className={`text-6xl md:text-8xl tracking-tight ${VERDICT_TONE[verdict] || "text-white"}`} style={serif}>
        {verdict.replace(/_/g, " ").toLowerCase() || "no verdict"}
      </p>
      <p className="text-white/70 text-base md:text-lg mt-4">{unresolved ? state.unresolved : VERDICT_MEANING[verdict]}</p>
      {trail.length > 0 && <p className="text-white/40 text-xs mt-4 font-mono">{trail.join("  →  ")}</p>}
      {cvs.length > 0
        ? <p className="text-white/40 text-xs mt-2">{nOk} of {cvs.length} claims individually SUPPORTED{unresolved ? "" : " · only those go into the pitch"}</p>
        : state.demo && !unresolved && <p className="text-white/40 text-xs mt-2">Recorded demo: per-claim audit isn't available in this recording</p>}
      {v.reason && <p className="text-white/60 text-sm leading-relaxed mt-6 max-w-2xl mx-auto text-left md:text-center">{v.reason}</p>}
      <p className="text-white/30 text-xs mt-8 max-w-xl mx-auto">
        Gemini sees only the thesis claims, the repo evidence and the company signals. It never sees the reasoning that
        produced the thesis, so it can't just agree with it.
      </p>
    </div>
  );
}

function Pitch({ state }: { state: RunState }) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copy, setCopy] = useState<{ msg: string; ok: boolean } | null>(null);
  if (blocked(state, "pitch")) return <StateBlock state={state} stage="pitch" />;
  const p = state.data.pitch!;
  const text = `${p.subject ? "Subject: " + p.subject + "\n\n" : ""}${p.outreach_message || ""}`;

  // Clipboard: async API → execCommand fallback → select for manual copy.
  const doCopy = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return setCopy({ msg: "Copied", ok: true });
      }
      throw new Error("Clipboard API unavailable");
    } catch {
      const ta = document.createElement("textarea");
      ta.readOnly = true;
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { /* ignore */ }
      ta.remove();
      if (ok) return setCopy({ msg: "Copied", ok: true });
      if (preRef.current) {
        const range = document.createRange();
        range.selectNodeContents(preRef.current);
        const sel = getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      setCopy({ msg: "Clipboard blocked. Message selected, press ⌘/Ctrl+C", ok: false });
    }
  };

  return (
    <div>
      {state.claimsUsed ? (
        <div className="mb-6">
          <p className="label mb-3">
            Built only from the {state.claimsUsed.length} claim{state.claimsUsed.length === 1 ? "" : "s"} Gemini marked SUPPORTED
          </p>
          <ul className="space-y-1.5 text-white/60 text-sm list-disc pl-4">
            {state.claimsUsed.map((c, i) => <li key={i}>{c.claim} ({c.source})</li>)}
          </ul>
        </div>
      ) : state.demo && (
        <p className="text-white/40 text-xs mb-4">Recorded demo: this pitch was drafted before per-claim filtering existed.</p>
      )}

      <div className="rounded-2xl bg-white/[0.03] ring-1 ring-white/10 overflow-hidden">
        {p.subject && (
          <div className="px-5 md:px-6 py-4 border-b border-white/10">
            <span className="label mr-3">Subject</span><span className="text-white">{p.subject}</span>
          </div>
        )}
        <pre ref={preRef} className="px-5 md:px-6 py-5 whitespace-pre-wrap font-sans text-white/80 text-sm md:text-base leading-relaxed">
          {p.outreach_message || ""}
        </pre>
        <div className="px-5 md:px-6 py-4 border-t border-white/10 flex flex-wrap items-center justify-between gap-4">
          {p.call_to_action ? <p className="text-white/50 text-sm">Ask: <b className="text-white font-medium">{p.call_to_action}</b></p> : <span />}
          <div className="flex items-center gap-3">
            <span aria-live="polite" className={`text-xs ${copy?.ok ? "text-emerald-300" : "text-amber-300"}`}>{copy?.msg}</span>
            <button type="button" onClick={doCopy} className="bg-white text-black rounded-full px-5 py-2 text-sm font-medium flex items-center gap-2">
              {copy?.ok ? <Check size={16} /> : <Copy size={16} />} Copy subject + message
            </button>
          </div>
        </div>
      </div>

      {Array.isArray(p.highlighted_claims) && p.highlighted_claims.length > 0 && (
        <div className="mt-6">
          <p className="label mb-3">Proof points the pitch leans on</p>
          <div className="flex flex-wrap gap-2">{p.highlighted_claims.map((c) => <Chip key={c}>{c}</Chip>)}</div>
        </div>
      )}
    </div>
  );
}
