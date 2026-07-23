"""Strata deliverables for the Atom platform — authored as docloom IR from the
uploaded (authoritative) source files, rebuilt properly with a non-pink theme.

Faithful docloom pipeline: emit validated IR -> lint gate -> deterministic render.
Content is taken from Strata_Memory_Documentation.docx + Strata_Memory_Design.pptx;
the Agent-as-a-Service platform is named "Atom".
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
# Professional non-pink theme: indigo primary + teal accent, slate neutrals.
# docloom derives the diagram kind-palette from these hues, so the whole
# system reads as one calm, on-brand family (no magenta anywhere).
# text-on-background / text-on-surface stay dark-on-light for WCAG AA.
# --------------------------------------------------------------------------- #
THEME = Theme(
    primary="#4338CA",      # indigo-700
    accent="#0D9488",       # teal-600
    background="#FFFFFF",
    surface="#F1F5F9",      # slate-100
    text="#0F172A",         # slate-900
    muted="#64748B",        # slate-500
    font_heading="Arial",
    font_body="Calibri",
)
LOGO_IMG = Image(path=LOGO, alt="Mphasis — The Next Applied")


def B(*items, levels=None):
    levels = levels or [0] * len(items)
    return BulletList(items=[ListItem(text=t, level=l) for t, l in zip(items, levels)])

def N(*items):
    return NumberedList(items=[ListItem(text=t) for t in items])


# --------------------------------------------------------------------------- #
# DIAGRAMS
# --------------------------------------------------------------------------- #

# A) Full reference architecture — standalone deliverable + in the spec
DIAGRAM_ARCH = Diagram(
    id="atom-strata-architecture",
    title="Strata on Atom — reference architecture",
    direction="LR",
    layout="dot",
    caption="Four planes keep Atom's fast retrieval path free of the slow, "
            "asynchronous learning path; memory is derived from traces, never "
            "written directly by a run.",
    groups=[
        DiagramGroup(id="hot", label="Hot read plane · ≤100ms · no LLM"),
        DiagramGroup(id="ing", label="Ingest plane · fire-and-forget"),
        DiagramGroup(id="bg", label="Background plane · batch workers"),
        DiagramGroup(id="ctl", label="Control plane"),
    ],
    nodes=[
        DiagramNode(id="agent", type="client", label="Atom agent runtime",
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

# B) Deck overview — lean four planes, crisp native shapes on a slide
DIAGRAM_OVERVIEW = Diagram(
    id="atom-strata-planes",
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
        DiagramNode(id="agent", type="client", label="Atom agent runtime", group="hot"),
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

# C) Full write-path lifecycle — spec
DIAGRAM_LIFECYCLE = Diagram(
    id="atom-strata-lifecycle",
    title="Write path — a memory's lifecycle",
    direction="LR",
    layout="dot",
    caption="Everything writes to the trace first; content-derived lessons are "
            "quarantined and never injected until real outcomes confirm them.",
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

# D) Deck lifecycle — lean
DIAGRAM_LIFECYCLE_DECK = Diagram(
    id="atom-strata-lifecycle-deck",
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


# --------------------------------------------------------------------------- #
# 1. THE DECK  (interactive PPTX)
# --------------------------------------------------------------------------- #
deck = Document(
    title="Strata",
    subtitle="Project-scoped learning memory for Atom, our Agent-as-a-Service platform",
    authors=["Mphasis · The Next Applied"],
    date="July 2026 · design review",
    logo=LOGO_IMG,
    slides=[
        Slide(layout="title", title="Strata",
              subtitle="Project-scoped learning memory for Atom, our Agent-as-a-Service platform",
              notes="Strata is Atom's memory service. Design review, July 2026. "
                    "Every numeric tunable is provisional and dashboard-corrected; "
                    "the architecture is not. Spec: MEMORY_PLAN.md."),

        Slide(layout="content",
              title="Agents on Atom learn, without bloat or drift",
              blocks=[
                  B("Heterogeneous agents, one service — pipelines, chatbots, "
                    "enrichment, watchdogs, workflows. Memory is opt-in per agent.",
                    "No bloat — injected tokens are a recurring bill; most runs "
                    "should inject nothing, so memory must earn its context.",
                    "No behavior drift — defined behavior stays sovereign; memory "
                    "enters as labeled data, never as instructions.",
                    "Learn only from real signal — execution signals for everyone, "
                    "quality learning only where feedback exists."),
                  Callout(style="info",
                          text="A guessed reward is worse than none. Strata is an "
                               "enhancer that must earn its place — never a "
                               "dependency Atom's agents rely on to function."),
              ],
              notes="This is why Strata exists: production agents that learn from "
                    "their own runs, safely, without paying for it on every run."),

        Slide(layout="two_column",
              title="Eight invariants hold; changing one is a redesign",
              blocks=[
                  Heading(level=3, text="Non-negotiable"),
                  B("Enhancer, never a dependency",
                    "Abstention by default",
                    "Policy subordination — data, not instructions",
                    "Honest rewards only"),
              ],
              right=[
                  Heading(level=3, text="By construction"),
                  B("The project is the wall",
                    "Trace-up: derived points to raw evidence",
                    "Open source, freely deployable",
                    "Skills live elsewhere (export hook only)"),
              ],
              notes="Changing any invariant is a redesign, not a tune. Every "
                    "tunable number, by contrast, is falsifiable on the dashboard."),

        Slide(layout="content",
              title="Seven memory types sit on one trace substrate",
              blocks=[
                  B("1 · Working — run/session state, blackboard mode",
                    "2 · Episodic — case summaries, exemplars, routing records",
                    "3 · Semantic — facts + verdicts, bi-temporal, subject-tagged",
                    "4 · Lessons — operational + quality lanes, gated, Q-scored",
                    "5 · Preferences — pinned, edited, never decays",
                    "6 · Derived state — baselines, deterministic overwrite, no gates",
                    "7 · Tool-result cache — content-hash + TTL, the cheapest win"),
                  Callout(style="info",
                          text="All seven rest on Substrate 0 — the trace archive of "
                               "raw runs + outcomes: the derivation pool, credit "
                               "ledger, and audit record. Rule: store conclusions, "
                               "never telemetry."),
              ],
              notes="Deliberately not types: task/session/workflow memory (scopes), "
                    "observation/decision/failure (episodic kinds), goals/checkpoints "
                    "(working-memory fields), skills (separate service), KB-RAG (out "
                    "of scope). The SIEM keeps the alerts; we keep what was derived."),

        Slide(layout="content",
              title="The project is the wall that decides who remembers",
              blocks=[
                  B("Org — deployment, admin, billing rollup; holds no memory",
                    "Agent-type — lessons + derived state travel with the definition",
                    "Workflow-template — seam lessons + routing records",
                    "User (chat only) — user facts, preferences, episodic continuity",
                    "Run — working memory + blackboard; dies with the run",
                    "Project-shared — entity verdicts + environment facts"),
                  Callout(style="warning",
                          text="The project is the memory wall: partition key, "
                               "deletion boundary, and poisoning blast radius. Credit "
                               "rule: an end-of-workflow verdict scores workflow-scope "
                               "memories only — per-agent blame is never guessed."),
              ],
              notes="Within an org, projects do not repeat, so cross-project sharing "
                    "is not a suppressed feature — the scenario does not exist."),

        Slide(layout="section", title="Architecture & economics",
              subtitle="The separation of planes is the latency and cost story"),

        Slide(layout="content",
              title="Four planes split the fast path from slow learning",
              blocks=[DIAGRAM_OVERVIEW],
              notes="Hot read plane: synchronous, 100 ms budget, no LLM imports "
                    "(enforced by CI), fails open. Ingest: fire-and-forget through a "
                    "Postgres queue. Background: batch workers on pinned local models; "
                    "cost scales with vault size, not traffic. Control: dashboard, "
                    "spend ledger, kill switch."),

        Slide(layout="content",
              title="Injection is the bill, so abstention is the cost model",
              blocks=[
                  StatRow(items=[
                      Stat(label="Tokens/day from 1k × 100k runs", value="100M"),
                      Stat(label="Runs that inject zero dynamic tokens", value="≥50%"),
                      Stat(label="Cheaper via cached static prefix", value="~90%"),
                      Stat(label="Cost of an idle project", value="$0"),
                  ]),
                  Callout(style="success",
                          text="Distillation is one-time per lesson; injection is "
                               "forever. Operational lessons come from parsers, not "
                               "LLMs; the distiller runs batch on pinned local models "
                               "behind a novelty gate. Decay makes each vault plateau."),
              ],
              notes="Static prefix (validated agent-type lessons + pinned "
                    "preferences) is rebuilt only at consolidation, so provider "
                    "prompt caching makes those tokens ~90% cheaper. Per-project "
                    "spend has hard daily caps."),

        Slide(layout="two_column",
              title="Two lanes keep learning honest and cheap",
              blocks=[
                  Heading(level=3, text="Operational lane"),
                  B("From execution itself: tool errors, timeouts, schema failures, retries",
                    "Parser-extracted, LLM-free, unambiguous",
                    "Every agent gets this lane"),
              ],
              right=[
                  Heading(level=3, text="Quality lane"),
                  B("Was the output actually good?",
                    "Exists only where a feedback adapter is wired",
                    "No adapter, lane off — nobody learns quality from silence"),
              ],
              notes="Rejection reasons are the densest signal in the whole system."),

        Slide(layout="content",
              title="Signal strength sets how much each outcome counts",
              blocks=[
                  Chart(chart="bar",
                        title="Feedback signal weight by adapter",
                        caption="Q updates are clamped, one per memory per day, from "
                                "unambiguous outcomes only; implicit behavior is "
                                "logged at weight 0, never scored.",
                        labels=["Explicit verdict", "Correction",
                                "Downstream event", "Implicit behavior"],
                        series=[Series(name="Signal weight", values=[1.0, 0.8, 0.3, 0.0])]),
              ],
              notes="Explicit verdict = analyst approve/reject with reasoning (the "
                    "SOC project). Correction = the human's edit diff. Downstream = "
                    "ticket resolved / reopened / alert re-fired."),

        Slide(layout="section", title="Learning safely",
              subtitle="Atom's inputs are adversarial by construction"),

        Slide(layout="content",
              title="Adversarial inputs make memory a real attack surface",
              blocks=[
                  Chart(chart="bar",
                        title="Reported success of memory-poisoning attacks",
                        caption="Content-derived lessons are quarantined and "
                                "shadow-validated before injection; blast radius "
                                "stops at the project wall.",
                        labels=["MINJA injection", "AgentPoison impact", "Benign degradation"],
                        series=[Series(name="Reported rate (%)", values=[95, 62, 1])]),
              ],
              notes="MINJA: >95% injection success via queries alone. AgentPoison: "
                    "~62% target impact with <1% benign degradation. MPBench: "
                    "aggressive auto-write widens exploitability."),

        Slide(layout="content",
              title="A lesson earns injection only after outcomes confirm it",
              blocks=[DIAGRAM_LIFECYCLE_DECK],
              notes="Tier A (operational, structured origin) is injectable "
                    "immediately as a labeled lower-trust candidate. Tier B "
                    "(model-distilled from run content) is quarantined and "
                    "shadow-validated against real outcomes first. Gates before "
                    "storage: injection-pattern scan, secret scan, novelty, provenance."),

        Slide(layout="content",
              title="Fixed and dynamic workflows share one memory story",
              blocks=[
                  B("Run blackboard — shared run state; parallel branches merged "
                    "transactionally, an agent can't overwrite another's committed keys",
                    "Routing records — input signature, path taken, outcomes; the "
                    "router retrieves similar past routes, its own logic decides",
                    "Agent memory travels with its definition — no per-workflow "
                    "copies; pure transform steps carry nothing at all",
                    "Prefetch — the next agent's memory is fetched during the "
                    "current step, so in-workflow retrieval cost approaches zero"),
                  Callout(style="info",
                          text="The trace store is mandatory for dynamic paths: no "
                               "trace, no credit assignment, no routing records."),
              ],
              notes="In-run handoffs remain the orchestrator's dataflow; the "
                    "blackboard earns its place only for large-payload reference "
                    "passing and parallel-branch coordination."),

        Slide(layout="content",
              title="On Atom's real problem, rivals cover under half the spec",
              blocks=[
                  Table(header=["Capability", "Mem0", "Zep", "Tencent", "ReMe", "Strata"],
                        rows=[
                            ["Learns from outcomes", "no", "no", "no", "partial", "yes"],
                            ["Workflow / multi-agent", "no", "no", "no", "no", "yes"],
                            ["Adversarial-input defense", "no", "no", "no", "no", "yes"],
                            ["Measures its own lift", "no", "no", "no", "no", "yes"],
                            ["In production today", "yes", "yes", "early", "early", "phase 0"],
                        ]),
              ],
              notes="Home turf — Mem0: chat personalization; Zep: temporal facts; "
                    "Tencent: anti-bloat/persona; ReMe: dev-tool memory; Strata: "
                    "agent-fleet learning. Honest read: on their home turf they beat "
                    "us today; on Atom's actual problem none covers half the spec. We "
                    "borrowed their best mechanisms (bi-temporal from Zep, non-lossy "
                    "layering from Tencent) instead of competing with them."),

        Slide(layout="section", title="Delivery",
              subtitle="Five phases, each with a hard gate and a human stop"),

        Slide(layout="content",
              title="Every phase must clear a hard gate before the next",
              blocks=[
                  Table(caption="Each phase ends at a human checkpoint; full detail "
                                "is in the specification.",
                        header=["Phase", "Delivers", "Gate to pass"],
                        rows=[
                            ["0 · Trace substrate", "Traces, outcome joins, config", "Queryable traces, zero hot cost"],
                            ["1 · Hot path", "Retrieval, abstention, budgets", "p99 <100ms, no false injections"],
                            ["2 · Operational lane", "Parsers, derived state, staleness", "Vault plateaus; staleness green"],
                            ["3 · Quality lane", "Adapters, Q-scoring, kill switch", "Positive SOC lift; red-team green"],
                            ["4 · Workflow + polish", "Blackboard, routing, prefetch", "Contention + credit-rule pass"],
                        ]),
              ],
              notes="Phase 3 is where belief becomes a number: the SOC project's "
                    "verdict loop is the proving ground for the quality lane. Phase 0 "
                    "can start regardless of the config decision below."),

        Slide(layout="two_column",
              title="The open decision: where memory config is born",
              subtitle="Proposal: the developer declares the envelope; Atom flies inside it",
              blocks=[
                  Heading(level=3, text="Declared by developer"),
                  B("Capability switches chosen at agent creation; empty is valid",
                    "Compliance-friendly: behavior is declared, never self-enabled",
                    "Deliberate write paths (MPBench: auto-write widens the surface)"),
              ],
              right=[
                  Heading(level=3, text="Inferred by platform"),
                  B("Creation-time auto-fill from agent facts (tools, chat, adapters)",
                    "Runtime suggestions upward: 'cache would have saved X — enable?'",
                    "Kill switch already automates the safe, downward direction"),
              ],
              notes="Runtime is always ours — routing, scoring, abstention, budgets, "
                    "per run. The only question is where the config is born. The "
                    "frozen moment is the real disagreement, not auto vs manual."),

        Slide(layout="quote",
              blocks=[Quote(
                  text="Memory that has to keep earning its place — measured per "
                       "agent-type, continuously, and switched off where the answer is no.",
                  attribution="Strata for Atom — the load-bearing principle")],
              notes="Every compared system asks you to believe it helps. This one "
                    "measures it, continuously, and shuts itself off where it does not."),
    ],
)

# --------------------------------------------------------------------------- #
# 2. THE SPECIFICATION  (report -> DOCX + HTML)
# --------------------------------------------------------------------------- #
spec = Document(
    title="Strata — System Documentation",
    subtitle="Project-scoped learning memory for Atom, an Agent-as-a-Service platform",
    authors=["Mphasis · The Next Applied"],
    date="July 2026",
    logo=LOGO_IMG,
    blocks=[
        Heading(level=2, text="Executive summary"),
        Paragraph(text="Strata is the project-scoped learning memory service for "
                       "Atom, an Agent-as-a-Service platform. Agents on Atom get "
                       "better by running — learning from their own traces — without "
                       "ever depending on memory to function. Three ideas carry the "
                       "design. Memory is an enhancer, never a dependency: the hot "
                       "path does retrieval only, with a hard 100 ms budget and "
                       "fail-open behavior. Memory must earn its context: most runs "
                       "inject nothing, and a continuous holdout auto-disables any "
                       "memory type that stops paying for its tokens. Learning must "
                       "be honest: only unambiguous outcomes move a score, and "
                       "nothing a model distilled from Atom's adversarial run content "
                       "is trusted until real outcomes confirm it."),
        Callout(style="info",
                text="Status: design locked, July 2026. Numeric tunables are "
                     "provisional and dashboard-corrected (Section 14); the "
                     "architecture is not. Companions: MEMORY_PLAN.md (source "
                     "specification), CLAUDE_CODE_PROMPT.md (executable build prompt)."),

        Heading(level=2, text="1. Non-negotiable design invariants"),
        Paragraph(text="These rules are the system. A change to any of them is a "
                       "redesign, not a tune."),
        Table(caption="The eight invariants everything else serves.",
              header=["#", "Invariant", "What it guarantees"],
              rows=[
                  ["1", "Enhancer, never a dependency", "The hot path does retrieval only: zero LLM calls, a hard 100 ms p99 budget, fail-open. Memory down means memoryless runs, never blocked runs."],
                  ["2", "Abstention is the default", "Injection is the dominant recurring cost (1,000 tokens across 100k runs/day is 100M tokens/day, forever). Most runs inject nothing; every memory earns its space."],
                  ["3", "Memory never changes defined behavior", "It enters the prompt as labeled data below the definition, never as instructions. It may suggest a policy change on the dashboard; it never silently bends one."],
                  ["4", "A guessed reward is worse than none", "Only unambiguous outcomes update scores. Ambiguous and implicit signals are logged on the trace, never scored."],
                  ["5", "The project is the wall", "Memory lives per project — no cross-project or cross-org memory. Org level keeps deployment, admin, and billing rollup only."],
                  ["6", "Everything derived points to raw evidence", "Consolidation reads raw traces, never summaries of summaries; every derived memory carries a pointer to its source trace."],
                  ["7", "Open source, freely deployable", "The default stack is permissive (PostgreSQL, Apache, BSD, MIT). AGPL components are opt-in slots behind our interfaces, never defaults."],
                  ["8", "Skills and KB-RAG are out of scope", "Memory stores what agents learn from running, as text and data. Runnable capability graduates to the separate skill service via an export hook."],
              ]),

        Heading(level=2, text="2. What is genuinely new, and what is deliberately borrowed"),
        Paragraph(text="Grounded in the July 2026 research pass across Mem0, "
                       "Zep/Graphiti, Letta, ReMe, Tencent, MemOS, A-MEM, "
                       "ReasoningBank, MemRL, omnigraph, unified-mem, the "
                       "MINJA/AgentPoison/MPBench attack literature, and public "
                       "Anthropic/OpenAI patterns. Strata's contribution is "
                       "governance and honesty around learning, not new storage "
                       "primitives."),
        Heading(level=3, text="New claims this system makes"),
        B("Governed ownership and promotion — every memory has an explicit owner "
          "scope and moves upward only through validation gates; nobody ships "
          "governed promotion where 'who should remember it' is first-class.",
          "Two-lane learning — an operational lane (parser-extracted, LLM-free, "
          "universal) and a quality lane that exists only where feedback exists.",
          "Feedback gradient adapters — feedback is a taxonomy with signal weights, "
          "not a switch: verdicts 1.0, corrections 0.8, downstream 0.3, implicit 0.",
          "Shadow validation — content-derived lessons are never injected until "
          "confirmed against real outcomes; anti-poisoning as a learning mechanism.",
          "Earn-your-context kill switch — continuous per-agent-type holdout "
          "sampling; sustained negative lift auto-disables that memory type.",
          "Derived state as a distinct write class — baselines and watermarks "
          "overwrite deterministically: no gates, no scores, no decay.",
          "Subject-tagged facts — whose fact it is (user, third party, entity, "
          "environment) is first-class metadata, making delete-by-subject tractable.",
          "Trace-up consolidation — distillation always reads the raw trace; "
          "non-lossy layering taken further than Tencent's evidence-at-L0."),
        Heading(level=3, text="Borrowed deliberately"),
        Paragraph(text="Bi-temporal validity and invalidate-don't-delete "
                       "(Zep/Graphiti). Hybrid retrieval fused with RRF (ReMe, "
                       "omnigraph, Cognee). Multi-factor scoring and clamped Q "
                       "updates (Generative Agents lineage, unified-mem). Abstention "
                       "and rarity gating, two-strike stale retirement (unified-mem). "
                       "Blackboard shared state (Nii 1986). Branch-per-agent with "
                       "review before merge (omnigraph pattern only). Payload offload "
                       "and symbolic references (Tencent, ReMe). Stable-prefix prompt "
                       "caching (Anthropic guidance)."),

        Heading(level=2, text="3. Vocabulary"),
        Table(caption="Working vocabulary.",
              header=["Term", "Meaning"],
              rows=[
                  ["Run", "One execution of an agent or workflow. Ephemeral."],
                  ["Session", "A chat lifetime spanning turns; a long-lived run for memory purposes."],
                  ["Project", "The memory isolation boundary inside an org. One use case, one team."],
                  ["Agent-type", "An agent definition. All its runs share learning."],
                  ["Workflow-template", "A defined composition of agents. All its runs share seam learning."],
                  ["Trace", "The raw, complete record of a run: inputs, outputs, tool calls, errors, timings."],
                  ["Outcome event", "A feedback record that joins a trace by run_id, whenever it arrives."],
                  ["Lane", "Operational (execution-signal learning) or quality (feedback learning)."],
                  ["Gate", "A check a memory must pass to change state (novelty, validation, promotion)."],
                  ["Injection", "Placing memory content into an agent's context for a run."],
                  ["Injection log", "Per-run record of exactly which memories were injected; the credit-assignment join."],
              ]),

        Heading(level=2, text="4. The seven memory types on one substrate"),
        Paragraph(text="Types are distinct storage-and-lifecycle classes. Not types: "
                       "task/session/workflow memory (scopes), "
                       "observation/decision/failure records (episodic kinds), and "
                       "goals and checkpoints (working-memory fields)."),
        Table(caption="One trace substrate underneath; seven memory types on top.",
              header=["Type", "What it holds", "Backing store", "Lifecycle"],
              rows=[
                  ["0 · Trace archive", "Full run records + outcomes for every run; derivation pool and audit ledger", "fs / S3 driver", "Cold, async; outcomes join by run_id, even days later"],
                  ["1 · Working", "Run state, scratchpad, plan, large payloads by reference; blackboard mode", "Valkey (TTL)", "Run- or session-scoped; never persists raw"],
                  ["2 · Episodic", "Case summaries, exemplars, chat summaries, routing records", "Postgres + pgvector", "Append-only, recency-decayed, consolidated"],
                  ["3 · Semantic", "Facts and verdicts, bi-temporal, subject-tagged, with provenance", "Postgres + pgvector", "Superseded not overwritten; invalidated not deleted"],
                  ["4 · Lesson", "Short typed 'what works / fails' notes; operational + quality lanes", "Postgres + pgvector", "Gated, Q-scored, decayed, revalidated on use"],
                  ["5 · Preference / persona", "Pinned conventions and persona, explicitly edited", "Static prefix", "Never decayed, never gated; subordinate to behavior"],
                  ["6 · Derived state", "Baselines, watermarks, rolling aggregates for watchdogs", "Postgres", "Deterministic overwrite each run; no gates, no scoring"],
                  ["7 · Tool-result cache", "Content-hash cache of idempotent tool calls", "Valkey", "TTL matched to source freshness; plumbing, not cognition"],
              ]),
        Paragraph(text="Rule: memory stores conclusions, never telemetry. The SIEM "
                       "keeps the alerts; Strata keeps what was derived, with a "
                       "pointer back. A user's own explicit statements take a "
                       "validated fast path (the user is the authority on "
                       "themselves); model-inferred style observations are Tier B — "
                       "quarantined and surfaced as suggestions, never silently "
                       "merged into persona."),

        Heading(level=2, text="5. Scope model"),
        Paragraph(text="Org holds deployment, admin, and billing rollup, and no "
                       "memory. Inside it, each project is the memory wall: the "
                       "partition key, the deletion boundary, and the poisoning blast "
                       "radius. Within a project:"),
        B("Agent-type — lessons and derived state travel with the agent definition "
          "wherever it runs; agents inside a workflow get no separate per-workflow copies.",
          "Workflow-template — seam lessons and routing records: learning about the "
          "composition itself.",
          "User (conversational agents only) — user facts, preferences, episodic continuity.",
          "Run — working memory and the blackboard; dies with the run.",
          "Project-shared — entity verdicts and environment facts, readable by any "
          "agent in the project with semantic memory enabled."),
        Paragraph(text="Dynamically spawned parallel agents have no persistent "
                       "identity; their state isolates on blackboard branches and "
                       "their operational lessons accrue to their agent-type "
                       "definition. Deletion: a project's death is one partition "
                       "drop; delete-by-subject uses the subject tag within a project."),

        Heading(level=2, text="6. Architecture: four planes"),
        Paragraph(text="The plane separation is Atom's latency and cost story."),
        Table(caption="Four planes, their nature, and their contract.",
              header=["Plane", "Nature", "Contents and contract"],
              rows=[
                  ["Hot read", "Synchronous, 100 ms budget", "Retriever (hybrid search, composite scoring, abstention) and Assembler (budgets, render-as-data). No LLM imports, enforced by CI. Fails open."],
                  ["Ingest", "Fire-and-forget", "Traces and outcome events through a Postgres SKIP LOCKED queue to the trace writer. Nothing awaited by the agent runtime."],
                  ["Background", "Batch workers", "Extractors (parsers), distiller, scorer, shadow validator, consolidator, invalidator, prefix builder, GC. Pinned small local models. Cost scales with vault size, not traffic."],
                  ["Control", "Continuous", "Dashboard, review queue, spend ledger with hard caps, kill switch with holdout sampling."],
              ]),
        DIAGRAM_ARCH,
        Paragraph(text="Stores: Postgres + pgvector partitioned by project, Valkey "
                       "for working memory and the tool cache, the trace archive "
                       "behind fs or S3 drivers, and a Postgres queue by default."),

        Heading(level=2, text="7. Hot path: retrieval and assembly"),
        N("Scope resolve — project, agent-type, optional user, optional "
          "workflow-template from the run context.",
          "Static prefix attach — the prebuilt block for this agent-type (pinned "
          "preferences + top validated lessons); no search, so prompt caching applies.",
          "Dynamic retrieve — hybrid search (pgvector ANN + lexical) over semantic "
          "facts and episodic exemplars in the resolved scopes, fused with RRF.",
          "Composite score — 0.40 similarity + 0.30 usefulness_Q + 0.15 recency + "
          "0.15 validity (starting weights, per-project tunable).",
          "Abstention gate — inject nothing unless the top candidate clears the "
          "threshold and the rarity gate (≥2 rare shared terms).",
          "Budget fill — descending score into per-type budgets, deduplicated "
          "against content already in context.",
          "Render as data — one fenced block headed 'MEMORY (recalled data, verify "
          "against current state)', never imperative phrasing; enforced by tests."),
        Callout(style="warning",
                text="Hard 100 ms p99 timeout on retrieval; on a miss, return the "
                     "static prefix or nothing, log, and continue. Workflow "
                     "prefetch: the orchestrator knows the next agent before it "
                     "starts, so its memory is fetched in parallel with the current "
                     "step. Tool cache and working memory are direct Valkey reads."),

        Heading(level=2, text="8. Write path and gates"),
        Paragraph(text="Everything writes to the trace first. Memory is derived, "
                       "never directly written by run content. Status transitions "
                       "happen only through the state machine; there is no admin "
                       "bypass in code."),
        DIAGRAM_LIFECYCLE,
        Table(caption="The write-path state machine.",
              header=["From", "Event", "To"],
              rows=[
                  ["(run)", "async trace write", "Trace"],
                  ["Trace", "parser (operational) / distiller (content)", "Extracted"],
                  ["Extracted", "Tier A: structured execution origin", "Candidate (injectable, capped, labeled)"],
                  ["Extracted", "Tier B: content-derived", "Quarantined (never injected)"],
                  ["Quarantined", "shadow confirmed / human verdict / 2-trace corroboration", "Candidate"],
                  ["Candidate", "promotion predicate met", "Validated"],
                  ["Validated", "contradiction with stronger provenance", "Superseded (link kept)"],
                  ["Validated", "invalidation event / TTL / failed revalidation", "Stale"],
                  ["Stale", "re-verification passes", "Validated"],
                  ["Stale", "two independent failures", "Retired"],
                  ["Validated", "decay floor reached", "Archived"],
              ]),
        B("Tier A (operational) derives from structured execution metadata with low "
          "poisoning surface; injectable immediately as candidates, capped at one "
          "note per run and labeled lower-trust.",
          "Tier B is anything a model distilled from run content, adversarial by "
          "construction on Atom; it lands quarantined and is never injected while quarantined.",
          "Shadow validation watches subsequent matching runs without injecting the "
          "lesson; two consistent confirmations across distinct runs promote it (one "
          "for failure lessons). Human-verdict provenance and two-trace corroboration skip it.",
          "Gates at extraction: novelty gate, schema check, secret scan, "
          "injection-pattern scan (imperative phrasing rejected at the door), provenance completeness.",
          "Contradictions never last-write-wins: a new fact supersedes only with "
          "equal-or-stronger provenance, else it quarantines and surfaces on the review queue."),

        Heading(level=2, text="9. Learning system"),
        Paragraph(text="Credit assignment starts at the injection log. Every run "
                       "records exactly which memories were injected; outcome events "
                       "join traces by run_id; the scorer walks outcome to trace to "
                       "injection log to memories."),
        Code(language="text", code=(
            "Q  <-  clamp01( Q + alpha * c * (r - Q) )\n\n"
            "  alpha = 0.30   learning rate\n"
            "  c in [0,1]     contribution, judged by a pinned model reading the trace\n"
            "  r              reward from the feedback adapter\n"
            "  <= 1 update / memory / day ; validated memories start at Q = 0.50\n"
            "  only unambiguous outcomes score"
        )),
        Table(caption="Feedback adapters and their signal weights.",
              header=["Adapter", "Example", "Weight"],
              rows=[
                  ["Explicit verdict", "Analyst approve/reject with reasoning (the SOC project)", "1.0"],
                  ["Correction", "Human edits the output before use; the diff is the signal", "0.8"],
                  ["Downstream event", "Ticket closed resolved, case reopened, alert re-fired", "0.3"],
                  ["Implicit behavior", "Regenerated, abandoned, rephrased and retried", "0.0 — logged, never scored"],
                  ["Silence", "No adapter configured", "Quality lane off; operational lane still runs"],
              ]),
        Paragraph(text="Workflow credit rule: feedback scores the level it was "
                       "given. An end-of-workflow verdict scores workflow-template "
                       "memories only; per-agent scoring requires the per-step "
                       "signals the operational lane provides. Per-agent blame is "
                       "never guessed from an end result. Earn-your-context kill "
                       "switch: 5% of runs execute memory-off per agent-type; "
                       "sustained negative lift over 14 days auto-disables that "
                       "memory type and notifies the developer with the evidence."),

        Heading(level=2, text="10. Staleness and forgetting"),
        N("Bi-temporal validity on semantic facts; supersede, never silently "
          "overwrite; invalidate, never delete.",
          "Event-driven invalidation — platform events (tool changed, environment "
          "fact updated, workflow edited) invalidate dependents through provenance "
          "selectors: a targeted query, not a scan.",
          "TTL classes for perishable facts: intel-derived in days, environment in "
          "months, user facts until superseded.",
          "Usage-triggered revalidation — any validated memory older than R days "
          "(default 30) that gets retrieved is re-verified asynchronously after the run.",
          "Decay and archive — unused memories decay 5% per idle week and archive at "
          "the floor, recoverable but out of retrieval; the vault plateaus.",
          "Two-strike retirement — a memory failing re-verification twice, "
          "independently, retires; one failure flags it."),

        Heading(level=2, text="11. Workflow memory"),
        B("Run blackboard — shared state for one workflow run: hand-off payloads by "
          "reference, hypotheses, decisions. Rows in Postgres "
          "(run_id, branch_id, author_agent, key, value_ref, status). Parallel spawns "
          "get logical branches the orchestrator validates and merges transactionally; "
          "an agent cannot overwrite another's committed keys.",
          "Routing records — for router-decided handoffs, each run appends its input "
          "signature (hashed features + an embedding of the free-text head), path "
          "taken, per-step outcomes, and final outcome. The router retrieves similar "
          "past records as data; its own defined logic decides. Stored at "
          "workflow-template scope.",
          "In-run handoffs are dataflow, not memory — the orchestrator's job. The "
          "blackboard earns its place only for large-payload reference passing and "
          "parallel-branch coordination. Dynamic paths make the trace store "
          "mandatory: no trace, no path, no credit assignment."),

        Heading(level=2, text="12. Storage and stack (license-verified, July 2026)"),
        Table(caption="Default stack, opt-in alternatives, and when to switch.",
              header=["Slot", "Default (license)", "Opt-in", "Switch when"],
              rows=[
                  ["Relational + vector + temporal", "Postgres 16/17 + pgvector (PostgreSQL)", "Qdrant driver (Apache 2.0)", "pgvector p95 over 200 ms under load"],
                  ["Lexical search", "Native Postgres FTS, ts_rank", "ParadeDB pg_search, BM25 (AGPL-3.0)", "Customer allows AGPL and lexical quality is the proven bottleneck"],
                  ["Working memory + cache", "Valkey (BSD-3, Linux Foundation)", "Redis 8 (AGPL option)", "Customer preference"],
                  ["Trace archive", "Filesystem; SeaweedFS for S3 (Apache 2.0)", "MinIO (AGPL-3.0)", "Customer preference"],
                  ["Graph layer", "None; deferred until earned", "Apache AGE (Apache 2.0, in-Postgres)", "Multi-hop query volume demonstrates need"],
                  ["Queue / workers", "Postgres SKIP LOCKED", "NATS (Apache 2.0)", "Throughput outgrows the DB queue"],
                  ["Worker models", "Pinned small open-weight, local (vLLM)", "—", "Pinning is mandatory for score comparability"],
              ]),
        Paragraph(text="Ruled out with reasons: Kuzu (repo archived October 2025, "
                       "company acquired). FalkorDB (SSPLv1; the service clause is a "
                       "direct hit for an AaaS platform). Memgraph and ArangoDB (BSL, "
                       "not open source). Neo4j Community (GPLv3 with clustering and "
                       "RBAC withheld). Jena (Apache and mature, but RDF/SPARQL with "
                       "JVM ops and no native vectors)."),
        Heading(level=3, text="Data model sketch (partitioned by project_id)"),
        Code(language="sql", code=(
            "memory_item(id, project_id, scope_type, scope_id, mem_type, kind, lane,\n"
            "  trust_tier, status, content, embedding vector, lexemes tsvector,\n"
            "  subject_tag, q_value, confidence, valid_from, valid_to, created_at,\n"
            "  expired_at, provenance jsonb, schema_version)\n"
            "memory_link(src_id, dst_id, relation)   -- supersedes|derived_from|contradicts|related\n"
            "trace_index(run_id, project_id, agent_type_id, workflow_template_id,\n"
            "  path jsonb, started_at, ended_at, payload_ref, outcome_status)\n"
            "outcome_event(run_id, adapter, weight, payload jsonb, arrived_at)\n"
            "injection_log(run_id, memory_id, tokens, slot)   -- the credit-assignment join\n"
            "blackboard_entry(run_id, branch_id, author_agent, key, value_ref, status)\n"
            "invalidation_event(event_type, selector jsonb, fired_at)\n"
            "spend_ledger(project_id, day, worker, model, tokens, cost)  -- rollup to org"
        )),

        Heading(level=2, text="13. Security, privacy, compliance"),
        Paragraph(text="Threat model in one line: Atom's inputs are adversarial by "
                       "construction — alert bodies, user text, and tool payloads "
                       "carry attacker-controlled strings — and published attacks "
                       "show memory is a real surface: MINJA reports over 95% "
                       "injection success via queries alone; AgentPoison reaches its "
                       "target roughly 62% of the time with under 1% benign "
                       "degradation; MPBench shows aggressive auto-write widens "
                       "exploitability."),
        B("Tier B quarantine with shadow validation — nothing content-derived is injected unvetted.",
          "Injection-pattern and secret scans before storage; imperative phrasing rejected at the door.",
          "Render-as-data with non-imperative phrasing, enforced by tests.",
          "Promotion requires outcome evidence, so a poisoned note must survive contact with reality before it spreads.",
          "Blast radius capped at the project wall; provenance everywhere for forensics; a review queue for contradictions and flags."),
        Paragraph(text="Privacy: subject tags enable delete-by-subject within a "
                       "project; project deletion is a partition drop; traces carry "
                       "per-project retention policies; worker models run locally so "
                       "run content never leaves the deployment."),

        Heading(level=2, text="14. Provisional numbers, and the metric that corrects each"),
        Paragraph(text="Philosophy: set defensible defaults, instrument everything, "
                       "let the dashboard falsify them. None of these are architecture."),
        Table(caption="Provisional tunables; each is corrected by a live metric.",
              header=["Parameter", "Initial", "Corrected by"],
              rows=[
                  ["Retrieval timeout", "100 ms p99, fail open", "Latency histogram, miss rate"],
                  ["Memory envelope per run", "1,200 tokens", "Lift-vs-tokens curve per agent-type"],
                  ["Static prefix budget", "up to 700 tokens", "Prefix cache hit rate, lift"],
                  ["Dynamic slice budget", "up to 500 tokens", "Abstention rate, lift"],
                  ["Abstention target", "≥50% of runs inject zero dynamic tokens", "Negative probes, lift"],
                  ["Score weights", "0.40 sim, 0.30 Q, 0.15 recency, 0.15 validity", "Per-project tuning vs outcome quality"],
                  ["Q update", "alpha 0.3, clamp [0,1], 1 update/memory/day", "Q-trajectory stability"],
                  ["Retirement", "Q < 0.25 after ≥4 scored uses", "Stale-retrieval rate"],
                  ["Shadow confirmations", "2 distinct runs (1 for failure lessons)", "Time in quarantine, red-team results"],
                  ["Decay", "5% per idle week; archive at floor 0.15", "Vault size trend (must plateau)"],
                  ["Revalidation age R", "30 days, on retrieval, async", "Stale-retrieval rate"],
                  ["Kill-switch holdout", "5% of runs memory-off", "Lift confidence intervals"],
                  ["Auto-disable trigger", "negative lift sustained 14 days", "Developer overrides, re-enable outcomes"],
                  ["LLM spend", "hard daily cap per project", "Spend ledger"],
              ]),

        Heading(level=2, text="15. Observability and control"),
        Paragraph(text="The dashboard is a deliverable, not a nice-to-have. Views: "
                       "Injections (per run: what, why, score, token cost), Q "
                       "evolution, Consolidation log (every merge and supersede as a "
                       "red/green diff), Staleness (stale-retrieval rate, "
                       "revalidation outcomes), Abstention, Vault size trend per "
                       "project (must plateau), Lift (memory-on vs holdout — the "
                       "kill-switch view), Spend vs caps, and the Review queue "
                       "(quarantine, contradictions, flags). Edit operations on any "
                       "memory: view with provenance drill-down to the raw trace, "
                       "pin, delete (subject-tag aware), merge, correct. A human "
                       "correction writes at weight 1.0 and supersedes."),

        Heading(level=2, text="16. Developer surface"),
        Paragraph(text="At agent creation, memory is a per-type opt-in "
                       "configuration. Empty is a valid and respectable profile. The "
                       "auto-fill and suggestion layer is an open product decision; "
                       "the declared surface below is the subset both options share, "
                       "so the build does not block on it."),
        Code(language="yaml", code=(
            "memory:\n"
            "  mode: static_control        # static_control (default) | agent_control | both\n"
            "  working:    { enabled: true, lifetime: run, offload_threshold_tokens: 20000 }\n"
            "  tool_cache: { enabled: true, ttl_class: intel }\n"
            "  episodic:   { enabled: false }\n"
            "  semantic:   { enabled: true, scope: project_shared }\n"
            "  lessons:    { operational: true, quality: auto }   # auto = on iff adapter wired\n"
            "  preferences:{ enabled: false }\n"
            "  derived_state: { enabled: false }\n"
            "feedback:\n"
            "  adapters: [verdict]         # verdict | correction | downstream | none\n"
            "budget:\n"
            "  total_tokens: 1200"
        )),
        Paragraph(text="Suggested defaults per archetype: one-shot pipeline agents — "
                       "working + tool cache + read-only project semantic + "
                       "operational lessons. Chatbots — working (session) + episodic "
                       "+ semantic (user) + preferences. Enrichment agents — tool "
                       "cache first, entity verdicts, operational lessons. Watchdogs "
                       "— derived state + operational lessons. Workflow members — "
                       "working (blackboard) + their agent-type memory; pure "
                       "transforms carry nothing. Decision agents — semantic + "
                       "lessons with the policy-suggestion channel. Adaptive "
                       "competitive agents (pricing, negotiation) — learning off by "
                       "default; enabling it requires explicit opt-in acknowledging "
                       "the causality caveat."),

        Heading(level=2, text="17. Evaluation harnesses"),
        B("Negative probes — deterministic irrelevant prompts per project fixture; required zero dynamic injections. CI-blocking.",
          "Latency bench — retrieval p50/p99 on a 100k-item fixture; p99 under 100 ms. CI-blocking.",
          "Hot-path purity — a test that fails if any LLM client is importable from hot-path modules. CI-blocking.",
          "Render-as-data — property tests for non-imperative phrasing, labeling, budget compliance.",
          "Poisoning red-team — seeded MINJA-style traces; nothing reaches validated status without outcome evidence.",
          "Staleness injection — flip environment facts and tool definitions in fixtures; dependents go stale; two strikes retire.",
          "Guessed-reward test — ambiguous outcomes produce zero Q updates.",
          "Lift A/B — a SOC-shaped simulated project measuring memory-on vs off end-task quality; the gate for the quality lane.",
          "Ledger audit — worker spend reconciles with the ledger; caps enforce."),

        Heading(level=2, text="18. Build phases with gates"),
        Table(caption="Five phases; each ends at a gate and a human checkpoint.",
              header=["Phase", "Delivers", "Gate"],
              rows=[
                  ["0 · Trace substrate + SDK", "TraceStore (fs + S3), trace schema, outcome joins, config, injection_log, ledger skeleton, leak + license CI", "Complete queryable traces; outcomes attach days later; zero hot-path impact"],
                  ["1 · Hot path", "Working mem + cache, semantic + episodic, hybrid retrieval + scoring + abstention, assembler, fail-open, Injections view, holdout", "Latency bench green; negative probes zero; purity green"],
                  ["2 · Operational lane + staleness", "Parsers, derived state, consolidator, invalidator, prefix builder + caching", "Synthetic failures produce lessons; 30-day soak shows vault plateau; staleness harness green"],
                  ["3 · Quality lane + learning", "Feedback adapters, scorer + Q, shadow validator, promotion gates, review queue, kill switch acting, lift dashboard", "Positive lift on the SOC-shaped sim; guessed-reward green; red-team green"],
                  ["4 · Workflow memory + polish", "Blackboard with branches, routing, prefetch, preference pinning, agent_control, Qdrant/AGE hooks, docs", "Contention tests green; end-only-feedback credit rule verified"],
              ]),
        Paragraph(text="Each phase ends with a human checkpoint: metrics presented "
                       "against the gate, explicit approval before the next phase. "
                       "Encoded in CLAUDE_CODE_PROMPT.md."),

        Heading(level=2, text="19. Deliberately out of scope"),
        B("Cross-project and cross-org memory — does not exist by construction.",
          "The skill service — export hook only.",
          "Any graph layer until earned.",
          "Knowledge-base RAG.",
          "Multi-region replication.",
          "A memory marketplace or commons — explicitly rejected for now."),
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

    render(deck, "pptx", out / "Strata_for_Atom_deck.pptx", THEME)
    render(deck, "html", out / "Strata_for_Atom_deck.html", THEME)
    render(spec, "docx", out / "Strata_for_Atom_spec.docx", THEME)
    render(spec, "html", out / "Strata_for_Atom_spec.html", THEME)

    svg = render_diagram(DIAGRAM_ARCH, THEME, fmt="svg")
    (out / "Strata_for_Atom_architecture.svg").write_text(svg, encoding="utf-8")
    png = render_diagram(DIAGRAM_ARCH, THEME, fmt="png")
    if png:
        (out / "Strata_for_Atom_architecture.png").write_bytes(png)
        print("wrote Strata_for_Atom_architecture.png")
    else:
        print("!! PNG rasterization returned None")
    drawio = render_diagram(DIAGRAM_ARCH, THEME, fmt="drawio")
    (out / "Strata_for_Atom_architecture.drawio").write_text(drawio, encoding="utf-8")

    deck.save(out / "Strata_for_Atom_deck.json")
    spec.save(out / "Strata_for_Atom_spec.json")
    (out / "Strata_for_Atom_architecture.json").write_text(
        DIAGRAM_ARCH.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    print("\nAll deliverables written to", out)


if __name__ == "__main__":
    main()
