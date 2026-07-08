"""Generate a document with Claude and render it to every format.

    pip install "docloom[pdf]" anthropic
    export ANTHROPIC_API_KEY=sk-...   (or `ant auth login`)
    python examples/generate_with_llm.py "A short pitch for an open-source bakery POS"

Works the same with any provider that supports structured output: pass
docloom.llm_schema() as the JSON schema, or the Document model to your SDK's
Pydantic helper (OpenAI .parse(), instructor, pydantic-ai).
"""

import sys

import anthropic

from docloom import AUTHORING_GUIDE, DEFAULT, Document, lint, render

topic = " ".join(sys.argv[1:]) or "Q3 2026 plan for a small SaaS team"
client = anthropic.Anthropic()


def generate(prompt: str) -> Document:
    response = client.messages.parse(
        model="claude-opus-4-8",
        max_tokens=16000,
        system=AUTHORING_GUIDE,
        messages=[{"role": "user", "content": prompt}],
        output_format=Document,
    )
    return response.parsed_output


doc = generate(f"Write a short slide deck and accompanying report about: {topic}")

# The docloom loop: deterministic lint, then let the model fix its own layout
# mistakes with machine-readable findings instead of shipping a broken deck.
findings = lint(doc, DEFAULT)
if findings:
    report = "\n".join(
        f"{f.severity} [{f.rule}] {f.where}: {f.message}" for f in findings
    )
    print(f"lint found {len(findings)} issue(s); asking the model to revise")
    doc = generate(
        "Revise this docloom document so every lint finding below is resolved. "
        "Return the full corrected document.\n\n"
        f"Document:\n{doc.model_dump_json()}\n\nLint findings:\n{report}"
    )

for fmt in ("pptx", "docx", "html", "md", "pdf"):
    print("wrote", render(doc, fmt, out_path=f"out/generated.{fmt}"))
