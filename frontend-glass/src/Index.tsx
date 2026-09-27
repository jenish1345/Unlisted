import { FormEvent, useEffect, useRef, useState } from "react";
import { ArrowRight, GitBranch, Globe, Mail, ScanSearch } from "lucide-react";
import AboutSection from "./components/AboutSection";
import FeaturedVideoSection from "./components/FeaturedVideoSection";
import PhilosophySection from "./components/PhilosophySection";
import ServicesSection from "./components/ServicesSection";
import ResultsSection from "./components/ResultsSection";
import { API, Fallback, useCompanies, usePipeline } from "./lib/pipeline";

const HERO_VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260405_074625_a81f018a-956b-43fb-9aee-4d1508e30e6a.mp4";

// Fade video opacity from its current value to `to` over `ms` with requestAnimationFrame.
function useVideoFade(ref: React.RefObject<HTMLVideoElement>) {
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    let raf = 0;
    let fadingOut = false;
    let timer: number | undefined;

    const fade = (to: number, ms = 500) => {
      cancelAnimationFrame(raf);
      const from = parseFloat(v.style.opacity || "0");
      const t0 = performance.now();
      const step = (now: number) => {
        const k = Math.min((now - t0) / ms, 1);
        v.style.opacity = String(from + (to - from) * k);
        if (k < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    };

    const onCanPlay = () => {
      v.play().catch(() => {});
      fade(1);
    };
    const onTimeUpdate = () => {
      if (!fadingOut && v.duration && v.duration - v.currentTime <= 0.55) {
        fadingOut = true;
        fade(0);
      }
    };
    const onEnded = () => {
      cancelAnimationFrame(raf);
      v.style.opacity = "0";
      timer = window.setTimeout(() => {
        v.currentTime = 0;
        v.play().catch(() => {});
        fadingOut = false;
        fade(1);
      }, 100);
    };

    v.addEventListener("canplay", onCanPlay, { once: true });
    v.addEventListener("timeupdate", onTimeUpdate);
    v.addEventListener("ended", onEnded);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      v.removeEventListener("canplay", onCanPlay);
      v.removeEventListener("timeupdate", onTimeUpdate);
      v.removeEventListener("ended", onEnded);
    };
  }, [ref]);
}

export default function Index() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useVideoFade(videoRef);

  const { companies, error: companiesError } = useCompanies();
  const { state, start } = usePipeline(companies);
  const [username, setUsername] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (companies.length && !companyId) setCompanyId(companies[0].id);
  }, [companies, companyId]);

  const running = !!state && !state.finished;
  const company = companies.find((c) => c.id === companyId);

  const launch = (demo: boolean, fallbackFrom: Fallback | null = null) => {
    setFormError(null);
    start({ demo, username: demo ? "" : username.trim().replace(/^@/, ""), companyId: demo ? "" : companyId, fallbackFrom });
    requestAnimationFrame(() => document.getElementById("results")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    // The backend silently falls back to demo playback when either field is empty, so guard it here.
    if (!username.trim()) {
      setFormError("Enter a GitHub username, or replay the recorded demo.");
      inputRef.current?.focus();
      return;
    }
    if (!companyId) {
      setFormError(companiesError || "Companies are still loading.");
      return;
    }
    launch(false);
  };

  const focusRun = () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(() => inputRef.current?.focus(), 400);
  };

  return (
    <div className="bg-black">
      <section className="min-h-screen overflow-hidden relative flex flex-col">
        <video
          ref={videoRef}
          src={HERO_VIDEO}
          className="absolute inset-0 w-full h-full object-cover object-bottom"
          style={{ opacity: 0 }}
          muted
          autoPlay
          playsInline
          preload="auto"
        />

        {/* Navbar */}
        <nav className="relative z-20 px-6 py-6">
          <div className="liquid-glass rounded-full max-w-5xl mx-auto px-6 py-3 flex items-center justify-between">
            <div className="flex items-center">
              <a href="#" className="flex items-center gap-2">
                <ScanSearch size={24} className="text-white" />
                <span className="text-white font-semibold text-lg">Unlisted</span>
              </a>
              <div className="hidden md:flex items-center gap-8 ml-8">
                <a href="#about" className="text-white/80 hover:text-white text-sm font-medium">How it works</a>
                <a href="#pipeline" className="text-white/80 hover:text-white text-sm font-medium">Pipeline</a>
                <a href="#audit" className="text-white/80 hover:text-white text-sm font-medium">Audit</a>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <button type="button" onClick={() => launch(true)} className="hidden sm:inline text-white text-sm font-medium">
                Demo
              </button>
              <button type="button" onClick={focusRun} className="liquid-glass rounded-full px-6 py-2 text-white text-sm font-medium">
                Run analysis
              </button>
            </div>
          </div>
        </nav>

        {/* Hero content */}
        <div className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12 text-center -translate-y-[20%]">
          <h1
            className="text-6xl sm:text-7xl md:text-8xl lg:text-9xl text-white tracking-tight whitespace-nowrap mb-10"
            style={{ fontFamily: "'Instrument Serif', serif" }}
          >
            Code <em className="italic">proves</em> it.
          </h1>

          <form onSubmit={onSubmit} className="max-w-xl w-full mb-6" noValidate>
            <div className="liquid-glass rounded-full pl-6 pr-2 py-2 flex items-center gap-3">
              <span className="text-white/40">@</span>
              <input
                ref={inputRef}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="GitHub username"
                autoComplete="off"
                spellCheck={false}
                aria-label="GitHub username"
                className="flex-1 min-w-0 bg-transparent outline-none text-white placeholder:text-white/40"
              />
              <select
                value={companyId}
                onChange={(e) => setCompanyId(e.target.value)}
                disabled={!companies.length}
                aria-label="Target company"
                className="glass-select bg-transparent outline-none text-white/80 text-sm max-w-[9rem] sm:max-w-[11rem] truncate border-l border-white/15 pl-3 cursor-pointer disabled:text-white/30"
              >
                {!companies.length && <option>{companiesError ? "Unavailable" : "Loading…"}</option>}
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
              <button
                type="submit"
                disabled={running}
                aria-label="Find the role"
                className="bg-white rounded-full p-3 text-black shrink-0 disabled:opacity-50"
              >
                <ArrowRight size={20} />
              </button>
            </div>
          </form>

          {(formError || companiesError) && (
            <p className="text-red-300/90 text-sm mb-4 max-w-xl" role="alert">{formError || companiesError}</p>
          )}

          <p className="text-white text-sm leading-relaxed px-4 max-w-xl mb-8">
            {company && !formError && !companiesError
              ? `${company.name}: ${company.description}. `
              : ""}
            We read what someone has actually shipped on GitHub, propose the role a company's engineering signals point to,
            have Gemini audit every claim, and draft outreach from only the claims that pass.
          </p>

          <button
            type="button"
            onClick={() => launch(true)}
            title="Streams the pre-recorded run in demo_cache.json. No GitHub, OpenAI or Gemini calls are made."
            className="liquid-glass rounded-full px-8 py-3 text-white text-sm font-medium hover:bg-white/5 transition-colors"
          >
            Replay the recorded demo
          </button>
        </div>

        {/* Social / utility icons */}
        <div className="relative z-10 flex justify-center gap-4 pb-12">
          <a href="https://github.com" target="_blank" rel="noopener" aria-label="GitHub"
            className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
            <GitBranch size={20} />
          </a>
          <a href={`${API}/docs`} target="_blank" rel="noopener" aria-label="API docs"
            className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
            <Globe size={20} />
          </a>
          <a href="#pipeline" aria-label="How outreach is drafted"
            className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
            <Mail size={20} />
          </a>
        </div>
      </section>

      {state && <ResultsSection state={state} onFallback={(f) => launch(true, f)} onRerun={focusRun} />}

      <AboutSection />
      <FeaturedVideoSection />
      <PhilosophySection />
      <ServicesSection onRun={focusRun} />

      <footer className="bg-black px-6 pb-12 text-center text-white/30 text-xs">
        Backend: {API || location.origin} · POST /api/analyze (server-sent events) · GET /api/companies
      </footer>
    </div>
  );
}
