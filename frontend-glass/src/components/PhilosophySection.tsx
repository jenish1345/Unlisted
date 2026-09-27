import { useRef } from "react";
import { motion, useInView } from "framer-motion";

const VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260307_083826_e938b29f-a43a-41ec-a153-3d4730578ab8.mp4";

export default function PhilosophySection() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <section id="audit" className="bg-black py-28 md:py-40 px-6 overflow-hidden">
      <div ref={ref} className="max-w-6xl mx-auto">
        <motion.h2
          initial={{ opacity: 0, y: 40 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.8 }}
          className="text-5xl md:text-7xl lg:text-8xl text-white tracking-tight mb-16 md:mb-24"
        >
          Evidence <em className="italic text-white/40" style={{ fontFamily: "'Instrument Serif', serif" }}>x</em> Audit
        </motion.h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-12">
          <motion.div
            initial={{ opacity: 0, x: -40 }}
            animate={inView ? { opacity: 1, x: 0 } : {}}
            transition={{ duration: 0.8, delay: 0.1 }}
            className="rounded-3xl overflow-hidden aspect-[4/3]"
          >
            <video src={VIDEO} className="w-full h-full object-cover" muted autoPlay loop playsInline preload="auto" />
          </motion.div>

          <motion.div
            initial={{ opacity: 0, x: 40 }}
            animate={inView ? { opacity: 1, x: 0 } : {}}
            transition={{ duration: 0.8, delay: 0.2 }}
            className="flex flex-col justify-center gap-10"
          >
            <div>
              <p className="text-white/40 text-xs tracking-widest uppercase mb-4">Two models, no echo</p>
              <p className="text-white/70 text-base md:text-lg leading-relaxed">
                One model writes the role thesis. Google Gemini audits it in a separate call, and it sees only the claims,
                the repository evidence and the company signals, never the reasoning behind them. It can't just agree.
              </p>
            </div>
            <div className="w-full h-px bg-white/10" />
            <div>
              <p className="text-white/40 text-xs tracking-widest uppercase mb-4">Only what survives</p>
              <p className="text-white/70 text-base md:text-lg leading-relaxed">
                A thesis judged overreaching is rewritten more conservatively and audited again. The outreach pitch is
                drafted only from claims Gemini marks supported. If no role holds up, no pitch is written.
              </p>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
