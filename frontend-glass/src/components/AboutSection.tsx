import { useRef } from "react";
import { motion, useInView } from "framer-motion";

export default function AboutSection() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });
  const serif = { fontFamily: "'Instrument Serif', serif" };

  return (
    <section id="about" className="relative bg-black pt-32 md:pt-44 pb-10 md:pb-14 px-6 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(255,255,255,0.03)_0%,_transparent_70%)] pointer-events-none" />
      <div ref={ref} className="relative max-w-6xl mx-auto text-center">
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.6 }}
          className="text-white/40 text-sm tracking-widest uppercase mb-8"
        >
          About Unlisted
        </motion.p>
        <motion.h2
          initial={{ opacity: 0, y: 40 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.8, delay: 0.1 }}
          className="text-4xl md:text-6xl lg:text-7xl text-white leading-[1.1] tracking-tight"
        >
          Turning <em className="italic text-white/60" style={serif}>shipped code</em> into
          <br className="hidden md:block" />{" "}
          <em className="italic text-white/60" style={serif}>roles no job board has listed yet.</em>
        </motion.h2>
      </div>
    </section>
  );
}
