import { useEffect, useRef, useState, useMemo } from "react";
import {
  GitBranch,
  Clipboard,
  Check,
  ChevronRight,
} from "lucide-react";
import {
  cladeRows,
  dataSources,
  fmt,
  metrics,
  qualityRows,
  scalingRows,
  speedup,
  reduction,
  type CladeRow,
} from "./data";

/* ─── Scroll reveal hook ─── */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.classList.add("visible");
          observer.unobserve(el);
        }
      },
      { threshold: 0.12 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return ref;
}

/* ─── Animated counter hook ─── */
function useCounter(target: number, decimals = 1, duration = 1200) {
  const [val, setVal] = useState(0);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          obs.unobserve(el);
          const start = performance.now();
          const tick = (now: number) => {
            const t = Math.min((now - start) / duration, 1);
            const ease = 1 - Math.pow(1 - t, 3);
            setVal(ease * target);
            if (t < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }
      },
      { threshold: 0.5 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [target, duration]);
  return { ref, display: val.toFixed(decimals) };
}

/* ─── Copy button ─── */
function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* noop */ }
  };
  return (
    <button className={`copy-btn${copied ? " copied" : ""}`} onClick={copy} type="button" title="复制">
      {copied ? <Check size={14} /> : <Clipboard size={14} />}
    </button>
  );
}

/* ━━━━━━━━━━━━━ NAVBAR ━━━━━━━━━━━━━ */
const sections = [
  { id: "about", label: "概述" },
  { id: "demo", label: "界面" },
  { id: "performance", label: "性能" },
  { id: "quality", label: "精度" },
  { id: "quickstart", label: "快速开始" },
  { id: "workflow", label: "工作流" },
  { id: "citation", label: "引用" },
];

function Navbar() {
  return (
    <nav className="site-nav">
      <div className="container">
        <a href="#top" className="nav-brand">
          <span className="nav-brand-name">CAX</span>
          <span className="nav-version">v0.6</span>
        </a>
        <div className="nav-links">
          {sections.map((s) => (
            <a key={s.id} href={`#${s.id}`}>{s.label}</a>
          ))}
        </div>
        <div className="nav-actions">
          <a href="https://github.com/malabz/Cax" target="_blank" rel="noopener noreferrer">
            <GitBranch size={16} />
            <span>GitHub</span>
          </a>
        </div>
      </div>
    </nav>
  );
}

/* ━━━━━━━━━━━━━ HERO ━━━━━━━━━━━━━ */
const heroCli = `# 解析 Cactus 规划拓扑
$ cax --from-file prepare_output.txt

# 一条命令自动缓存、筛选并执行
$ cax auto --seqfile examples/evolverPrimates.txt --mash-threshold 0.02

# 打开交互式树界面
$ cax`;

function Hero() {
  const c1 = useCounter(metrics.maxCladeSpeedup, 1);
  const c2 = useCounter(metrics.totalReduction, 1);
  const c3 = useCounter(metrics.ramaxQuality.precision * 100, 3);

  return (
    <section className="hero" id="top">
      <div className="container">
        <div className="hero-text">
          <h1>Cactus-RaMAx</h1>
          <p className="hero-subtitle">
            A phylogenetic-tree-aware hybrid alignment planning framework.
            <br />
            CAX 是面向大规模多基因组渐进比对的混合调度框架。基于系统发育树拓扑与 Mash
            遗传距离，在高同源分支自动部署高速比对器 RaMAx，在复杂分支保留 Progressive
            Cactus 精度，实现大幅提速且不损失比对质量。
          </p>
          <div className="hero-actions">
            <a href="#quickstart" className="btn-primary">
              快速开始 <ChevronRight size={15} />
            </a>
            <a href="#performance" className="btn-secondary">查看性能基准</a>
          </div>
          <div className="hero-metrics">
            <div className="hero-metric">
              <span className="number" ref={c1.ref}>{c1.display}×</span>
              <span className="label">最大分支加速</span>
            </div>
            <span className="hero-metrics-separator">·</span>
            <div className="hero-metric">
              <span className="number" ref={c2.ref}>{c2.display}%</span>
              <span className="label">累计耗时节省</span>
            </div>
            <span className="hero-metrics-separator">·</span>
            <div className="hero-metric">
              <span className="number" ref={c3.ref}>{c3.display}%</span>
              <span className="label">Precision 精度</span>
            </div>
          </div>
        </div>
        <div className="hero-code">
          <pre><code>{heroCli}</code></pre>
          <CopyBtn text={heroCli.replace(/^#.*\n?/gm, "").replace(/^\$ /gm, "").trim()} />
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ ABOUT ━━━━━━━━━━━━━ */
function About() {
  const r = useReveal<HTMLDivElement>();
  return (
    <section className="about-section" id="about">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">Project Overview</span>
          <h2>为什么需要 CAX？</h2>
        </div>
        <div className="about-content">
          <div className="about-prose">
            <p>
              <strong>问题：</strong>渐进式多基因组比对（Progressive Genome Alignment）是比较基因组学的基础工具，
              但随着测序基因组数量的快速增长，传统工具如 Progressive Cactus 面临严重的算力瓶颈——
              每增加一个基因组，累计计算耗时攀升约 <strong>{fmt(metrics.cactusSlope, 1)}h</strong>。
            </p>
            <p>
              <strong>方案：</strong>CAX 将 Cactus 的静态线性规划重构为动态的发育树感知依赖图谱。
              通过 Mash 遗传距离量化分支间的序列同源度，在进化距离近的子树自动部署高速对齐器 RaMAx，
              在需要复杂祖先重建的远缘分支保留 Cactus 精度。
            </p>
            <p>
              <strong>结果：</strong>在 12 个哺乳动物进化分支（6 科级 + 6 属级）的验证中，
              CAX 实现最高 <strong>{fmt(metrics.maxCladeSpeedup, 1)}×</strong> 单分支加速、
              累计节省 <strong>{fmt(metrics.totalReduction, 1)}%</strong> 计算时间，
              同时保持 F1-Score <strong>{fmt(metrics.f1Retained, 1)}%</strong> 的基线留存率。
            </p>
          </div>
          <div className="pipeline-flow">
            <div className="pipeline-step">
              <div className="pipeline-step-label">Input</div>
              <div className="pipeline-step-desc">seqFile.txt + tree.nwk</div>
            </div>
            <div className="pipeline-arrow">→</div>
            <div className="pipeline-step">
              <div className="pipeline-step-label">CAX Planning</div>
              <div className="pipeline-step-desc">Mash 距离 → 混合调度</div>
            </div>
            <div className="pipeline-arrow">→</div>
            <div className="pipeline-step">
              <div className="pipeline-step-label">Output</div>
              <div className="pipeline-step-desc">output.hal 比对数据库</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function TerminalDemo() {
  const r = useReveal<HTMLDivElement>();

  return (
    <section id="demo">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">Interactive Console</span>
          <h2>树感知规划界面</h2>
          <p>CAX 将复杂的系统发育依赖关系还原为可交互的分支画布，直接在终端中审查、替换并运行混合比对计划。</p>
        </div>
        <div className="ui-demo-panel">
          <div className="ui-demo-media">
            <img
              src={`${import.meta.env.BASE_URL}cax-ui-demo.gif`}
              alt="CAX interactive terminal UI showing tree navigation, RaMAx toggles, run settings, and command execution"
              loading="lazy"
            />
          </div>
          <div className="ui-demo-notes">
            <div>
              <strong>Tree canvas</strong>
              <span>用真实拓扑展示 Cactus / RaMAx 状态，避免在未命名祖先节点上丢失上下文。</span>
            </div>
            <div>
              <strong>Run settings</strong>
              <span>统一设置线程、日志与执行模式，运行前可复查完整依赖树。</span>
            </div>
            <div>
              <strong>Execution view</strong>
              <span>命令启动后持续显示当前步骤、剩余任务与资源状态。</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ PERFORMANCE ━━━━━━━━━━━━━ */
function ScalingChart() {
  const w = 780, h = 340;
  const m = { t: 24, r: 24, b: 48, l: 56 };
  const iw = w - m.l - m.r, ih = h - m.t - m.b;
  const xMin = 4, xMax = 7, yMax = 270;
  const x = (v: number) => m.l + ((v - xMin) / (xMax - xMin)) * iw;
  const y = (v: number) => m.t + ih - (v / yMax) * ih;
  const line = (key: "cactusTime" | "ramaxTime") =>
    scalingRows.map((r, i) => `${i ? "L" : "M"}${x(r.genomes)},${y(r[key])}`).join(" ");

  return (
    <div className="chart-container" style={{ marginBottom: 32 }}>
      <div className="chart-legend">
        <div className="chart-legend-item">
          <span className="chart-legend-swatch baseline" />
          <span>Cactus</span>
        </div>
        <div className="chart-legend-item">
          <span className="chart-legend-swatch ramax" />
          <span>CAX (RaMAx)</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto" }}>
        {/* grid */}
        {[0, 50, 100, 150, 200, 250].map((t) => (
          <g key={t}>
            <line className="chart-grid-line" x1={m.l} y1={y(t)} x2={w - m.r} y2={y(t)} />
            <text className="chart-axis-text" x={m.l - 8} y={y(t) + 4} textAnchor="end">{t}</text>
          </g>
        ))}
        {/* axes */}
        <line className="chart-axis-line" x1={m.l} y1={m.t} x2={m.l} y2={h - m.b} />
        <line className="chart-axis-line" x1={m.l} y1={h - m.b} x2={w - m.r} y2={h - m.b} />
        {/* x ticks */}
        {[4, 5, 6, 7].map((v) => (
          <text key={v} className="chart-axis-text" x={x(v)} y={h - m.b + 20} textAnchor="middle">{v} genomes</text>
        ))}
        {/* axis labels */}
        <text className="chart-axis-text" x={m.l + iw / 2} y={h - 6} textAnchor="middle" fontWeight={600}>输入基因组数量</text>
        <text className="chart-axis-text" x={14} y={m.t + ih / 2} textAnchor="middle" transform={`rotate(-90,14,${m.t + ih / 2})`} fontWeight={600}>CPU Hours</text>
        {/* lines */}
        <path className="chart-line-baseline" d={line("cactusTime")} />
        <path className="chart-line-ramax" d={line("ramaxTime")} />
        {/* points + labels */}
        {scalingRows.map((r) => (
          <g key={`c${r.genomes}`}>
            <circle className="chart-point-baseline" cx={x(r.genomes)} cy={y(r.cactusTime)} r={4} />
            <text className="chart-label-baseline" x={x(r.genomes)} y={y(r.cactusTime) - 10} textAnchor="middle">{fmt(r.cactusTime, 1)}</text>
          </g>
        ))}
        {scalingRows.map((r) => (
          <g key={`r${r.genomes}`}>
            <circle className="chart-point-ramax" cx={x(r.genomes)} cy={y(r.ramaxTime)} r={4} />
            <text className="chart-label-ramax" x={x(r.genomes)} y={y(r.ramaxTime) + 16} textAnchor="middle">{fmt(r.ramaxTime, 1)}</text>
          </g>
        ))}
      </svg>
      <div className="chart-caption">
        <strong>Fig 1.</strong> 不同基因组规模下的累计计算耗时对比。Cactus 斜率 {fmt(metrics.cactusSlope, 1)}h/genome，
        RaMAx 仅 {fmt(metrics.ramaxSlope, 1)}h/genome。
        数据来源: 实验结果
      </div>
    </div>
  );
}

function BarChart({ rows, title, source }: { rows: CladeRow[]; title: string; source: string }) {
  const maxTime = Math.max(...rows.map((r) => r.cactusTime));
  const r = useReveal<HTMLDivElement>();

  return (
    <div className="bar-chart reveal" ref={r}>
      <div className="bar-chart-header">
        <strong style={{ color: "var(--text-primary)", fontSize: "0.9rem" }}>{title}</strong>
        <span>{source}</span>
      </div>
      {rows.map((row) => {
        const s = speedup(row.cactusTime, row.ramaxTime);
        return (
          <div className="bar-chart-row" key={row.clade}>
            <div className="bar-chart-clade">{row.clade}</div>
            <div className="bar-chart-bars">
              <div
                className="bar-chart-bar baseline"
                style={{ "--bar-width": `${(row.cactusTime / maxTime) * 100}%`, width: `${(row.cactusTime / maxTime) * 100}%` } as React.CSSProperties}
              >
                <span className="bar-chart-bar-label">{fmt(row.cactusTime, 1)}h</span>
              </div>
              <div
                className="bar-chart-bar ramax"
                style={{ "--bar-width": `${(row.ramaxTime / maxTime) * 100}%`, width: `${(row.ramaxTime / maxTime) * 100}%` } as React.CSSProperties}
              >
                <span className="bar-chart-bar-label">{fmt(row.ramaxTime, 1)}h</span>
              </div>
            </div>
            <div className="bar-chart-speedup">{fmt(s, 1)}×</div>
          </div>
        );
      })}
    </div>
  );
}

function PerformanceSection() {
  const [filter, setFilter] = useState<"all" | "family" | "genus">("all");
  const filteredRows = useMemo(
    () => filter === "all" ? cladeRows : cladeRows.filter((r) => r.level === filter),
    [filter]
  );
  const familyRows = cladeRows.filter((r) => r.level === "family");
  const genusRows = cladeRows.filter((r) => r.level === "genus");
  const r = useReveal<HTMLDivElement>();

  return (
    <section className="bg-alt" id="performance">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">Benchmarks</span>
          <h2>性能基准</h2>
          <p>
            在 {cladeRows.length} 个哺乳动物进化分支中验证，累计加速比 {fmt(metrics.totalSpeedup, 1)}×，
            节省 {fmt(metrics.totalReduction, 1)}% 计算时间。
          </p>
        </div>

        <ScalingChart />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 32, marginBottom: 40 }}>
          <BarChart rows={familyRows} title="科级分支 (Family)" source="实验结果" />
          <BarChart rows={genusRows} title="属级分支 (Genus)" source="实验结果" />
        </div>

        {/* Summary table */}
        <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: "1rem" }}>分支加速详表</strong>
          <div className="table-filters">
            {(["all", "family", "genus"] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`table-filter${filter === f ? " active" : ""}`}
                onClick={() => setFilter(f)}
              >
                {f === "all" ? "全部" : f === "family" ? "科级 Family" : "属级 Genus"}
              </button>
            ))}
          </div>
        </div>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Clade</th>
                <th>Level</th>
                <th>Cactus (h)</th>
                <th>CAX (h)</th>
                <th>Speedup</th>
                <th>节省</th>
                <th>Cactus Mem (GB)</th>
                <th>CAX Mem (GB)</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const s = speedup(row.cactusTime, row.ramaxTime);
                const red = reduction(row.cactusTime, row.ramaxTime);
                return (
                  <tr key={`${row.level}-${row.clade}`}>
                    <td><strong>{row.clade}</strong></td>
                    <td>{row.levelZh}</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{fmt(row.cactusTime, 2)}</td>
                    <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 600 }}>{fmt(row.ramaxTime, 2)}</td>
                    <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 700 }}>{fmt(s, 1)}×</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{fmt(red, 1)}%</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{fmt(row.cactusMemory, 1)}</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{fmt(row.ramaxMemory, 1)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr style={{ fontWeight: 600 }}>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)" }}><strong>Total / Avg</strong></td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)" }}></td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)", fontFamily: "var(--font-mono)" }}>{fmt(metrics.totalCactusTime, 1)}</td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)", fontFamily: "var(--font-mono)", color: "var(--accent)" }}>{fmt(metrics.totalRamaxTime, 1)}</td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)", fontFamily: "var(--font-mono)", color: "var(--accent)" }}><strong>{fmt(metrics.totalSpeedup, 1)}×</strong></td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)", fontFamily: "var(--font-mono)" }}>{fmt(metrics.totalReduction, 1)}%</td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)" }}></td>
                <td style={{ padding: "10px 14px", borderTop: "2px solid var(--border)" }}></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ QUALITY ━━━━━━━━━━━━━ */
function QualitySection() {
  const maxF1 = Math.max(...qualityRows.map((r) => r.f1));
  const r = useReveal<HTMLDivElement>();

  return (
    <section id="quality">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">Alignment Quality</span>
          <h2>对齐质量验证</h2>
          <p>
            在 Primates Benchmark 灵长类标准测试中，CAX 调配的 RaMAx 混合对齐获得
            Precision {metrics.ramaxQuality.precision.toFixed(5)}（所有工具最高），
            F1-Score {metrics.ramaxQuality.f1.toFixed(5)}，基线留存率 {fmt(metrics.f1Retained, 1)}%。
          </p>
        </div>
        <div className="data-table-wrapper">
          <table className="quality-table">
            <thead>
              <tr>
                <th>Aligner</th>
                <th>Entry</th>
                <th>Precision</th>
                <th>Recall</th>
                <th>F1-Score</th>
                <th style={{ width: 180 }}>F1 (归一化)</th>
              </tr>
            </thead>
            <tbody>
              {qualityRows.map((row) => {
                const isRamax = row.aligner === "RaMAx";
                const isCactus = row.aligner === "Progressive Cactus";
                const cls = isRamax ? "row-cax" : isCactus ? "row-baseline" : "";
                return (
                  <tr key={`${row.aligner}-${row.entry}`} className={cls}>
                    <td><strong>{row.aligner}</strong></td>
                    <td style={{ color: "var(--text-tertiary)" }}>{row.entry || "—"}</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{row.precision.toFixed(5)}</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{row.recall.toFixed(5)}</td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>{row.f1.toFixed(5)}</td>
                    <td>
                      <div className="f1-bar">
                        <div className="f1-bar-track">
                          <div
                            className={`f1-bar-fill${isRamax ? " accent" : isCactus ? " baseline" : ""}`}
                            style={{ width: `${(row.f1 / maxF1) * 100}%` }}
                          />
                        </div>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="chart-caption" style={{ marginTop: 12 }}>
          <strong>Table 2.</strong> Primates Benchmark 多向对齐精度对比（实验结果）。
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ QUICK START ━━━━━━━━━━━━━ */
const installCode = `# 推荐在干净 Conda 环境中安装
conda create -n cax python=3.10 -y
conda activate cax

# 一键安装推荐 Cactus 版本
bash cactus-install.sh

# 安装 RaMAx、Mash 与 CAX
conda install -c conda-forge -c malab ramax
conda install -c bioconda mash
pip install -e .`;

const runCode = `# 自动生成输出路径并直接运行
cax auto --seqfile examples/evolverPrimates.txt --mash-threshold 0.02

# 或打开交互式 UI
cax`;

function QuickStart() {
  const r = useReveal<HTMLDivElement>();
  return (
    <section className="quickstart" id="quickstart">
      <div className="container reveal" ref={r}>
        <div className="section-header" style={{ textAlign: "center" }}>
          <span className="section-label">Getting Started</span>
          <h2>快速开始</h2>
          <p style={{ margin: "0 auto" }}>几条命令即可完成从安装到输出的完整流程。</p>
        </div>
        <div className="quickstart-grid">
          <div className="quickstart-card">
            <h3>安装环境</h3>
            <div className="quickstart-code">
              <pre><code>{installCode}</code></pre>
              <CopyBtn text={installCode.replace(/^#.*\n?/gm, "").trim()} />
            </div>
          </div>
          <div className="quickstart-card">
            <h3>验证运行</h3>
            <div className="quickstart-code">
              <pre><code>{runCode}</code></pre>
              <CopyBtn text={runCode.replace(/^#.*\n?/gm, "").trim()} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ WORKFLOW ━━━━━━━━━━━━━ */
const workflowSteps = [
  { id: "01", title: "cactus-prepare", desc: "输入多基因组序列路径，生成 Cactus 原始静态规划拓扑指令。" },
  { id: "02", title: "parseGraph", desc: "CAX 自动加载该指令，解析轮次依赖并重建为发育拓扑关系图。" },
  { id: "03", title: "tree-inspect", desc: "规划终端交互审查，点击节点可手动微调或自动配置替换属性。" },
  { id: "04", title: "mash-check", desc: "分析各支系近缘 Mash 同源距离矩阵，以定量证据驱动加速决策。" },
  { id: "05", title: "hybrid-build", desc: "一键调度：将高同源子树自动指派给 RaMAx 比对核，完成混合规划。" },
  { id: "06", title: "export & run", desc: "导出高并发 Makefile 并执行比对拼接，输出完整的全局 HAL 比对数据库。" },
];

function Workflow() {
  const r = useReveal<HTMLDivElement>();
  return (
    <section className="workflow-section" id="workflow">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">Workflow</span>
          <h2>工作流</h2>
          <p>从输入到输出的完整六步流程。</p>
        </div>
        <div className="workflow-steps">
          {workflowSteps.map((step) => (
            <div className="workflow-step" key={step.id}>
              <div className="workflow-step-number">{step.id}</div>
              <div className="workflow-step-content">
                <div className="workflow-step-title">{step.title}</div>
                <div className="workflow-step-desc">{step.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ CITATION ━━━━━━━━━━━━━ */
function Citation() {
  const r = useReveal<HTMLDivElement>();
  return (
    <section className="citation-section" id="citation">
      <div className="container reveal" ref={r}>
        <div className="section-header">
          <span className="section-label">How to Cite</span>
          <h2>引用</h2>
          <p>
            CAX 的相关论文正在准备中，敬请期待。
            如需了解最新进展或在研究中使用本工具，请通过 GitHub 与我们联系。
          </p>
        </div>
        <div className="citation-block" style={{ color: "var(--text-tertiary)", fontStyle: "italic", fontSize: "0.9rem", background: "var(--bg-code)", border: "1px dashed #30363d", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 120, textAlign: "center" }}>
          <span>论文撰写中，BibTeX 引用格式即将发布 …</span>
        </div>
      </div>
    </section>
  );
}

/* ━━━━━━━━━━━━━ FOOTER ━━━━━━━━━━━━━ */
function Footer() {
  return (
    <footer className="site-footer">
      <div className="container">
        <strong>Cactus-RaMAx (CAX)</strong>
        <span className="footer-sep">·</span>
        <span>
          © 2026 By <a href="mailto:tianqinzhong@qq.com">Qinzhong Tian</a> ·{" "}
          <a href="http://lab.malab.cn/~cjt/MSA/" target="_blank" rel="noopener noreferrer">MALab</a>
        </span>
        <span className="footer-sep">·</span>
        <a href="https://github.com/malabz/Cax" target="_blank" rel="noopener noreferrer">
          <GitBranch size={14} /> GitHub
        </a>
      </div>
    </footer>
  );
}

/* ━━━━━━━━━━━━━ APP ━━━━━━━━━━━━━ */
export default function App() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <About />
        <TerminalDemo />
        <PerformanceSection />
        <QualitySection />
        <QuickStart />
        <Workflow />
        <Citation />
      </main>
      <Footer />
    </>
  );
}
