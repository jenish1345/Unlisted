import { useCallback, useEffect, useRef, useState } from "react";

// Port of the pipeline client in frontend/index.html: same endpoints, same SSE event handling.

// Same origin by default (the Vite dev proxy or the FastAPI app serving the build); ?api= overrides.
const params = new URLSearchParams(location.search);
export const API = (params.get("api") || import.meta.env.VITE_API || "").replace(/\/$/, "");

export const STAGES = ["extraction", "synthesis", "thesis", "verification", "pitch"] as const;
export type Stage = (typeof STAGES)[number];
export type StageStatus = "pending" | "done" | "error" | "unresolved" | "skipped";

export const STAGE_LABEL: Record<Stage, string> = {
  extraction: "Extract", synthesis: "Synthesize", thesis: "Thesis", verification: "Verify", pitch: "Pitch",
};
export const PENDING_COPY: Record<Stage, string> = {
  extraction: "Reading public repositories and READMEs from GitHub…",
  synthesis: "Turning the repos into concrete, repo-cited evidence…",
  thesis: "Cross-referencing the evidence against the company's hiring signals…",
  verification: "Gemini is auditing each claim against the raw evidence…",
  pitch: "Drafting outreach from the claims that survived…",
};
export const VERDICT_MEANING: Record<string, string> = {
  SUPPORTED: "The evidence backs this role.",
  PARTIALLY_SUPPORTED: "Some claims hold up. Others need more proof.",
  OVERREACHING: "The thesis claims more than the code shows.",
};

export type Company = { id: string; name: string; description: string };
export type Repo = { name: string; html_url?: string; description?: string; language?: string; stars?: number; what_this_proves?: string };
export type Claim = { claim: string; source: string };
export type ClaimVerdict = { index: number; verdict: string; note?: string };
/* eslint-disable @typescript-eslint/no-explicit-any */
export type RunData = {
  extraction?: { username: string; name?: string; bio?: string; avatar_url?: string; html_url?: string; repos?: Repo[] };
  synthesis?: { skills?: string[]; evidence?: { claim: string; source_repo: string }[] };
  thesis?: { role_title: string; justification: string; supporting_evidence?: Claim[] };
  verification?: { verdict?: string; reason?: string; claim_verdicts?: ClaimVerdict[] };
  pitch?: { subject?: string; outreach_message?: string; call_to_action?: string; highlighted_claims?: string[] };
};

export type Fallback = { stage: string; message: string; username: string; company: string | null };

export type RunState = {
  demo: boolean;
  username: string;
  companyName: string | null;
  fallbackFrom: Fallback | null;
  rewriting: boolean;            // first audit said OVERREACHING; backend is rewriting the thesis
  firstVerification: RunData["verification"] | null;
  unresolved: string | null;     // message when no role survived verification
  claimsUsed: Claim[] | null;    // claims the pitch was built from (live runs only)
  status: Record<Stage, StageStatus>;
  data: RunData;
  error: Partial<Record<Stage, string>>;
  at: Partial<Record<Stage, number>>;
  thesisNote: string | null;
  originalThesis: RunData["thesis"] | null;
  started: number;
  finished: boolean;
  elapsed: number | null;
};

function freshState(demo: boolean, username: string, companyName: string | null, fallbackFrom: Fallback | null): RunState {
  return {
    demo, username, companyName, fallbackFrom,
    rewriting: false, firstVerification: null, unresolved: null, claimsUsed: null,
    status: Object.fromEntries(STAGES.map((s) => [s, "pending"])) as Record<Stage, StageStatus>,
    data: {}, error: {}, at: {}, thesisNote: null, originalThesis: null,
    started: performance.now(), finished: false, elapsed: null,
  };
}

// Normalise FastAPI error bodies: {detail: "..."} or {detail: [{msg, loc}, ...]} (422).
async function readHttpError(res: Response) {
  let text = "";
  try { text = await res.text(); } catch { /* ignore */ }
  try {
    const j = JSON.parse(text);
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
  } catch { /* not JSON */ }
  return `HTTP ${res.status}${text ? ": " + text.slice(0, 200) : ""}`;
}

// Parse a text/event-stream body read via fetch (EventSource can't POST).
async function readSSE(body: ReadableStream<Uint8Array>, onEvent: (e: any) => void) {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    if (done) buf += dec.decode() + "\n\n";
    buf = buf.replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
      if (!data) continue;
      try { onEvent(JSON.parse(data)); } catch (err) { console.warn("Unparseable SSE event", data, err); }
    }
    if (done) return;
  }
}

const skipAfter = (st: RunState, stage: Stage) =>
  STAGES.slice(STAGES.indexOf(stage) + 1).forEach((s) => { if (st.status[s] === "pending") st.status[s] = "skipped"; });

function applyEvent(st: RunState, evt: any) {
  const stage = evt && evt.stage;
  if (!STAGES.includes(stage)) { console.warn("Unknown stage", evt); return; }
  const s = stage as Stage;
  st.at[s] = performance.now() - st.started;

  if (evt.status === "error") {
    st.status[s] = "error";
    st.error[s] = evt.message || "This stage failed without a message.";
    // Backend halts on the first error: everything after it will never arrive.
    skipAfter(st, s);
  } else if (evt.status === "unresolved") {
    // No role survived the audit. The backend stops here and never drafts a pitch.
    st.rewriting = false;
    st.status[s] = "unresolved";
    st.unresolved = evt.message || "No role survived verification.";
    if (evt.data) (st.data as any)[s] = evt.data;
    skipAfter(st, s);
  } else if (evt.status === "done") {
    if (s === "thesis" && st.data.thesis && evt.note) {
      // OVERREACHING → backend re-emits a conservative thesis, then audits it again.
      st.originalThesis = st.data.thesis;
      st.thesisNote = evt.note;
      st.rewriting = false;
      st.firstVerification = st.data.verification || null;
      delete st.data.verification;
      delete st.at.verification;
      st.status.verification = "pending";
    }
    if (s === "verification") st.rewriting = evt.round === 1 && evt.data && evt.data.verdict === "OVERREACHING";
    if (s === "pitch") st.claimsUsed = Array.isArray(evt.claims_used) ? evt.claims_used : null;
    st.status[s] = "done";
    (st.data as any)[s] = evt.data || {};
  }
}

export const activeStage = (st: RunState): Stage | undefined =>
  st.rewriting ? "thesis" : STAGES.find((s) => st.status[s] === "pending");

export const failedStage = (st: RunState): Stage | undefined =>
  STAGES.find((s) => st.status[s] === "error" || st.status[s] === "unresolved");

// Map a thesis source string to a repo from extraction, mirroring the backend's source_matches_repo():
// the exact repo name, or the repo name as a whole token (e.g. "fastapi (README)").
export function matchRepo(st: RunState, source: string): Repo | null {
  const repos = st.data.extraction?.repos || [];
  const s = String(source || "").trim().toLowerCase();
  if (!s) return null;
  const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const hits = repos.filter((r) => {
    const n = r.name.toLowerCase();
    return s === n || new RegExp(`(^|[^\\w.-])${esc(n)}($|[^\\w.-])`).test(s);
  });
  return hits.sort((a, b) => b.name.length - a.name.length)[0] || null;
}

export const safeUrl = (u?: string) => (typeof u === "string" && /^https:\/\//i.test(u) ? u : undefined);
export const fmtNum = (n?: number) =>
  typeof n === "number" ? (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n)) : "";

export function useCompanies() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/api/companies`);
        if (!res.ok) throw new Error(await readHttpError(res));
        const list = await res.json();
        if (!Array.isArray(list) || !list.length) throw new Error("The backend returned no companies.");
        setCompanies(list);
      } catch (e: any) {
        setError(`Couldn't load companies from ${API || location.origin}/api/companies. ${e.message}. Is the backend running?`);
      }
    })();
  }, []);
  return { companies, error };
}

export function usePipeline(companies: Company[]) {
  const stRef = useRef<RunState | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  // State is mutated in place (like the original) and published as a fresh shallow copy per event.
  const [state, setState] = useState<RunState | null>(null);
  const publish = () => stRef.current && setState({ ...stRef.current });

  const start = useCallback(async (opts: { demo: boolean; username: string; companyId: string; fallbackFrom?: Fallback | null }) => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    const company = companies.find((c) => c.id === opts.companyId);
    const st = freshState(opts.demo, opts.username, opts.demo ? null : company?.name ?? null, opts.fallbackFrom ?? null);
    stRef.current = st;
    publish();

    const finish = (transportError: string | null) => {
      st.finished = true;
      st.rewriting = false;
      st.elapsed = performance.now() - st.started;
      const firstOpen = STAGES.find((s) => st.status[s] === "pending");
      if (firstOpen) {
        st.status[firstOpen] = "error";
        st.error[firstOpen] = transportError
          ? `Connection to the backend failed: ${transportError}`
          : "The stream ended before this stage reported a result.";
        skipAfter(st, firstOpen);
      }
      publish();
    };

    try {
      const res = await fetch(`${API}/api/analyze${opts.demo ? "?demo=true" : ""}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ github_username: opts.username, company_id: opts.companyId, demo: opts.demo }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error(await readHttpError(res));
      await readSSE(res.body, (evt) => { if (stRef.current === st) { applyEvent(st, evt); publish(); } });
      if (stRef.current === st) finish(null);
    } catch (e: any) {
      if (e.name === "AbortError" || stRef.current !== st) return;
      finish(e.message || String(e));
    }
  }, [companies]);

  useEffect(() => () => ctrlRef.current?.abort(), []);
  return { state, start };
}
