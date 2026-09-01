/**
 * BACKEND BOOT GUARD — v2.0 (single panel)
 * src/components/BackendBootGuard.jsx
 *
 * Everything in one card:
 *   ┌─────────────────────────────┐
 *   │  ⚡ Scalp Terminal   1m 12s │  ← header row
 *   │  ████████████░░░░░░░░  72%  │  ← progress bar
 *   │ ─────────────────────────── │
 *   │         🧠                  │  ← quote (crossfade)
 *   │    The Strategist           │
 *   │  "Prediction is ego…"       │
 *   │   ● ● ● ─── ● ● ● ●        │  ← dots
 *   │ ─────────────────────────── │
 *   │  ● Initialising engine…     │  ← status line
 *   └─────────────────────────────┘
 *
 * v1.0 saved as BackendBootGuard.v1.0.jsx
 */

import { useEffect, useState, useRef } from "react";
import { getStatus } from "../api";

const POLL_INTERVAL_MS  = 4000;
const EXPECTED_BOOT_MS  = 95000;
const FADE_DURATION_MS  = 800;
const QUOTE_INTERVAL_MS = 8500;

const ENERGY = {
  risk:       { accent: "#ef4444", glow: "rgba(239,68,68,0.2)",   label: "Risk"      },
  patience:   { accent: "#3b82f6", glow: "rgba(59,130,246,0.2)",  label: "Patience"  },
  execution:  { accent: "#10b981", glow: "rgba(16,185,129,0.2)",  label: "Execution" },
  discipline: { accent: "#f59e0b", glow: "rgba(245,158,11,0.2)",  label: "Discipline"},
  mindset:    { accent: "#a855f7", glow: "rgba(168,85,247,0.2)",  label: "Mindset"   },
};

const C = {
  bg:         "#020817",
  card:       "#0b1221",
  border:     "#1e293b",
  borderLit:  "#243048",
  text:       "#f8fafc",
  textSoft:   "#cbd5e1",
  textMuted:  "#64748b",
  textDim:    "#1e293b",
  success:    "#10b981",
  divider:    "#1a2540",
};

/* ── 100 quotes ── */
const QUOTES = [
  { icon: "🧠", archetype: "The Strategist",         line: "Prediction is ego. Preparation is edge.",                    energy: "mindset"    },
  { icon: "🧘", archetype: "The Monk",                line: "The market tests discipline, not intelligence.",             energy: "patience"   },
  { icon: "🎯", archetype: "The Sniper",              line: "Wait longer. Strike faster.",                                energy: "execution"  },
  { icon: "🦅", archetype: "The Hawk",                line: "If the setup isn't clean, don't fly.",                      energy: "discipline" },
  { icon: "🔥", archetype: "The Risk Tamer",          line: "Your stop-loss is survival, not weakness.",                 energy: "risk"       },
  { icon: "🏗",  archetype: "The Builder",             line: "Consistency builds accounts. Excitement destroys them.",    energy: "discipline" },
  { icon: "🧊", archetype: "The Ice Trader",          line: "Emotions spike. Professionals don't.",                      energy: "mindset"    },
  { icon: "🕰",  archetype: "The Timekeeper",          line: "Patience compounds faster than capital.",                   energy: "patience"   },
  { icon: "🧮", archetype: "The Quant",               line: "If you can't measure it, you can't trust it.",              energy: "mindset"    },
  { icon: "🛡",  archetype: "The Guardian",            line: "Protect capital first. Grow it second.",                   energy: "risk"       },
  { icon: "⚖",  archetype: "The Judge",               line: "Let price speak. Silence your bias.",                       energy: "mindset"    },
  { icon: "🌊", archetype: "The Wave Rider",          line: "Ride momentum. Don't argue with it.",                      energy: "execution"  },
  { icon: "🧱", archetype: "The Wall",                line: "No edge? No trade.",                                        energy: "discipline" },
  { icon: "🧨", archetype: "The Controlled Fire",     line: "Volatility is fuel. Discipline is containment.",           energy: "risk"       },
  { icon: "🔍", archetype: "The Analyst",             line: "Details create advantage.",                                 energy: "mindset"    },
  { icon: "🦉", archetype: "The Observer",            line: "Most losses begin with overconfidence.",                    energy: "mindset"    },
  { icon: "🏹", archetype: "The Archer",              line: "A clear target beats constant action.",                     energy: "execution"  },
  { icon: "🧠", archetype: "The Edge Seeker",         line: "Small advantages compound quietly.",                        energy: "mindset"    },
  { icon: "🚦", archetype: "The Signal Keeper",       line: "Green means go. Anything else means wait.",                energy: "discipline" },
  { icon: "🧩", archetype: "The Pattern Reader",      line: "The chart whispers before it shouts.",                     energy: "patience"   },
  { icon: "🚀", archetype: "The Accelerator",         line: "Scale what works. Cut what doesn't.",                      energy: "execution"  },
  { icon: "🎛",  archetype: "The Controller",          line: "Risk defines the trade before profit does.",               energy: "risk"       },
  { icon: "🌌", archetype: "The Macro Mind",          line: "Zoom out before zooming in.",                              energy: "mindset"    },
  { icon: "🪨", archetype: "The Stoic",               line: "Losses are tuition. Ego makes them expensive.",            energy: "mindset"    },
  { icon: "📐", archetype: "The Architect",           line: "Structure before execution.",                               energy: "discipline" },
  { icon: "🔁", archetype: "The Compounding Mind",    line: "Repetition creates mastery.",                              energy: "discipline" },
  { icon: "🐺", archetype: "The Lone Wolf",           line: "Follow your system, not the crowd.",                       energy: "mindset"    },
  { icon: "⚡", archetype: "The Executor",            line: "Execution speed matters only after clarity.",              energy: "execution"  },
  { icon: "🔐", archetype: "The Vault",               line: "Capital locked is capital alive.",                         energy: "risk"       },
  { icon: "🌡",  archetype: "The Thermostat",          line: "Adjust exposure, not emotions.",                          energy: "risk"       },
  { icon: "🧭", archetype: "The Navigator",           line: "Trend is direction. Noise is distraction.",                energy: "mindset"    },
  { icon: "🧬", archetype: "The Scientist",           line: "Test. Validate. Deploy.",                                   energy: "mindset"    },
  { icon: "🪓", archetype: "The Cutter",              line: "Cut losses faster than you cut profits.",                  energy: "execution"  },
  { icon: "🧠", archetype: "The Calm Mind",           line: "Stillness outperforms excitement.",                        energy: "patience"   },
  { icon: "📊", archetype: "The Statistician",        line: "One trade means nothing. A system means everything.",     energy: "mindset"    },
  { icon: "🛠",  archetype: "The Craftsman",           line: "Precision beats intensity.",                               energy: "execution"  },
  { icon: "🧘", archetype: "The Detached One",        line: "Detach from outcomes. Attach to process.",                energy: "patience"   },
  { icon: "🦾", archetype: "The Machine",             line: "Rules don't hesitate.",                                    energy: "discipline" },
  { icon: "🎚",  archetype: "The Balance Keeper",      line: "Overtrading is disguised impatience.",                    energy: "patience"   },
  { icon: "🌒", archetype: "The Contrarian",          line: "When everyone agrees, risk increases.",                   energy: "mindset"    },
  { icon: "🏔",  archetype: "The Mountain",            line: "Steady beats spectacular.",                               energy: "patience"   },
  { icon: "🪙", archetype: "The Capitalist",          line: "Your account is inventory. Don't waste stock.",           energy: "risk"       },
  { icon: "🎯", archetype: "The Sharpshooter",        line: "High probability or no participation.",                   energy: "execution"  },
  { icon: "📈", archetype: "The Momentum Reader",     line: "Strength attracts strength.",                             energy: "execution"  },
  { icon: "🕶",  archetype: "The Silent Operator",     line: "Profit quietly. Improve constantly.",                     energy: "discipline" },
  { icon: "🛑", archetype: "The Gatekeeper",          line: "No confirmation, no commitment.",                         energy: "discipline" },
  { icon: "🧨", archetype: "The Risk Realist",        line: "The market owes nothing.",                                energy: "risk"       },
  { icon: "🪶", archetype: "The Lightweight",         line: "Lower size. Higher clarity.",                             energy: "risk"       },
  { icon: "🧿", archetype: "The Visionary",           line: "Trade the plan. Not the candle.",                         energy: "mindset"    },
  { icon: "🧊", archetype: "The Final Word",          line: "Flat is a position.",                                     energy: "patience"   },
  { icon: "🧭", archetype: "The Cartographer",        line: "Map risk before chasing reward.",                         energy: "risk"       },
  { icon: "🧊", archetype: "The Emotionless",         line: "Price moves. You don't have to.",                         energy: "mindset"    },
  { icon: "🏹", archetype: "The Precisionist",        line: "Accuracy beats frequency.",                               energy: "execution"  },
  { icon: "🌪",  archetype: "The Storm Walker",        line: "Chaos rewards the prepared.",                             energy: "mindset"    },
  { icon: "🔄", archetype: "The Process Devotee",     line: "Follow the system. Ignore the noise.",                    energy: "discipline" },
  { icon: "🔍", archetype: "The Clarity Seeker",      line: "If it's confusing, it's not a setup.",                   energy: "discipline" },
  { icon: "⚙️", archetype: "The Engineer",             line: "Refine rules. Reduce randomness.",                       energy: "mindset"    },
  { icon: "🛑", archetype: "The Discipline Master",   line: "Skipping bad trades is a win.",                          energy: "discipline" },
  { icon: "🧠", archetype: "The Probability Thinker", line: "Think in outcomes, not certainties.",                     energy: "mindset"    },
  { icon: "🌅", archetype: "The Long View",           line: "Today's trade is part of a lifetime curve.",             energy: "patience"   },
  { icon: "⚖",  archetype: "The Equilibrium",         line: "Balance exposure before expanding ambition.",            energy: "risk"       },
  { icon: "🎛",  archetype: "The Risk Architect",      line: "Design exits before dreaming entries.",                  energy: "risk"       },
  { icon: "🧘", archetype: "The Centered Mind",       line: "Stillness protects capital.",                            energy: "patience"   },
  { icon: "🔐", archetype: "The Protector",           line: "Drawdown control is hidden alpha.",                      energy: "risk"       },
  { icon: "🧪", archetype: "The Tester",              line: "Backtest confidence. Forward test discipline.",          energy: "mindset"    },
  { icon: "🌊", archetype: "The Flow Reader",         line: "Align with flow. Don't fight currents.",                 energy: "execution"  },
  { icon: "🧱", archetype: "The Structure Keeper",    line: "Strong structure survives volatility.",                  energy: "discipline" },
  { icon: "🐢", archetype: "The Patient One",         line: "Slow decisions, steady growth.",                         energy: "patience"   },
  { icon: "🕶",  archetype: "The Observer",            line: "React less. Observe more.",                              energy: "patience"   },
  { icon: "📊", archetype: "The System Keeper",       line: "A rule ignored becomes a loss invited.",                 energy: "discipline" },
  { icon: "🚦", archetype: "The Traffic Controller",  line: "Red means pause. Not panic.",                           energy: "discipline" },
  { icon: "🔥", archetype: "The Controlled Flame",    line: "Intensity without control burns accounts.",              energy: "risk"       },
  { icon: "🧠", archetype: "The Strategic Mind",      line: "Edge is built, not found.",                             energy: "mindset"    },
  { icon: "📐", archetype: "The Geometry Reader",     line: "Symmetry reveals opportunity.",                         energy: "execution"  },
  { icon: "🛡",  archetype: "The Defender",            line: "Survival precedes success.",                            energy: "risk"       },
  { icon: "🧮", archetype: "The Data Loyalist",       line: "Numbers don't argue. Ego does.",                        energy: "mindset"    },
  { icon: "🧘", archetype: "The Detacher",            line: "Your trade is not your identity.",                      energy: "mindset"    },
  { icon: "🐺", archetype: "The Independent",         line: "Consensus is comfort. Edge is discomfort.",             energy: "mindset"    },
  { icon: "⚡", archetype: "The Minimalist",          line: "Fewer trades. Cleaner decisions.",                      energy: "discipline" },
  { icon: "🎯", archetype: "The Executioner",         line: "Precision entry. Ruthless exit.",                       energy: "execution"  },
  { icon: "🌌", archetype: "The Bigger Picture",      line: "Trends outlive opinions.",                              energy: "mindset"    },
  { icon: "🪶", archetype: "The Light Touch",         line: "Size appropriately. Sleep peacefully.",                 energy: "risk"       },
  { icon: "🧱", archetype: "The Foundation Builder",  line: "Strong base. Stable growth.",                          energy: "discipline" },
  { icon: "📈", archetype: "The Growth Keeper",       line: "Compounding rewards restraint.",                        energy: "patience"   },
  { icon: "🧊", archetype: "The Composed",            line: "Calm traders scale.",                                   energy: "patience"   },
  { icon: "🔁", archetype: "The Habit Maker",         line: "Daily discipline beats occasional brilliance.",        energy: "discipline" },
  { icon: "🛠",  archetype: "The Refiner",             line: "Improve one rule at a time.",                          energy: "discipline" },
  { icon: "🌒", archetype: "The Silent Accumulator",  line: "Small gains stack quietly.",                           energy: "patience"   },
  { icon: "🕰",  archetype: "The Clock Watcher",       line: "Timing matters. Urgency doesn't.",                    energy: "patience"   },
  { icon: "🎛",  archetype: "The Exposure Manager",    line: "Control size. Control outcome.",                       energy: "risk"       },
  { icon: "🧿", archetype: "The Focused Eye",         line: "One setup. Full attention.",                           energy: "execution"  },
  { icon: "🪓", archetype: "The Decisive Cutter",     line: "Cut quickly. Regret slowly.",                         energy: "execution"  },
  { icon: "🧠", archetype: "The Rationalist",         line: "Emotion inflates risk.",                               energy: "mindset"    },
  { icon: "📊", archetype: "The Edge Protector",      line: "Your edge survives only with discipline.",             energy: "discipline" },
  { icon: "🛑", archetype: "The Gate Guardian",       line: "No confirmation. No commitment.",                      energy: "discipline" },
  { icon: "🔐", archetype: "The Risk Vault",          line: "Capital preserved is opportunity preserved.",          energy: "risk"       },
  { icon: "⚖",  archetype: "The Balance Master",      line: "Win rate means nothing without risk control.",        energy: "risk"       },
  { icon: "🏔",  archetype: "The Endurer",             line: "Endurance builds equity.",                            energy: "patience"   },
  { icon: "🌬", archetype: "The Breath Keeper",       line: "Pause before pressing buy.",                          energy: "patience"   },
  { icon: "🧊", archetype: "The Final Discipline",    line: "Flat is powerful.",                                   energy: "discipline" },

  // 101–110
  { icon: "🧠", archetype: "The Cold Operator",        line: "Trade the plan. Not the pulse.",                      energy: "discipline" },
  { icon: "🎯", archetype: "The Precision Sniper",     line: "Wait longer. Strike cleaner.",                        energy: "execution"  },
  { icon: "🧊", archetype: "The Ice Mind",             line: "Emotion widens spreads.",                             energy: "mindset"    },
  { icon: "📉", archetype: "The Drawdown Realist",     line: "Losses teach faster than wins.",                      energy: "mindset"    },
  { icon: "🛡",  archetype: "The Risk Guardian",        line: "Capital is inventory. Protect it.",                  energy: "risk"       },
  { icon: "⚡", archetype: "The Quick Exit",           line: "Small loss. Immediate clarity.",                      energy: "execution"  },
  { icon: "🔄", archetype: "The Reset Button",         line: "Every candle is a new decision.",                     energy: "mindset"    },
  { icon: "🧩", archetype: "The Pattern Solver",       line: "Structure repeats. Impulse fades.",                   energy: "discipline" },
  { icon: "📏", archetype: "The Risk Measurer",        line: "Size defines survival.",                              energy: "risk"       },
  { icon: "🚪", archetype: "The Door Keeper",          line: "No edge. No entry.",                                  energy: "discipline" },

  // 111–120
  { icon: "🧘", archetype: "The Detached Mind",        line: "Detach from outcome. Execute process.",               energy: "patience"   },
  { icon: "🔬", archetype: "The Analyzer",             line: "Review ruthlessly. Improve quietly.",                 energy: "mindset"    },
  { icon: "🧠", archetype: "The Logical Brain",        line: "Certainty is expensive.",                             energy: "mindset"    },
  { icon: "🪶", archetype: "The Light Risker",         line: "Trade light. Scale smart.",                           energy: "risk"       },
  { icon: "🧱", archetype: "The Foundation Thinker",   line: "Consistency compounds.",                              energy: "discipline" },
  { icon: "🎛",  archetype: "The System Guardian",      line: "Rules reduce regret.",                               energy: "discipline" },
  { icon: "🐺", archetype: "The Lone Thinker",         line: "Independent thought beats crowd comfort.",            energy: "mindset"    },
  { icon: "📊", archetype: "The Data Devotee",         line: "Evidence over excitement.",                           energy: "mindset"    },
  { icon: "🕰",  archetype: "The Patient Hunter",       line: "Patience finds premium.",                            energy: "patience"   },
  { icon: "🧊", archetype: "The Still Trader",         line: "Silence strengthens decisions.",                      energy: "patience"   },

  // 121–130
  { icon: "🔥", archetype: "The Controlled Aggressor", line: "Attack only when risk is defined.",                   energy: "risk"       },
  { icon: "🛑", archetype: "The Stop-Loss Believer",   line: "Stops are tuition, not punishment.",                  energy: "risk"       },
  { icon: "📈", archetype: "The Edge Builder",         line: "Edge evolves. Ego resists.",                          energy: "mindset"    },
  { icon: "🧠", archetype: "The Probability Master",   line: "Expect nothing. Prepare for everything.",             energy: "mindset"    },
  { icon: "⚖",  archetype: "The Balance Keeper",       line: "Risk small. Think big.",                             energy: "risk"       },
  { icon: "🎯", archetype: "The Closer",               line: "Execution defines professionals.",                    energy: "execution"  },
  { icon: "🪓", archetype: "The Cutter",               line: "Cut losers. Let logic stay.",                         energy: "execution"  },
  { icon: "🧮", archetype: "The Statistician",         line: "One trade means nothing.",                            energy: "mindset"    },
  { icon: "🛡",  archetype: "The Survivor",             line: "Longevity beats intensity.",                         energy: "risk"       },
  { icon: "🌊", archetype: "The Flow Aligner",         line: "Align with trend, not opinion.",                      energy: "execution"  },

  // 131–140
  { icon: "🔁", archetype: "The Habit Engineer",       line: "Daily review builds mastery.",                        energy: "discipline" },
  { icon: "🧊", archetype: "The Calm Executor",        line: "Calm entries scale better.",                          energy: "patience"   },
  { icon: "📊", archetype: "The Structure Reader",     line: "Clarity hides in structure.",                         energy: "mindset"    },
  { icon: "🕶",  archetype: "The Invisible Trader",     line: "Trade quietly. Grow steadily.",                      energy: "discipline" },
  { icon: "🔬", archetype: "The Optimizer",            line: "Refine risk before refining entries.",                energy: "risk"       },
  { icon: "🎛",  archetype: "The Exposure Controller",  line: "Control size. Control emotion.",                     energy: "risk"       },
  { icon: "🧱", archetype: "The Brick Layer",          line: "One disciplined trade at a time.",                    energy: "discipline" },
  { icon: "🌌", archetype: "The Macro Thinker",        line: "Zoom out before zooming in.",                         energy: "mindset"    },
  { icon: "🐢", archetype: "The Long Player",          line: "Slow growth sustains wealth.",                        energy: "patience"   },
  { icon: "🧠", archetype: "The Rational Mind",        line: "Feelings don't calculate.",                           energy: "mindset"    },

  // 141–150
  { icon: "⚔",  archetype: "The Market Warrior",       line: "Discipline is your armor.",                          energy: "discipline" },
  { icon: "🛡",  archetype: "The Capital Defender",     line: "Defend first. Attack later.",                        energy: "risk"       },
  { icon: "🎯", archetype: "The Target Setter",        line: "Define exits before entries.",                        energy: "execution"  },
  { icon: "🔐", archetype: "The Vault Keeper",         line: "Preserve power for prime setups.",                    energy: "risk"       },
  { icon: "🧊", archetype: "The Composed Fighter",     line: "Pressure reveals preparation.",                      energy: "mindset"    },
  { icon: "🧠", archetype: "The Strategic Operator",   line: "Plan in peace. Execute in chaos.",                    energy: "discipline" },
  { icon: "🔥", archetype: "The Controlled Risker",    line: "Bold with structure. Never blind.",                   energy: "risk"       },
  { icon: "📉", archetype: "The Drawdown Controller",  line: "Reduce risk when uncertain.",                         energy: "risk"       },
  { icon: "🧭", archetype: "The Direction Seeker",     line: "Trend is your compass.",                              energy: "mindset"    },
  { icon: "🏆", archetype: "The Elite Rule",           line: "Professional traders survive first.",                 energy: "discipline" },
];

function makeShuffledOrder() {
  const order = Array.from({ length: QUOTES.length }, (_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

/* ── 25 Market Trivia facts ── */
const TRIVIA = [
  { fact: "NIFTY fell over 12% in a single session on March 23, 2020.",          lesson: "Extreme volatility creates both opportunity and danger." },
  { fact: "NIFTY dropped nearly 38% from Jan to March 2020 during COVID.",       lesson: "Bear markets move faster than bull markets." },
  { fact: "Sensex crashed 13.2% on March 23, 2020 — its worst single-day fall.", lesson: "Panic accelerates downside momentum." },
  { fact: "In 2008, NIFTY fell over 50% during the global financial crisis.",    lesson: "Systemic risk resets everything." },
  { fact: "Lehman Brothers collapsed on Sept 15, 2008.",                         lesson: "Liquidity risk is invisible — until it isn't." },
  { fact: "On Black Monday (Oct 19, 1987), the Dow fell 22% in one day.",       lesson: "Tail events are rare — but devastating." },
  { fact: "Harshad Mehta's 1992 scam triggered a massive market crash in India.",lesson: "Leverage without transparency ends badly." },
  { fact: "The 2013 Taper Tantrum caused sharp FII outflows from India.",        lesson: "Global liquidity drives emerging markets." },
  { fact: "NIFTY rallied over 100% from March 2020 lows within 18 months.",     lesson: "Capitulation often marks generational bottoms." },
  { fact: "Infosys corrected over 30% in 2017 after governance concerns.",       lesson: "Even quality stocks face sentiment shocks." },
  { fact: "The 2000 Dot-com crash erased trillions globally.",                   lesson: "Hype eventually meets valuation." },
  { fact: "Oil futures briefly went negative in April 2020.",                    lesson: "Markets can behave irrationally under stress." },
  { fact: "Yes Bank fell over 80% during its 2020 crisis.",                      lesson: "Concentration risk destroys portfolios." },
  { fact: "In 2016, demonetization triggered sharp volatility in Indian equities.",lesson: "Policy shocks reprice risk instantly." },
  { fact: "The 1997 Asian Financial Crisis collapsed multiple currencies.",       lesson: "Currency risk amplifies equity risk." },
  { fact: "NIFTY has delivered ~12% CAGR since inception.",                      lesson: "Time in market beats timing the market." },
  { fact: "Markets fell during the Kargil War in 1999 — but recovered quickly.", lesson: "Geopolitical fear often fades faster than expected." },
  { fact: "Reliance Industries gained over 1000% during the early 2000s boom.", lesson: "Compounding works best with strong fundamentals." },
  { fact: "The 2022 global rate hike cycle triggered sharp corrections worldwide.",lesson: "Interest rates drive asset valuations." },
  { fact: "Circuit breakers were triggered multiple times in Indian markets in 2020.", lesson: "Volatility protection exists for a reason." },
  { fact: "The 2018 IL&FS crisis shook India's NBFC sector.",                   lesson: "Credit risk spreads contagiously." },
  { fact: "Tesla gained over 700% in 2020.",                                     lesson: "Momentum can outperform logic — temporarily." },
  { fact: "GameStop surged over 1,500% in early 2021.",                          lesson: "Crowd behaviour can overpower fundamentals." },
  { fact: "NIFTY corrected ~20% in 2022 during global tightening.",             lesson: "Even strong trends need resets." },
  { fact: "Markets have historically recovered from every major crash.",          lesson: "Fear is temporary. Discipline is permanent." },
];

function makeShuffledTrivia() {
  const order = Array.from({ length: TRIVIA.length }, (_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/* ── Candlestick background ── */
/* ── Live scrolling candlestick chart (canvas, price-walk) ── */
function CandlestickBg({ color }) {
  const canvasRef  = useRef(null);
  const stateRef   = useRef(null);
  const colorRef   = useRef(color); // color changes don't restart the animation

  // Keep colorRef current on every render without triggering effect
  colorRef.current = color;

  function rgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function genCandle(lastClose, H) {
    const mid   = H * 0.5;
    const drift = (mid - lastClose) * 0.08;   // gentle pull to centre
    // Larger move range: 18-32% of canvas height per candle
    const move  = (Math.random() - 0.48) * H * 0.28 + drift;
    const open  = lastClose;
    const close = Math.min(Math.max(open + move, H * 0.10), H * 0.90);
    const bodyH = Math.abs(close - open);
    // Wicks: 25-80% of body height on each side
    const wickT = bodyH * (0.25 + Math.random() * 0.6);
    const wickB = bodyH * (0.20 + Math.random() * 0.55);
    const high  = Math.min(close, open) - wickT;
    const low   = Math.max(close, open) + wickB;
    // canvas Y: smaller = higher price
    const bullish = close < open;
    return { open, close, high, low, bullish };
  }

  // Animation runs once — never restarts when color changes
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const W = canvas.width  = canvas.offsetWidth;
    const H = canvas.height = canvas.offsetHeight;

    const CANDLE_W   = 11;   // body width px
    const GAP        = 6;    // gap between candles
    const STEP       = CANDLE_W + GAP;
    const SCROLL_SPD = 0.6;  // px per frame (~36px/s at 60fps)

    // Seed candles to fill canvas + right buffer
    const needed = Math.ceil(W / STEP) + 8;
    const candles = [];
    let price = H * 0.5;
    for (let i = 0; i < needed; i++) {
      const c = genCandle(price, H);
      price = c.close;
      candles.push(c);
    }

    stateRef.current = { candles, offset: 0, lastClose: price };

    let raf;

    function draw() {
      const st  = stateRef.current;
      const col = colorRef.current;   // always latest color, no restart
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, W, H);

      st.offset += SCROLL_SPD;
      if (st.offset >= STEP) {
        st.offset -= STEP;
        st.candles.shift();
        const c = genCandle(st.lastClose, H);
        st.lastClose = c.close;
        st.candles.push(c);
      }

      st.candles.forEach((c, i) => {
        const x = i * STEP - st.offset + 4;

        const bodyTop = Math.min(c.open, c.close);
        const bodyBot = Math.max(c.open, c.close);
        const bodyH   = Math.max(bodyBot - bodyTop, 2);

        const bullColor = rgba(col, 0.80);
        const bearColor = rgba(col, 0.50);
        const cc = c.bullish ? bullColor : bearColor;

        // Wick
        ctx.beginPath();
        ctx.strokeStyle = cc;
        ctx.lineWidth   = 1.5;
        ctx.moveTo(x + CANDLE_W / 2, c.high);
        ctx.lineTo(x + CANDLE_W / 2, c.low);
        ctx.stroke();

        // Body — bullish: solid fill; bearish: outline + dim fill
        if (c.bullish) {
          ctx.fillStyle = bullColor;
          ctx.fillRect(x, bodyTop, CANDLE_W, bodyH);
        } else {
          ctx.fillStyle = rgba(col, 0.18);
          ctx.fillRect(x, bodyTop, CANDLE_W, bodyH);
          ctx.strokeStyle = bearColor;
          ctx.lineWidth   = 1.5;
          ctx.strokeRect(x, bodyTop, CANDLE_W, bodyH);
        }
      });

      raf = requestAnimationFrame(draw);
    }

    draw();
    return () => cancelAnimationFrame(raf);
  }, []); // ← empty deps: init once, never restart

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "absolute", inset: 0,
        width: "100%", height: "100%",
        opacity: 0.22,
        pointerEvents: "none",
      }}
    />
  );
}


/* ── Quote crossfade section ── */
function QuoteSection({ quote, visible, isToday, energyColor }) {
  return (
    <div style={{
      opacity:   visible ? 1 : 0,
      transform: visible ? "translateY(0) scale(1)" : "translateY(6px) scale(0.98)",
      transition: "opacity 0.6s ease, transform 0.6s ease",
      display: "flex", flexDirection: "column", alignItems: "center",
      textAlign: "center", gap: 10,
      padding: "4px 0",
      position: "relative", zIndex: 1,
    }}>

      {/* Today's archetype badge */}
      {isToday && (
        <div style={{
          fontSize: 9, fontWeight: 700, letterSpacing: "1.6px",
          textTransform: "uppercase",
          color: energyColor,
          background: energyColor + "1a",
          border: `1px solid ${energyColor}40`,
          padding: "3px 12px", borderRadius: 20,
          animation: "todayPulse 1.6s ease-in-out infinite",
        }}>
          Today's Archetype
        </div>
      )}

      {/* Icon */}
      <div style={{
        fontSize: 34, lineHeight: 1,
        filter: `drop-shadow(0 0 18px ${energyColor}60)`,
        transform: isToday ? "scale(1.1)" : "scale(1)",
        transition: "transform 0.5s ease",
      }}>
        {quote.icon}
      </div>

      {/* Category + name */}
      <div>
        <div style={{
          fontSize: 9, fontWeight: 600, letterSpacing: "1.4px",
          textTransform: "uppercase", color: energyColor, opacity: 0.7,
          marginBottom: 3,
        }}>
          {ENERGY[quote.energy].label}
        </div>
        <div style={{
          fontSize: 12, fontWeight: 700, letterSpacing: "1.2px",
          textTransform: "uppercase", color: energyColor,
        }}>
          {quote.archetype}
        </div>
      </div>

      {/* Line */}
      <div style={{
        fontSize: 15, fontWeight: 400, lineHeight: 1.7,
        color: "#cbd5e1", fontStyle: "italic", maxWidth: 310,
      }}>
        &ldquo;{quote.line}&rdquo;
      </div>
    </div>
  );
}

/* ── Dots ── */
function Dots({ idx, color, total = 8 }) {
  const pos = idx % total;
  return (
    <div style={{ display: "flex", justifyContent: "center", gap: 5 }}>
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} style={{
          width: i === pos ? 20 : 5, height: 3, borderRadius: 2,
          background: i === pos ? color : C.border,
          boxShadow: i === pos ? `0 0 6px ${color}50` : "none",
          transition: "width 0.4s ease, background 0.4s ease",
        }} />
      ))}
    </div>
  );
}

/* ── Main ── */
export default function BackendBootGuard({ children }) {
  const [phase,        setPhase]        = useState("booting");
  const [elapsed,      setElapsed]      = useState(0);
  const [attempt,      setAttempt]      = useState(0);
  const [statusMsg,    setStatusMsg]    = useState("Initialising engine…");
  const [quoteIdx,     setQuoteIdx]     = useState(0);
  const [quoteVisible, setQuoteVisible] = useState(true);
  const [isToday,      setIsToday]      = useState(false);
  const [triviaIdx,    setTriviaIdx]    = useState(0);
  const [triviaVis,    setTriviaVis]    = useState(true);

  const shuffledOrder  = useRef(makeShuffledOrder());
  const shuffledTrivia = useRef(makeShuffledTrivia());
  const startTime      = useRef(Date.now());
  const pollTimer      = useRef(null);
  const tickTimer      = useRef(null);
  const fadeTimer      = useRef(null);
  const quoteTimer     = useRef(null);
  const todayTimer     = useRef(null);
  const triviaTimer    = useRef(null);

  useEffect(() => {
    tickTimer.current = setInterval(() => setElapsed(Date.now() - startTime.current), 1000);
    return () => clearInterval(tickTimer.current);
  }, []);

  useEffect(() => {
    if (phase !== "booting") return;
    const msgs = [
      "Initialising engine…", "Loading strategy configs…",
      "Connecting to market feed…", "Warming up trade state…", "Almost ready…",
    ];
    setStatusMsg(msgs[Math.min(Math.floor(elapsed / 18000), msgs.length - 1)]);
  }, [elapsed, phase]);

  useEffect(() => {
    if (phase !== "booting") return;
    quoteTimer.current = setInterval(() => {
      setQuoteVisible(false);
      setTimeout(() => {
        setQuoteIdx(p => (p + 1) % QUOTES.length);
        setQuoteVisible(true);
      }, 650);
    }, QUOTE_INTERVAL_MS);
    return () => clearInterval(quoteTimer.current);
  }, [phase]);

  // Trivia rotates every 15s — independent of quote cycle
  useEffect(() => {
    if (phase !== "booting") return;
    triviaTimer.current = setInterval(() => {
      setTriviaVis(false);
      setTimeout(() => {
        setTriviaIdx(p => (p + 1) % TRIVIA.length);
        setTriviaVis(true);
      }, 500);
    }, 15000);
    return () => clearInterval(triviaTimer.current);
  }, [phase]);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const s = await getStatus();
        if (!cancelled && s) markReady();
      } catch {
        if (!cancelled) setAttempt(n => n + 1);
      }
    }
    check();
    pollTimer.current = setInterval(check, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(pollTimer.current); };
  }, []);

  function markReady() {
    clearInterval(pollTimer.current);
    clearInterval(tickTimer.current);
    clearInterval(quoteTimer.current);
    clearInterval(triviaTimer.current);
    setElapsed(Date.now() - startTime.current);
    setPhase("ready");
    setIsToday(true);
    todayTimer.current = setTimeout(() => {
      setIsToday(false);
      fadeTimer.current = setTimeout(() => setPhase("done"), FADE_DURATION_MS + 150);
    }, 1500);
  }

  useEffect(() => () => { clearTimeout(fadeTimer.current); clearTimeout(todayTimer.current); }, []);

  if (phase === "done") return <>{children}</>;

  const pct          = phase === "ready" ? 100 : Math.min(95, (elapsed / EXPECTED_BOOT_MS) * 100);
  const isReady      = phase === "ready";
  const quote        = QUOTES[shuffledOrder.current[quoteIdx]];
  const trivia       = TRIVIA[shuffledTrivia.current[triviaIdx]];
  const ec           = ENERGY[quote.energy].accent;

  return (
    <>
      <div style={{ visibility: "hidden", pointerEvents: "none" }}>{children}</div>

      {/* Full-screen overlay */}
      <div style={{
        position: "fixed", inset: 0, zIndex: 9999,
        background: C.bg,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
        opacity:       isReady && !isToday ? 0 : 1,
        transition:    isReady && !isToday ? `opacity ${FADE_DURATION_MS}ms ease` : "none",
        pointerEvents: isReady && !isToday ? "none" : "all",
      }}>

        {/* ── Single card ── */}
        <div style={{
          width: 520,
          background: C.card,
          border: `1px solid ${ec}35`,
          borderRadius: 16,
          overflow: "hidden",
          boxShadow: `0 24px 64px rgba(0,0,0,0.7), 0 0 0 1px ${ec}12`,
          transition: "border-color 0.8s ease, box-shadow 0.8s ease",
          display: "flex", flexDirection: "column",
        }}>

          {/* ── TOP SECTION: header + progress ── */}
          <div style={{ padding: "16px 28px 14px", position: "relative" }}>

            {/* Radial glow behind header */}
            <div style={{
              position: "absolute", top: 0, left: 0, right: 0, height: "100%",
              background: `radial-gradient(ellipse at 50% 0%, ${ec}0e 0%, transparent 70%)`,
              pointerEvents: "none",
              transition: "background 0.8s ease",
            }} />

            {/* Header row */}
            <div style={{
              display: "flex", alignItems: "center",
              justifyContent: "space-between", marginBottom: 16,
              position: "relative", zIndex: 1,
            }}>
              {/* Logo */}
              <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                <span style={{ fontSize: 18 }}>⚡</span>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.text, letterSpacing: "0.2px" }}>
                    Scalp Terminal
                  </div>
                  <div style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.6px", textTransform: "uppercase", marginTop: 1 }}>
                    {isReady ? "Backend ready" : "Starting backend…"}
                  </div>
                </div>
              </div>

              {/* Elapsed + checks */}
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: C.text, fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
                  {formatElapsed(elapsed)}
                </div>
                <div style={{ fontSize: 9, color: C.textMuted, marginTop: 3 }}>
                  {attempt} check{attempt !== 1 ? "s" : ""} · ~90s
                </div>
              </div>
            </div>

            {/* Progress bar */}
            <div style={{ position: "relative", zIndex: 1 }}>
              <div style={{ width: "100%", height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
                <div style={{
                  height: "100%", width: `${pct}%`,
                  background: isReady ? "#10b981" : ec,
                  boxShadow: `0 0 10px ${isReady ? "#10b98160" : ec + "60"}`,
                  borderRadius: 2,
                  transition: isReady ? "width 0.3s ease, background 0.5s ease" : "width 1s linear",
                }} />
              </div>
              {/* Pct label */}
              <div style={{
                position: "absolute", right: 0, top: -16,
                fontSize: 9, fontWeight: 600,
                color: isReady ? "#10b981" : ec,
                fontVariantNumeric: "tabular-nums",
                transition: "color 0.5s ease",
              }}>
                {Math.round(pct)}%
              </div>
            </div>
          </div>

          {/* ── DIVIDER ── */}
          <div style={{ height: 1, background: `linear-gradient(90deg, transparent, ${ec}30, transparent)`, transition: "background 0.8s ease" }} />

          {/* ── MIDDLE SECTION: quote + candles ── */}
          <div style={{
            padding: "18px 32px 14px",
            position: "relative", overflow: "hidden",
            minHeight: 190,
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            gap: 12,
          }}>
            {/* Candlestick bg */}
            <CandlestickBg color={ec} />

            {/* Subtle energy tint */}
            <div style={{
              position: "absolute", inset: 0,
              background: `radial-gradient(ellipse at 50% 60%, ${ec}07 0%, transparent 70%)`,
              pointerEvents: "none", transition: "background 0.8s ease",
            }} />

            <QuoteSection quote={quote} visible={quoteVisible} isToday={isToday} energyColor={ec} />
            <Dots idx={quoteIdx} color={ec} />
          </div>

          {/* ── DIVIDER ── */}
          <div style={{ height: 1, background: `linear-gradient(90deg, transparent, ${ec}20, transparent)`, transition: "background 0.8s ease" }} />

          {/* ── BOTTOM SECTION: status line ── */}
          <div style={{ padding: "11px 28px 13px", display: "flex", alignItems: "center", gap: 9 }}>
            {/* Pulse dot */}
            <span style={{
              display: "inline-block", width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
              background: isReady ? "#10b981" : ec,
              boxShadow: isReady ? "0 0 8px #10b98180" : `0 0 8px ${ec}80`,
              animation: isReady ? "none" : "bootPulse 1.4s ease-in-out infinite",
              transition: "background 0.5s ease",
            }} />
            <span style={{
              fontSize: 12, fontWeight: 500,
              color: isReady ? "#10b981" : C.textMuted,
              transition: "color 0.5s ease",
            }}>
              {isReady ? "Connected — loading app…" : statusMsg}
            </span>

            {/* Spacer + energy pill */}
            <div style={{ flex: 1 }} />
            <span style={{
              fontSize: 9, fontWeight: 600, letterSpacing: "0.8px",
              textTransform: "uppercase",
              color: ec, background: ec + "18",
              padding: "2px 8px", borderRadius: 10,
              border: `1px solid ${ec}30`,
              transition: "all 0.8s ease",
            }}>
              {ENERGY[quote.energy].label}
            </span>
          </div>

          {/* ── Trivia strip — replaces hint, appears after 10s ── */}
          {!isReady && elapsed > 10000 && (
            <div style={{
              borderTop: `1px solid ${C.border}`,
              padding: "10px 20px 11px",
              display: "flex", gap: 10, alignItems: "flex-start",
              opacity: triviaVis ? 1 : 0,
              transition: "opacity 0.5s ease",
            }}>
              {/* Icon */}
              <span style={{ fontSize: 13, flexShrink: 0, marginTop: 1 }}>📊</span>

              {/* Text */}
              <div>
                <div style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "1.2px",
                  textTransform: "uppercase", color: ec, marginBottom: 3,
                }}>
                  Did you know?
                </div>
                <div style={{ fontSize: 11, color: C.textSoft, lineHeight: 1.55, marginBottom: 3 }}>
                  {trivia.fact}
                </div>
                <div style={{ fontSize: 10, color: C.textMuted, lineHeight: 1.4, fontStyle: "italic" }}>
                  {trivia.lesson}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes bootPulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.85); }
        }
        @keyframes todayPulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.55; }
        }
      `}</style>
    </>
  );
}