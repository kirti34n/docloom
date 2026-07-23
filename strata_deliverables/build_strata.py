"""Author the Strata deliverables as docloom IR and render them.

This is the faithful docloom pipeline: an LLM (here, Claude/Opus acting as the
model) emits schema-validated IR; the deterministic linter gates it; the
renderers turn it into native PPTX / DOCX / HTML / SVG. Nothing is hand-drawn.

Produces, all Mphasis-branded:
  1. strata_deck.pptx / .html   -- the interactive deck (native charts + shapes)
  2. strata_spec.docx  / .html  -- the technical specification (report)
  3. strata_architecture.svg / .png / .drawio -- the standalone architecture diagram
"""
from __future__ import annotations

import sys
from pathlib import Path

from docloom import (
    BulletList, Callout, Chart, Code, Diagram, DiagramEdge, DiagramGroup,
    DiagramNode, Document, Heading, Image, ListItem, NumberedList, Paragraph,
    Quote, Series, Slide, Stat, StatRow, Table, Theme, lint, has_errors, render,
    render_diagram,
)

HERE = Path(__file__).resolve().parent
LOGO = r"C:\Users\kirti\Downloads\mphasis-intellyx-BC-logo-800x500-1.png"

# --------------------------------------------------------------------------- #
# Mphasis brand theme (colors sampled from the logo chevron gradient).
# text-on-background / text-on-surface stay dark-on-light for WCAG AA.
# --------------------------------------------------------------------------- #
THEME = Theme(
    primary="#CC0C60",      # Mphasis magenta
    accent="#30A8D8",       # Mphasis cyan-blue
    background="#FFFFFF",
    surface="#F4F5F7",
    text="#10121A",
    muted="#5B6472",
    font_heading="Arial",
    font_body="Calibri",
)

LOGO_IMG = Image(path=LOGO, alt="Mphasis — The Next Applied")

# small authoring helpers -------------------------------------------------- #
def B(*items, levels=None):
    levels = levels or [0] * len(items)
    return BulletList(items=[ListItem(text=t, level=l) for t, l in zip(items, levels)])

def N(*items):
    return NumberedList(items=[ListItem(text=t) for t in items])

# --------------------------------------------------------------------------- #
# DIAGRAMS (coordinate-free IR; the solver lays them out)
# --------------------------------------------------------------------------- #

# A) Full reference architecture -- standalone deliverable + in the spec
DIAGRAM_ARCH = Diagram(
    id="strata-architecture",
    title="Strata reference architecture",
    direction="LR",
    layout="dot",
    caption="Four planes keep the fast retrieval path free of the slow, "
            "asynchronous learning path; memory is derived from traces, never "
            "written directly by a run.",
    groups=[
        DiagramGroup(id="hot", label="Hot read plane · ≤100ms · no LLM"),
        DiagramGroup(id="ing", label="Ingest plane · async, fire-and-forget"),
        DiagramGroup(id="bg", label="Background plane · batch workers"),
        DiagramGroup(id="ctl", label="Control plane"),
    ],
    nodes=[
        DiagramNode(id="agent", type="client", label="Agent runtime",
                    sublabel="your app / SDK", group="hot"),
        DiagramNode(id="retriever", type="service", label="Retriever",
                    sublabel="hybrid search + abstention", group="hot"),
        DiagramNode(id="assembler", type="service", label="Assembler",
                    sublabel="budgets · render-as-data", group="hot"),
        DiagramNode(id="valkey", type="store", label="Working mem + tool cache",
                    sublabel="Valkey · TTL", group="hot"),
        DiagramNode(id="feedback", type="external", label="Feedback adapters",
                    sublabel="verdict / correction", group="ing"),
        DiagramNode(id="queue", type="queue", label="Ingest queue",
                    sublabel="Postgres SKIP LOCKED", group="ing"),
        DiagramNode(id="trace", type="store", label="Trace archive",
                    sublabel="fs / S3 driver", group="ing"),
        DiagramNode(id="learn", type="service", label="Extractors + Distiller",
                    sublabel="operational + quality lanes", group="bg"),
        DiagramNode(id="validate", type="security", label="Shadow validator",
                    sublabel="anti-poisoning", group="bg"),
        DiagramNode(id="store", type="store", label="Memory store",
                    sublabel="Postgres + pgvector", group="bg"),
        DiagramNode(id="prefix", type="service", label="Prefix builder",
                    sublabel="prompt-cache prefix", group="bg"),
        DiagramNode(id="dash", type="client", label="Dashboard",
                    sublabel="injections · lift · review", group="ctl"),
        DiagramNode(id="kill", type="security", label="Kill switch",
                    sublabel="net-lift auto-disable", group="ctl"),
    ],
    edges=[
        DiagramEdge(source="agent", target="retriever", style="emphasis"),
        DiagramEdge(source="retriever", target="assembler"),
        DiagramEdge(source="assembler", target="agent", label="context block"),
        DiagramEdge(source="agent", target="valkey", label="working mem"),
        DiagramEdge(source="agent", target="queue", style="dashed", label="trace events"),
        DiagramEdge(source="feedback", target="queue", style="dashed", label="outcome events"),
        DiagramEdge(source="queue", target="trace"),
        DiagramEdge(source="trace", target="learn", label="distill"),
        DiagramEdge(source="learn", target="store"),
        DiagramEdge(source="validate", target="store", style="secure"),
        DiagramEdge(source="store", target="retriever", style="emphasis", label="recall"),
        DiagramEdge(source="prefix", target="assembler", label="static prefix"),
        DiagramEdge(source="store", target="dash"),
        DiagramEdge(source="trace", target="dash"),
        DiagramEdge(source="kill", target="store", style="secure", label="auto-disable"),
    ],
)

# B) Deck overview -- lean (6 nodes, terse) so it renders as crisp native shapes
DIAGRAM_OVERVIEW = Diagram(
    id="strata-planes",
    direction="LR",
    layout="dot",
    caption="The hot path does retrieval only; a separate asynchronous path "
            "learns from traces and feeds recall back in.",
    groups=[
        DiagramGroup(id="hot", label="Hot path · ≤100ms · no LLM"),
        DiagramGroup(id="slow", label="Learning · async workers"),
        DiagramGroup(id="ctl", label="Control"),
    ],
    nodes=[
        DiagramNode(id="agent", type="client", label="Agent runtime", group="hot"),
        DiagramNode(id="hot", type="service", label="Retrieve + assemble", group="hot"),
        DiagramNode(id="trace", type="store", label="Trace archive", group="slow"),
        DiagramNode(id="learn", type="service", label="Learners + gates", group="slow"),
        DiagramNode(id="store", type="store", label="Memory store", group="slow"),
        DiagramNode(id="dash", type="client", label="Dashboard + kill switch", group="ctl"),
    ],
    edges=[
        DiagramEdge(source="agent", target="hot", style="emphasis"),
        DiagramEdge(source="hot", target="agent", label="context"),
        DiagramEdge(source="agent", target="trace", style="dashed", label="traces"),
        DiagramEdge(source="trace", target="learn"),
        DiagramEdge(source="learn", target="store"),
        DiagramEdge(source="store", target="hot", style="emphasis", label="recall"),
        DiagramEdge(source="store", target="dash"),
    ],
)

# B2) Deck lifecycle -- lean (5 nodes, no sublabels) for slide legibility;
# the full 8-node lifecycle (DIAGRAM_LIFECYCLE) is used in the spec.
DIAGRAM_LIFECYCLE_DECK = Diagram(
    id="strata-lifecycle-deck",
    direction="LR",
    layout="dot",
    caption="Content-derived lessons are quarantined and never injected until "
            "real outcomes confirm them; drift and repeated failure retire them.",
    nodes=[
        DiagramNode(id="trace", type="store", label="Trace"),
        DiagramNode(id="gate", type="service", label="Distill + gate"),
        DiagramNode(id="quarantine", type="security", label="Quarantine (Tier B)"),
        DiagramNode(id="validated", type="store", label="Validated"),
        DiagramNode(id="retired", type="external", label="Stale → retired"),
    ],
    edges=[
        DiagramEdge(source="trace", target="gate"),
        DiagramEdge(source="gate", target="validated", label="Tier A"),
        DiagramEdge(source="gate", target="quarantine", label="Tier B"),
        DiagramEdge(source="quarantine", target="validated", style="secure",
                    label="shadow-confirmed"),
        DiagramEdge(source="validated", target="retired", style="dashed",
                    label="drift · two-strike"),
    ],
)

# C) Memory lifecycle / promotion pipeline -- deck + spec
DIAGRAM_LIFECYCLE = Diagram(
    id="strata-lifecycle",
    title="How a lesson earns injection",
    direction="LR",
    layout="dot",
    caption="Content-derived lessons are quarantined and never injected until "
            "real outcomes confirm them; drift and repeated failure retire them.",
    nodes=[
        DiagramNode(id="trace", type="store", label="Trace", sublabel="every run"),
        DiagramNode(id="extract", type="service", label="Extract + distill",
                    sublabel="parsers / pinned LLM"),
        DiagramNode(id="tierA", type="service", label="Candidate (Tier A)",
                    sublabel="operational · low-risk"),
        DiagramNode(id="tierB", type="security", label="Quarantine (Tier B)",
                    sublabel="content-derived"),
        DiagramNode(id="shadow", type="security", label="Shadow validation",
                    sublabel="confirm vs outcomes"),
        DiagramNode(id="validated", type="store", label="Validated",
                    sublabel="injectable · Q-scored"),
        DiagramNode(id="stale", type="external", label="Stale / superseded",
                    sublabel="events · TTL · drift"),
        DiagramNode(id="retired", type="external", label="Retired / archived",
                    sublabel="two-strike · decay"),
    ],
    edges=[
        DiagramEdge(source="trace", target="extract"),
        DiagramEdge(source="extract", target="tierA"),
        DiagramEdge(source="extract", target="tierB"),
        DiagramEdge(source="tierA", target="validated"),
        DiagramEdge(source="tierB", target="shadow"),
        DiagramEdge(source="shadow", target="validated", style="secure", label="2 confirmations"),
        DiagramEdge(source="validated", target="stale", style="dashed", label="invalidation"),
        DiagramEdge(source="stale", target="validated", label="re-verify"),
        DiagramEdge(source="stale", target="retired", label="two strikes"),
    ],
)

# --------------------------------------------------------------------------- #
# 1. THE DECK  (interactive PPTX)
# --------------------------------------------------------------------------- #
deck = Document(
    title="Strata",
    subtitle="Project-scoped learning memory for an Agent-as-a-Service platform",
    authors=["Mphasis · The Next Applied"],
    date="July 2026",
    logo=LOGO_IMG,
    slides=[
        Slide(layout="title", title="Strata",
              subtitle="Project-scoped learning memory for an Agent-as-a-Service platform",
              notes="Codename Strata: a layered memory over a trace bed. Working "
                    "design, July 2026. Every number is provisional and "
                    "dashboard-corrected; the architecture is not."),

        Slide(layout="content",
              title="Production agents relearn the same lessons on every run",
              blocks=[
                  Callout(style="danger",
                          text="Agents ship with fixed behavior and no memory of "
                               "what already worked or failed in this exact project."),
                  B("A tool that always times out on a payload class is retried "
                    "blindly, run after run.",
                    "An entity already ruled a false positive is re-investigated "
                    "from scratch every time.",
                    "Nothing an agent learns while running is carried into the "
                    "next run, so quality never compounds."),
              ],
              notes="This is the gap Strata closes: turn each run's evidence into "
                    "memory the next run can use — safely."),

        Slide(layout="content",
              title="Memory should make agents better without ever blocking them",
              blocks=[
                  B("Enhancer, never a dependency: the hot path does retrieval "
                    "only — zero LLM calls, hard 100ms timeout, fail-open.",
                    "Abstention is the default: most runs inject nothing, so "
                    "memory only spends context when it earns it.",
                    "Memory never changes defined behavior: it enters the prompt "
                    "as labeled data, below the definition, never as instructions.",
                    "A guessed reward is worse than none: only unambiguous "
                    "outcomes update scores."),
                  Callout(style="info",
                          text="The project is the wall: memory lives per project, "
                               "with no cross-project or cross-org sharing."),
              ],
              notes="These are four of the eight non-negotiable invariants. "
                    "Everything else in the design serves them."),

        Slide(layout="section", title="What Strata guarantees",
              subtitle="The numbers memory has to hit to be allowed in the prompt"),

        Slide(layout="content",
              title="Injected tokens cost forever, so most runs inject none",
              blocks=[
                  StatRow(items=[
                      Stat(label="Injected tokens/day at 100k runs", value="100M"),
                      Stat(label="Runs that inject zero dynamic tokens", value="≥50%"),
                      Stat(label="Hot-path retrieval budget (p99)", value="≤100ms"),
                      Stat(label="LLM calls inside a run", value="0"),
                  ]),
                  Paragraph(text="Injection is the dominant recurring cost of a "
                                 "memory layer. Strata treats context space as a "
                                 "budget every memory must pay for, continuously."),
              ],
              notes="1,000 injected tokens × 100k runs/day = 100M tokens/day, "
                    "forever. That math is why abstention is the default."),

        Slide(layout="two_column",
              title="Eight invariants hold the line; every number stays tunable",
              blocks=[
                  Heading(level=3, text="Non-negotiable"),
                  B("Enhancer, never a dependency",
                    "Abstention is the default",
                    "Memory never changes defined behavior",
                    "A guessed reward is worse than none"),
              ],
              right=[
                  Heading(level=3, text="By construction"),
                  B("The project is the isolation wall",
                    "Everything derived points to raw evidence",
                    "Open source, permissive by default",
                    "Skills are out of scope (export hook only)"),
              ],
              notes="A change to any invariant is a redesign, not a tune. The "
                    "tunables all live in the dashboard and are falsifiable."),

        Slide(layout="section", title="What's new here",
              subtitle="Claims the surveyed 2026 memory market does not make"),

        Slide(layout="content",
              title="Four claims the 2026 memory market does not make",
              blocks=[
                  BulletList(display="grid", items=[
                      ListItem(text="Governed ownership + validated promotion"),
                      ListItem(text="Two-lane learning: operational vs quality"),
                      ListItem(text="Feedback gradients, not on/off switches"),
                      ListItem(text="Shadow validation as anti-poisoning"),
                  ]),
              ],
              notes="A preview of the differentiators; the next slide expands each "
                    "against what the surveyed market actually ships."),

        Slide(layout="two_column",
              title="Strata ships governance the market's flat memory layers skip",
              blocks=[
                  Heading(level=3, text="New"),
                  B("Governed ownership + a validated promotion pipeline",
                    "Two-lane learning: operational vs quality",
                    "Feedback gradient adapters with signal weights",
                    "Shadow validation as anti-poisoning, not just a filter"),
              ],
              right=[
                  Heading(level=3, text="Also new"),
                  B("Earn-your-context kill switch (holdout + net lift)",
                    "Derived state as a distinct, un-gated write class",
                    "Subject-tagged facts for privacy and delete-by-subject",
                    "Trace-up consolidation: distill only from raw traces"),
              ],
              notes="Borrowed deliberately: bi-temporal validity (Zep/Graphiti), "
                    "RRF hybrid retrieval, clamped Q-values, blackboard state, "
                    "stable-prefix prompt caching. We reinvent none of these."),

        Slide(layout="content",
              title="Two lanes keep learning honest and cheap",
              blocks=[
                  Table(caption="The operational lane runs everywhere; the quality "
                                "lane exists only where feedback does.",
                        header=["", "Operational lane", "Quality lane"],
                        rows=[
                            ["Source", "Execution signals (errors, timeouts)", "Real feedback on output"],
                            ["Method", "Code parsers, no LLM", "Distilled, behind gates"],
                            ["Needs feedback", "No — universal", "Yes — per project"],
                            ["Speed", "Near-real-time", "Batch"],
                        ]),
              ],
              notes="Separating these is what makes learning work on a project "
                    "with no feedback signal at all — the operational lane still "
                    "learns from what broke."),

        Slide(layout="section", title="Architecture",
              subtitle="Separate planes are the latency and cost story"),

        Slide(layout="content",
              title="Four planes split the fast path from slow learning",
              blocks=[DIAGRAM_OVERVIEW],
              notes="Hot read plane is synchronous and LLM-free. Ingest is "
                    "fire-and-forget. Background workers do all distillation, "
                    "scoring, validation and consolidation off the hot path."),

        Slide(layout="content",
              title="Retrieval runs one fixed pipeline in under 100 milliseconds",
              blocks=[
                  N("Resolve scope: project, agent-type, optional user + workflow.",
                    "Attach the prebuilt static prefix (cache-friendly, no search).",
                    "Hybrid retrieve: vector ANN + lexical, fused with RRF.",
                    "Composite score each candidate on four weighted factors.",
                    "Abstention gate: inject nothing unless it clears score + rarity.",
                    "Fill per-type token budgets in descending score order.",
                    "Render as a labeled data block — never as instructions."),
                  Callout(style="warning",
                          text="On a timeout, return the static prefix or nothing. "
                               "Fail open, log the miss — a run is never blocked."),
              ],
              notes="Workflow prefetch hides in-workflow retrieval cost: the next "
                    "agent's memory is fetched in parallel with the current step."),

        Slide(layout="content",
              title="Composite scoring ranks every candidate before abstention",
              blocks=[
                  Chart(chart="pie",
                        title="Composite retrieval score weights",
                        caption="Starting weights, tunable per project; validity "
                                "derives from bi-temporal currency and provenance.",
                        labels=["Similarity", "Usefulness (Q)", "Recency", "Validity"],
                        series=[Series(name="Weight", values=[0.40, 0.30, 0.15, 0.15])]),
              ],
              notes="Only a candidate clearing both the score threshold and the "
                    "rarity gate (≥2 shared rare terms) is ever injected."),

        Slide(layout="content",
              title="Signal strength sets how much each outcome counts",
              blocks=[
                  Chart(chart="bar",
                        title="Feedback signal weight by adapter",
                        caption="Implicit behavior is logged at weight 0: it is "
                                "analytics, never a reward. A guessed reward is "
                                "worse than none.",
                        labels=["Explicit verdict", "Human correction",
                                "Downstream event", "Implicit behavior"],
                        series=[Series(name="Signal weight", values=[1.0, 0.8, 0.3, 0.0])]),
              ],
              notes="Rejection reasons (weight 1.0) are the highest-density "
                    "source the distiller has."),

        Slide(layout="section", title="Learning safely",
              subtitle="Adversarial-by-construction inputs make memory an attack surface"),

        Slide(layout="content",
              title="A lesson earns injection only after outcomes confirm it",
              blocks=[DIAGRAM_LIFECYCLE_DECK],
              notes="Tier A (operational, structured origin) is injectable "
                    "immediately as a labeled lower-trust candidate. Tier B "
                    "(anything a model distilled from run content) is quarantined "
                    "and shadow-validated against real outcomes first."),

        Slide(layout="content",
              title="Memory poisoning is not a hypothetical threat",
              blocks=[
                  StatRow(items=[
                      Stat(label="reported success of the MINJA memory-injection "
                                 "attack, through ordinary queries alone",
                           value="95%"),
                  ]),
              ],
              notes="A single hero number to frame the attack surface before the "
                    "comparison chart — the defense is shadow validation."),

        Slide(layout="content",
              title="Adversarial inputs make memory a real attack surface",
              blocks=[
                  Chart(chart="bar",
                        title="Reported success of memory-poisoning attacks",
                        caption="Promotion requires outcome evidence, so a poisoned "
                                "note must survive contact with reality before it "
                                "can spread — and the blast radius stops at the project wall.",
                        labels=["MINJA injection", "AgentPoison impact", "Benign degradation"],
                        series=[Series(name="Reported rate (%)", values=[95, 62, 1])]),
              ],
              notes="MINJA: >95% injection success via queries alone. AgentPoison: "
                    "~62% target impact with <1% benign degradation — stealthy by "
                    "design. Shadow validation answers this class directly."),

        Slide(layout="content",
              title="Memory keeps its budget only by paying for it",
              blocks=[
                  StatRow(items=[
                      Stat(label="Memory-off holdout, always running", value="5%"),
                      Stat(label="Sustained negative lift → auto-disable", value="14 days"),
                      Stat(label="Total memory envelope per run", value="1,200 tok"),
                  ]),
                  B("A per-agent-type holdout runs memory-off continuously; net "
                    "lift is a dashboard metric, not a belief.",
                    "Sustained negative lift auto-disables that memory type for "
                    "that agent and notifies the developer with the evidence."),
              ],
              notes="This is the earn-your-context kill switch — the mechanism "
                    "that keeps the system honest at scale."),

        Slide(layout="section", title="Delivery",
              subtitle="Five phases, each ending at a gate and a human checkpoint"),

        Slide(layout="content",
              title="Five phases, each ending at a gate",
              blocks=[
                  NumberedList(display="timeline", items=[
                      ListItem(text="Trace substrate"),
                      ListItem(text="Hot path"),
                      ListItem(text="Operational lane"),
                      ListItem(text="Quality lane"),
                      ListItem(text="Workflow memory"),
                  ]),
              ],
              notes="The delivery arc at a glance; the next slide details each "
                    "phase's deliverables and the gate it must pass."),

        Slide(layout="content",
              title="Each phase must pass its gate before the next one starts",
              blocks=[
                  Table(caption="Every phase ends at a gate and a human checkpoint; "
                                "full detail is in the specification.",
                        header=["Phase", "Delivers", "Gate to pass"],
                        rows=[
                            ["0 · Trace substrate", "TraceStore, SDK, ledger", "Queryable trace, zero hot cost"],
                            ["1 · Hot path", "Retrieval, scoring, assembler", "Latency + purity benches green"],
                            ["2 · Operational lane", "Parsers, consolidator", "30-day soak: vault plateaus"],
                            ["3 · Quality lane", "Adapters, scorer, validator", "Lift A/B + red-team green"],
                            ["4 · Workflow memory", "Blackboard, routing, prefetch", "Branch-merge + credit rule"],
                        ]),
              ],
              notes="The gates are encoded so a phase cannot silently ship "
                    "half-working."),

        Slide(layout="content",
              title="Open, permissive, and enterprise-deployable by default",
              blocks=[
                  B("Postgres 16/17 + pgvector for relational, vector and temporal "
                    "data — one store, partitioned by project.",
                    "Valkey (BSD) for working memory and the tool-result cache; "
                    "filesystem or S3-compatible driver for the trace archive.",
                    "Postgres SKIP LOCKED queue — zero new infrastructure to start.",
                    "AGPL components are opt-in slots behind our own interfaces, "
                    "never defaults."),
                  Callout(style="success",
                          text="Pinned small open-weight worker models run locally, "
                               "so run content never leaves the deployment."),
              ],
              notes="Ruled out for license reasons: FalkorDB (SSPL), Memgraph / "
                    "ArangoDB (BSL), Neo4j Community (clustering withheld)."),

        Slide(layout="quote",
              blocks=[Quote(
                  text="Memory as an enhancer that must earn its context — never a "
                       "dependency that can block a run.",
                  attribution="Strata — the load-bearing design principle")],
              notes="If you remember one thing: memory here is optional, "
                    "measured, and honest. It pays for its context or it is "
                    "switched off."),
    ],
)

# --------------------------------------------------------------------------- #
# 2. THE SPECIFICATION  (report -> DOCX + HTML)
# --------------------------------------------------------------------------- #
spec = Document(
    title="Strata — Technical Specification",
    subtitle="Project-scoped learning memory for an Agent-as-a-Service platform",
    authors=["Mphasis · The Next Applied"],
    date="July 2026",
    logo=LOGO_IMG,
    blocks=[
        Heading(level=2, text="Executive summary"),
        Paragraph(text="Strata is a project-scoped learning memory layer for an "
                       "Agent-as-a-Service platform: agents get better by running, "
                       "learning from their own traces, without ever depending on "
                       "memory to function. Three ideas carry the design. First, "
                       "memory is an enhancer, never a dependency — the hot path "
                       "does retrieval only, with a hard 100 ms budget and "
                       "fail-open behavior. Second, memory must earn its context — "
                       "most runs inject nothing, and a continuous holdout "
                       "auto-disables any memory type that stops paying for its "
                       "tokens. Third, learning must be honest — only unambiguous "
                       "outcomes move a score, and nothing a model distilled from "
                       "adversarial run content is trusted until real outcomes "
                       "confirm it."),
        Callout(style="info",
                text="Status: design locked from discussion, July 2026. Every "
                     "numeric tunable in this document is provisional and paired "
                     "with the dashboard metric that corrects it. The architecture "
                     "is not provisional."),

        Heading(level=2, text="The problem Strata solves"),
        Paragraph(text="Agents on an AaaS platform are defined once — behavior, "
                       "scope, and tools — and then run at volume. Today, nothing "
                       "they learn while running survives the run. A tool that "
                       "reliably times out on a payload class is retried blindly; "
                       "an entity already ruled a false positive is re-investigated "
                       "from scratch; a rejection reason that would sharpen the "
                       "next output is discarded. Quality never compounds. Strata "
                       "turns each run's raw evidence into memory the next run can "
                       "use — under strict guarantees that keep it safe, cheap, and "
                       "subordinate to the agent's defined behavior."),

        Heading(level=2, text="Design invariants"),
        Paragraph(text="These rules are what everything else serves. A change to "
                       "any of them is a redesign, not a tune."),
        Table(caption="The eight non-negotiable invariants.",
              header=["#", "Invariant", "What it guarantees"],
              rows=[
                  ["1", "Enhancer, never a dependency",
                   "Hot path does retrieval only; 100 ms p99; fail-open. Memory down means memoryless runs, never blocked runs."],
                  ["2", "Abstention is the default",
                   "Most runs inject nothing dynamic; injection is the dominant recurring cost, so every memory must earn its space."],
                  ["3", "Memory never changes defined behavior",
                   "It enters the prompt as labeled data below the definition, never as instructions (policy-subordination invariant)."],
                  ["4", "A guessed reward is worse than none",
                   "Only unambiguous outcomes update scores; ambiguous signals are logged on the trace, never scored."],
                  ["5", "The project is the wall",
                   "Memory lives per project — no cross-project or cross-org sharing. Org level keeps deployment, admin and billing only."],
                  ["6", "Everything derived points to raw evidence",
                   "Consolidation reads raw traces, never summaries of summaries; every derived memory carries a source-trace pointer."],
                  ["7", "Open source, freely deployable",
                   "Default stack is permissive (PostgreSQL, Apache, BSD, MIT). AGPL components are opt-in slots, never defaults."],
                  ["8", "Skills are out of scope",
                   "Memory stores what was learned as text and data; anything that matures into runnable capability graduates to a separate skill service via an export hook."],
              ]),

        Heading(level=2, text="What is new, and what is deliberately borrowed"),
        Paragraph(text="Grounded in a July 2026 research pass across the surveyed "
                       "memory market (Mem0, Zep/Graphiti, Letta, ReMe, MemOS, "
                       "A-MEM, ReasoningBank, and the MINJA/AgentPoison attack "
                       "literature). Strata's contribution is governance and "
                       "honesty around learning, not new storage primitives."),
        Heading(level=3, text="New — claims the market does not make"),
        B("Governed ownership and promotion: every memory has an explicit owner "
          "scope and moves upward only through validation gates.",
          "Two-lane learning: an operational lane (LLM-free, from execution "
          "signals, universal) and a quality lane (only where feedback exists).",
          "Feedback gradient adapters: feedback is a taxonomy with signal weights, "
          "not an on/off switch.",
          "Shadow validation: content-derived lessons are confirmed against real "
          "outcomes before injection — anti-poisoning as a learning mechanism.",
          "Earn-your-context kill switch: a continuous holdout measures net lift "
          "and auto-disables memory that stops paying for itself.",
          "Derived state as a distinct write class: baselines and watermarks "
          "overwrite deterministically, with no gates, scores, or decay.",
          "Subject-tagged facts: whose fact it is (user, third party, entity, "
          "environment) is first-class metadata, making delete-by-subject tractable.",
          "Trace-up consolidation: distillation always reads the raw trace, never "
          "a summary of a summary."),
        Heading(level=3, text="Borrowed deliberately — commoditized mechanisms we adopt"),
        Paragraph(text="Bi-temporal validity and invalidate-don't-delete "
                       "(Zep/Graphiti); hybrid retrieval fused with RRF; "
                       "multi-factor scoring and clamped Q-value updates; "
                       "rarity/abstention gating and two-strike stale retirement; "
                       "blackboard shared state; branch-per-agent with "
                       "review-before-merge; message offload by symbolic "
                       "reference; and stable-prefix prompt caching. Strata "
                       "reinvents none of these."),

        Heading(level=2, text="The seven memory types on one substrate"),
        Paragraph(text="Types are distinct storage-and-lifecycle classes on a "
                       "shared substrate. Scopes (task, session, workflow) and "
                       "record kinds (observation, decision, failure) are not "
                       "types — they are fields."),
        Table(caption="One trace substrate underneath; seven memory types on top.",
              header=["Type", "What it holds", "Backing store", "Lifecycle"],
              rows=[
                  ["0 · Trace archive", "Full run records for every run; the derivation pool and audit ledger", "fs / S3 driver", "Cold, append-only; outcomes join by run_id"],
                  ["1 · Working memory", "Current run state, scratchpad, offloaded large payloads", "Valkey (TTL)", "Run- or session-scoped; dies with the run"],
                  ["2 · Episodic", "Past-run cases, exemplars, conversation summaries, routing records", "Postgres + pgvector", "Append-only, recency-decayed, consolidated"],
                  ["3 · Semantic", "Durable facts and verdicts with bi-temporal validity and a subject tag", "Postgres + pgvector", "Superseded, never overwritten; invalidated, never deleted"],
                  ["4 · Lesson", "Short typed 'what works / fails' notes (operational + quality lanes)", "Postgres + pgvector", "Gated, Q-scored, decayed, revalidated on use"],
                  ["5 · Preference / persona", "Pinned conventions and persona blocks, explicitly edited", "Static prompt prefix", "Never decayed, never gated; always subordinate to behavior"],
                  ["6 · Derived state", "Baselines, watermarks, rolling aggregates for watchdogs", "Postgres", "Deterministic overwrite each run; no gates, no scoring"],
                  ["7 · Tool-result cache", "Content-hash cache of idempotent tool calls", "Valkey", "TTL matched to source freshness; plumbing, not cognition"],
              ]),

        Heading(level=2, text="Scope model — the project is the wall"),
        Paragraph(text="Org level holds deployment, admin and billing rollup, and "
                       "no memory. Every memory lives inside a project, owned by "
                       "one of five scopes. Within an org, projects do not repeat, "
                       "so cross-project sharing is not a suppressed feature — it is "
                       "a scenario that does not exist."),
        B("Agent-type — lessons, derived state and cache policy that travel with "
          "the agent definition wherever it runs.",
          "Workflow-template — seam lessons and routing records about which step "
          "breaks on which input class.",
          "User — user facts, preferences and episodic memory; conversational "
          "agents only.",
          "Run — working memory and the run blackboard; dies with the run.",
          "Project-shared — environment facts and entity verdicts, readable by "
          "any agent in the project with semantic memory enabled."),
        Paragraph(text="Deletion is tractable by construction: a project's death "
                       "is one partition drop, and delete-by-subject uses the "
                       "subject tag within a project."),

        Heading(level=2, text="Architecture — four planes"),
        Paragraph(text="The separation of planes is the latency and cost story. "
                       "The hot read plane is synchronous, LLM-free, and bounded "
                       "at 100 ms. The ingest plane is fire-and-forget. The "
                       "background plane holds every worker that reads traces and "
                       "writes memory. The control plane is the dashboard, the "
                       "spend ledger, and the kill switch."),
        DIAGRAM_ARCH,
        Heading(level=3, text="Component responsibilities"),
        B("Retriever — scope resolution, hybrid search (pgvector ANN + lexical, "
          "RRF fusion), composite scoring, and the abstention gate.",
          "Assembler — fills per-type token budgets, attaches the cached static "
          "prefix, and renders memory as a labeled data block.",
          "Extractors — pure-code parsers over traces for the operational lane "
          "and derived state; no LLM.",
          "Distiller — a small pinned open-weight model, batch, behind the "
          "novelty gate; produces episodic records and quality lessons.",
          "Scorer — joins outcome events to injection logs and applies Q-updates "
          "on unambiguous outcomes only.",
          "Shadow validator — confirms quarantined content-derived lessons "
          "against real outcomes without injecting them.",
          "Consolidator + Invalidator — nightly merge/dedup/decay, plus "
          "event-driven, TTL, and usage-triggered staleness handling.",
          "Prefix builder — reassembles the static block after each consolidation "
          "cycle so provider prompt caching makes those tokens ~90% cheaper."),
        Heading(level=3, text="Data model sketch"),
        Paragraph(text="Everything is partitioned by project_id in Postgres. The "
                       "injection log is the join key for credit assignment."),
        Code(language="sql", code=(
            "memory_item(\n"
            "  id, project_id, scope_type, scope_id, mem_type, kind,\n"
            "  lane, trust_tier,            -- A | B\n"
            "  status,                      -- quarantined|candidate|validated|\n"
            "                               --   superseded|stale|retired|archived\n"
            "  content, embedding vector, lexemes tsvector,\n"
            "  subject_tag,                 -- user | third_party:<id> |\n"
            "                               --   entity:<id> | environment\n"
            "  q_value float, confidence float,\n"
            "  valid_from, valid_to, created_at, expired_at,\n"
            "  provenance jsonb,            -- trace_ids, verdict_id, tool_refs\n"
            "  schema_version int)\n\n"
            "trace_index(run_id, project_id, agent_type_id, workflow_template_id,\n"
            "            path jsonb, started_at, ended_at, payload_ref, outcome_status)\n"
            "outcome_event(run_id, adapter, weight, payload jsonb, arrived_at)\n"
            "injection_log(run_id, memory_id, tokens, slot)  -- the credit-assignment join"
        )),

        Heading(level=2, text="The hot path — retrieval and assembly"),
        N("Resolve scope from the run context: project, agent-type, optional user "
          "and workflow-template.",
          "Attach the prebuilt static prefix for this agent-type — no search, "
          "prompt-cache friendly.",
          "Hybrid retrieve over scoped semantic facts and episodic exemplars "
          "(vector ANN + lexical, RRF-fused).",
          "Composite score: 0.40 similarity + 0.30 usefulness Q + 0.15 recency + "
          "0.15 validity.",
          "Abstention gate: inject nothing unless the top candidate clears the "
          "score threshold and the rarity gate (≥2 shared rare terms).",
          "Budget fill: descending score into per-type token budgets; dedup "
          "against content already in context.",
          "Render as data: one fenced block, labeled 'recalled data, verify "
          "against current state', never imperative phrasing."),
        Callout(style="warning",
                text="Hard timeout 100 ms p99 on retrieve-through-fill. On a miss, "
                     "return the static prefix only, or nothing. Fail open, log "
                     "the miss. In-workflow retrieval is prefetched in parallel "
                     "with the current step, so its perceived cost approaches zero."),

        Heading(level=2, text="Write path and gates"),
        Paragraph(text="Everything writes to the trace first; memory is derived, "
                       "never written directly by run content. Two trust tiers "
                       "govern how a derived note enters the system."),
        DIAGRAM_LIFECYCLE,
        B("Tier A — operational, derived by parsers from structured execution "
          "metadata. Low poisoning surface; injectable immediately as a labeled, "
          "lower-trust candidate, capped at one note per run.",
          "Tier B — content-derived, distilled by a model from run content "
          "(adversarial by construction). Lands quarantined; never injected while "
          "quarantined.",
          "Shadow validation (Tier B exit) — the validator watches subsequent "
          "matching runs without injecting the lesson; two consistent "
          "confirmations across distinct runs promote it (one for failure lessons).",
          "Other gates — novelty, schema, secret scan, injection-pattern scan "
          "(imperative or tool-invocation phrasing is rejected), and provenance "
          "completeness.",
          "Contradictions — never last-write-wins: a new fact supersedes an old "
          "one only with equal-or-stronger provenance, else it quarantines and "
          "surfaces on the review queue."),

        Heading(level=2, text="Learning system"),
        Paragraph(text="Credit assignment starts at the injection log. Every run "
                       "records exactly which memories were injected; outcome "
                       "events join traces by run_id; the scorer walks outcome → "
                       "trace → injection log → memories."),
        Code(language="text", code=(
            "Q  <-  clamp01( Q + alpha * c * (r - Q) )\n\n"
            "  alpha = 0.30   learning rate\n"
            "  c in [0,1]     judged contribution (did this memory plausibly help?)\n"
            "  r              reward from the feedback adapter\n"
            "  <= 1 update / memory / day ; validated memories start at Q = 0.50\n"
            "  only unambiguous outcomes score -- everything else leaves Q untouched"
        )),
        Table(caption="Feedback adapters and their signal weights.",
              header=["Adapter", "Example", "Weight", "Notes"],
              rows=[
                  ["Explicit verdict", "Analyst approve/reject with reasoning", "1.0", "Gold; rejection reasons feed the distiller"],
                  ["Correction", "Human edits the output before use", "0.8", "The diff is the signal"],
                  ["Downstream event", "Ticket closed resolved; case reopened", "0.3", "Weak, delayed; joins whenever it arrives"],
                  ["Implicit behavior", "Regenerated, abandoned, retried", "0.0", "Logged on trace, never scored"],
                  ["Silence", "No adapter configured", "n/a", "Quality lane off; operational lane still runs"],
              ]),
        Paragraph(text="Workflow credit rule: feedback scores the level it was "
                       "given. An end-of-workflow verdict scores workflow-template "
                       "memories, never per-agent — per-agent scoring requires the "
                       "per-step signals the operational lane provides. Never guess "
                       "per-agent blame from an end result."),

        Heading(level=2, text="Staleness and forgetting"),
        Paragraph(text="A layered defense keeps the vault current and bounded, so "
                       "per-project cost stays flat as run volume grows."),
        N("Bi-temporal validity on semantic facts: supersede, never overwrite; "
          "invalidate, never delete.",
          "Event-driven invalidation: platform events (tool changed, environment "
          "fact updated, workflow edited) invalidate dependents via provenance "
          "selectors.",
          "TTL classes for perishable facts: intel-derived days, environment "
          "months, user facts until superseded.",
          "Usage-triggered revalidation: any retrieved memory older than R days "
          "(default 30) is re-verified asynchronously after the run.",
          "Decay and archive: unused memories decay 5% per idle week and archive "
          "at the floor — recoverable, out of retrieval.",
          "Two-strike retirement: a memory failing re-verification twice, "
          "independently, retires; one failure flags it."),

        Heading(level=2, text="Workflow memory"),
        B("Run blackboard — shared state for one workflow run: hand-off payloads "
          "by reference, hypotheses, intermediate decisions. Parallel spawns each "
          "get a logical branch the orchestrator validates and merges "
          "transactionally; an agent cannot overwrite another's committed keys.",
          "Routing records — for dynamic orchestration, each run appends its input "
          "signature, the path taken, per-step outcomes, and the final outcome. "
          "The router retrieves similar past records as data; its own defined "
          "logic still decides.",
          "In-run hand-offs are dataflow, not memory — the orchestrator's job. The "
          "blackboard earns its place only for large-payload reference passing and "
          "parallel-branch coordination."),

        Heading(level=2, text="Storage and stack"),
        Paragraph(text="Defaults are permissive and license-verified as of July "
                       "2026; AGPL options are opt-in slots behind Strata's own "
                       "interfaces."),
        Table(caption="Default stack, opt-in alternatives, and when to switch.",
              header=["Slot", "Default (license)", "Opt-in", "Switch when"],
              rows=[
                  ["Relational + vector + temporal", "Postgres 16/17 + pgvector (PostgreSQL)", "Qdrant (Apache 2.0)", "pgvector p95 > 200 ms under load"],
                  ["Lexical search", "Native Postgres FTS (PostgreSQL)", "ParadeDB pg_search / BM25 (AGPL)", "Policy allows AGPL and lexical quality is the proven bottleneck"],
                  ["Working memory + cache", "Valkey (BSD-3)", "Redis 8 (AGPL option)", "Customer preference only"],
                  ["Trace archive", "Filesystem; SeaweedFS S3 (Apache 2.0)", "MinIO (AGPL-3.0)", "Customer preference"],
                  ["Graph layer", "None — deferred until earned", "Apache AGE (Apache 2.0)", "Entity-relationship query volume proves the need"],
                  ["Queue / workers", "Postgres SKIP LOCKED (zero new infra)", "NATS (Apache 2.0)", "Worker throughput outgrows the DB queue"],
                  ["Worker models", "Pinned small open-weight, local (vLLM)", "n/a", "Pinning is mandatory — scores must stay comparable over time"],
              ]),
        Paragraph(text="Ruled out with reasons: FalkorDB (SSPLv1 service clause), "
                       "Memgraph and ArangoDB (BSL, not open source), Neo4j "
                       "Community (clustering and RBAC withheld), and Kuzu (repo "
                       "archived, October 2025)."),

        Heading(level=2, text="Security, privacy, and compliance"),
        Paragraph(text="Threat model in one line: this platform's inputs are "
                       "adversarial by construction — alert bodies, user text and "
                       "tool payloads all carry attacker-controlled strings — and "
                       "published attacks show memory is a real attack surface."),
        Table(caption="Published memory attacks that motivate the controls.",
              header=["Attack", "Reported effect"],
              rows=[
                  ["MINJA", ">95% injection success via queries alone"],
                  ["AgentPoison", "~62% target impact with <1% benign degradation (stealthy)"],
                  ["MPBench finding", "Aggressive auto-write makes agents measurably more exploitable"],
              ]),
        B("Tier B quarantine and shadow validation: nothing content-derived is "
          "injected unvetted.",
          "Injection-pattern and secret scans before storage; imperative or "
          "tool-invocation phrasing is rejected.",
          "Render-as-data with non-imperative phrasing, enforced by tests.",
          "Promotion requires outcome evidence, so a poisoned note must survive "
          "contact with reality before it can spread.",
          "Blast radius is capped at the project wall by construction; provenance "
          "on everything supports forensics.",
          "Privacy: subject tags enable delete-by-subject; project deletion is a "
          "partition drop; worker models run locally, so run content never leaves "
          "the deployment."),

        Heading(level=2, text="Provisional numbers, and the metric that corrects each"),
        Paragraph(text="Philosophy: set defensible defaults, instrument "
                       "everything, and let the dashboard falsify them. None of "
                       "these are architecture."),
        Table(caption="Representative tunables; each is corrected by a live metric.",
              header=["Parameter", "Initial", "Corrected by"],
              rows=[
                  ["Retrieval timeout", "100 ms p99, fail open", "Latency histogram, miss rate"],
                  ["Memory envelope per run", "1,200 tokens", "Lift-vs-tokens curve per agent-type"],
                  ["Abstention target", "≥50% of runs inject zero dynamic tokens", "False-injection probes, lift"],
                  ["Composite score weights", "0.40 / 0.30 / 0.15 / 0.15", "Per-project tuning vs outcome quality"],
                  ["Q update", "alpha 0.3, clamp [0,1], 1/mem/day", "Q-trajectory stability alerts"],
                  ["Shadow confirmations to promote", "2 distinct runs (1 for failures)", "Time-in-quarantine, red-team results"],
                  ["Decay", "5% per idle week; archive at 0.15", "Vault-size trend (must plateau)"],
                  ["Revalidation age R", "30 days, on retrieval, async", "Stale-retrieval rate"],
                  ["Kill-switch holdout", "5% of runs memory-off", "Net-lift confidence intervals"],
                  ["Auto-disable trigger", "Negative lift sustained 14 days", "Developer overrides, re-enable outcomes"],
                  ["Daily LLM spend cap / project", "Hard cap, configurable", "Spend ledger"],
              ]),

        Heading(level=2, text="Build phases and gates"),
        Table(caption="Five phases; each ends at a gate and a human checkpoint.",
              header=["Phase", "Delivers", "Gate"],
              rows=[
                  ["0 · Trace substrate + SDK", "TraceStore (fs + S3), trace schema, outcome join, ledger skeleton", "Every run yields a complete queryable trace; outcomes attach days later; zero hot-path impact"],
                  ["1 · Hot path", "Working memory + cache, hybrid retrieval, scoring, abstention, assembler, dashboard v0", "Latency bench green; negative probes 0; hot-path purity green"],
                  ["2 · Operational lane + staleness", "Parsers, derived state, consolidator, invalidator, prefix builder", "Synthetic failures produce lessons; 30-day soak shows vault plateau"],
                  ["3 · Quality lane + learning", "Feedback adapters, scorer, shadow validator, promotion gates, lift dashboard", "Lift A/B positive on the SOC sim; poisoning red-team green"],
                  ["4 · Workflow memory + polish", "Blackboard with branches, routing records, prefetch, edit ops, Qdrant/AGE hooks", "Parallel-branch merge correct under contention; end-only credit rule verified"],
              ]),

        Heading(level=2, text="Deliberately out of scope"),
        B("Cross-project and cross-org memory — does not exist by construction.",
          "The skill service — memory keeps the text; a repeatedly-validated "
          "procedure emits an export suggestion, and skills own the runnable.",
          "A graph layer until multi-hop query volume earns it.",
          "Knowledge-base RAG over documents — memory is only what agents learn "
          "from running.",
          "Multi-region replication and a memory marketplace — deployment "
          "concerns and an explicitly rejected direction, respectively."),

        Heading(level=2, text="Glossary"),
        Table(caption="Working vocabulary.",
              header=["Term", "Meaning"],
              rows=[
                  ["Run", "One execution of an agent or workflow. Ephemeral."],
                  ["Session", "A chat lifetime spanning turns; a long-lived run for memory purposes."],
                  ["Project", "The memory isolation boundary inside an org. One use case, one team."],
                  ["Agent-type", "An agent definition created on the platform; all its runs share learning."],
                  ["Trace", "The raw, complete record of a run: inputs, outputs, tool calls, errors, timings."],
                  ["Outcome event", "A feedback record that joins a trace by run_id whenever it arrives."],
                  ["Lane", "Operational (execution-signal learning) or quality (feedback learning)."],
                  ["Injection log", "Per-run record of exactly which memories were injected — the credit-assignment join key."],
              ]),
    ],
)


# --------------------------------------------------------------------------- #
# lint gate + render
# --------------------------------------------------------------------------- #
def gate(doc: Document, name: str) -> None:
    findings = lint(doc, THEME)
    errs = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warning"]
    print(f"\n=== lint {name}: {len(errs)} error(s), {len(warns)} warning(s) ===")
    for f in findings:
        if f.severity in ("error", "warning"):
            print(f"  {f.severity:7} [{f.rule}] {f.where}: {f.message}")
    if has_errors(findings):
        print(f"!! {name} HAS LINT ERRORS -- refusing to render")
        sys.exit(1)


def main() -> None:
    out = HERE
    gate(deck, "deck")
    gate(spec, "spec")

    # 1. deck -> interactive PPTX + self-contained HTML
    render(deck, "pptx", out / "strata_deck.pptx", THEME)
    render(deck, "html", out / "strata_deck.html", THEME)
    # 2. spec -> DOCX + HTML
    render(spec, "docx", out / "strata_spec.docx", THEME)
    render(spec, "html", out / "strata_spec.html", THEME)
    # 3. standalone architecture diagram -> SVG, PNG, drawio
    svg = render_diagram(DIAGRAM_ARCH, THEME, fmt="svg")
    (out / "strata_architecture.svg").write_text(svg, encoding="utf-8")
    png = render_diagram(DIAGRAM_ARCH, THEME, fmt="png")
    if png:
        (out / "strata_architecture.png").write_bytes(png)
        print("wrote strata_architecture.png")
    else:
        print("!! PNG rasterization returned None (resvg extra missing)")
    drawio = render_diagram(DIAGRAM_ARCH, THEME, fmt="drawio")
    (out / "strata_architecture.drawio").write_text(drawio, encoding="utf-8")

    # persist the emitted IR too (the 'LLM output' artifacts)
    deck.save(out / "strata_deck.json")
    spec.save(out / "strata_spec.json")
    (out / "strata_architecture.json").write_text(
        DIAGRAM_ARCH.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    print("\nAll deliverables written to", out)


if __name__ == "__main__":
    main()
