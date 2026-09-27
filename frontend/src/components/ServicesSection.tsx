import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { ArrowUpRight } from "lucide-react";

const CARDS = [
  {
    video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260314_131748_f2ca2a28-fed7-44c8-b9a9-bd9acdd5ec31.mp4",
    tag: "Stages 1–3 · Extract, Synthesize, Thesis",
    title: "Evidence & Role Thesis",
    desc: "We pull their most recent public repositories and READMEs, work out what each one proves, and cross-reference that evidence against where the company is actually investing, with every claim cited.",
  },
  {
    video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260324_151826_c7218672-6e92-402c-9e45-f1e0f454bdc4.mp4",
    tag: "Stages 4–5 · Verify, Pitch",
    title: "Independent Audit & Outreach",
    desc: "Gemini scores each claim as supported, partly supported or overreaching. The cold pitch is built only from what passes, ready to copy with its subject line.",
  },
];

export default function ServicesSection({ onRun }: { onRun: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <section id="pipeline" className="relative bg-black py-28 md:py-40 px-6 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(255,255,255,0.02)_0%,_transparent_60%)] pointer-events-none" />
      <div ref={ref} className="relative max-w-6xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.7 }}
          className="flex items-end justify-between mb-12 md:mb-16"
        >
          <h2 className="text-3xl md:text-5xl text-white tracking-tight">What it does</h2>
          <p className="hidden md:block text-white/40 text-sm">The five-stage pipeline</p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8">
          {CARDS.map((c, i) => (
            <motion.button
              key={c.title}
              type="button"
              onClick={onRun}
              initial={{ opacity: 0, y: 50 }}
              animate={inView ? { opacity: 1, y: 0 } : {}}
              transition={{ duration: 0.8, delay: 0.15 * i }}
              className="liquid-glass rounded-3xl overflow-hidden group text-left"
            >
              <div className="relative aspect-video overflow-hidden">
                <video src={c.video} className="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105"
                  muted autoPlay loop playsInline preload="auto" />
                <div className="absolute inset-0 bg-gradient-to-t from-black/40 to-transparent" />
              </div>
              <div className="p-6 md:p-8">
                <div className="flex items-center justify-between mb-4 gap-4">
                  <span className="uppercase tracking-widest text-white/40 text-xs">{c.tag}</span>
                  <span className="liquid-glass rounded-full p-2 text-white shrink-0"><ArrowUpRight size={16} /></span>
                </div>
                <h3 className="text-white text-xl md:text-2xl mb-3 tracking-tight">{c.title}</h3>
                <p className="text-white/50 text-sm leading-relaxed">{c.desc}</p>
              </div>
            </motion.button>
          ))}
        </div>
      </div>
    </section>
  );
}
