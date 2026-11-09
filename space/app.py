"""Guardrails Azure Policy playground: a Hugging Face Space.

Pick where a resource is created and what it looks like, and see what Azure Policy would do with
it under Guardrails' rules. Runs tools/policy_eval.py on the same JSON files Terraform deploys.
"""

from __future__ import annotations

import gradio as gr

from demo import EXAMPLES, GROUPS, HIERARCHY, evaluate, finops_html, load_example, rules_rows, tags_text, verdict_html

REPO = "https://github.com/omdkpatel810-ops/azure-guardrails"
FIRST_EXAMPLE = next(iter(EXAMPLES))

CSS = """
.verdict { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border-radius: 10px; border: 1px solid; }
.verdict strong { font-size: 1.5rem; line-height: 1.2; }
.verdict.allowed { border-color: #1a7f37; background: color-mix(in srgb, #1a7f37 10%, transparent); }
.verdict.denied { border-color: #cf222e; background: color-mix(in srgb, #cf222e 10%, transparent); }
.verdict.error { border-color: #9a6700; background: color-mix(in srgb, #9a6700 10%, transparent); }
.verdict.allowed strong { color: #1a7f37; }
.verdict.denied strong { color: #cf222e; }
.verdict.error strong { color: #9a6700; }
.dark .verdict.allowed strong { color: #3fb950; }
.dark .verdict.denied strong { color: #ff7b72; }
.dark .verdict.error strong { color: #d29922; }
.gradio-container table, .gradio-container table *,
.gradio-container .virtual-row, .gradio-container .virtual-row * { font-family: var(--font) !important; }
.gradio-container .virtual-row *, .gradio-container table th * {
  word-break: normal !important; overflow-wrap: break-word !important;
}
.gradio-container .virtual-row small { opacity: 0.75; }
"""

INTRO = f"""
# Guardrails: Azure Policy playground

[Guardrails]({REPO}) is a governed Azure landing zone built with Terraform. Its rules keep data in Canada,
make every resource group say who owns it and who pays for it, and stop storage from being opened to
the internet. Choose where a resource is created and what it looks like, and see what Azure Policy would
do with it. No Azure account is needed.
"""

FOOTNOTE = """
This runs the same policy JSON files the Terraform deploys, through a small evaluator that models the
parts of Azure Policy these rules use: case-insensitive comparisons, Indexed mode, and Modify running
before Deny and Audit. It's a model, not Azure itself.
"""


def check(group: str, resource: str, resource_group: str):
    outcome = evaluate(group, resource, resource_group)
    tags = tags_text(outcome)
    return (
        verdict_html(outcome),
        gr.Dataframe(value=outcome.rows, visible=bool(outcome.rows)),
        gr.Code(value=tags, visible=bool(tags)),
    )


def pick(label: str):
    group, resource, resource_group = load_example(label)
    return (group, resource, resource_group, *check(group, resource, resource_group))


def build() -> gr.Blocks:
    with gr.Blocks(title="Guardrails: Azure Policy playground") as demo:
        gr.Markdown(INTRO)
        with gr.Tab("Try a resource"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=5):
                    example = gr.Dropdown(list(EXAMPLES), value=FIRST_EXAMPLE, label="Start from an example")
                    group = gr.Radio([(label, key) for key, label in GROUPS.items()], label="Management group")
                    resource = gr.Code(language="json", label="Resource", lines=10)
                    with gr.Accordion("Its resource group (for tag inheritance)", open=False):
                        resource_group = gr.Code(language="json", label="Resource group", lines=7)
                    run = gr.Button("Check", variant="primary")
                with gr.Column(scale=6):
                    verdict = gr.HTML()
                    results = gr.Dataframe(
                        headers=["Effect", "Rule", "Why"],
                        datatype=["markdown", "markdown", "str"],
                        interactive=False,
                        wrap=True,
                        label="Rules that apply",
                        column_widths=["17%", "37%", "46%"],
                        max_height=900,
                    )
                    tags = gr.Code(language="json", label="Tags after Modify", lines=5, interactive=False)
            gr.Markdown(FOOTNOTE)

        with gr.Tab("The rules"):
            gr.Markdown(f"Policies assigned at one level apply to everything below it.\n\n```\n{HIERARCHY}\n```")
            gr.Dataframe(
                value=rules_rows(),
                headers=["Group", "Assignment", "What it covers", "Effects"],
                interactive=False,
                wrap=True,
                max_height=900,
                column_widths=["14%", "26%", "45%", "15%"],
            )

        with gr.Tab("FinOps report"):
            gr.HTML(finops_html())

        outputs = [verdict, results, tags]
        example.change(pick, example, [group, resource, resource_group, *outputs])
        group.change(check, [group, resource, resource_group], outputs)
        run.click(check, [group, resource, resource_group], outputs)
        demo.load(pick, example, [group, resource, resource_group, *outputs])
    return demo


demo = build()

if __name__ == "__main__":
    demo.launch(css=CSS, theme=gr.themes.Soft(primary_hue="blue"))
