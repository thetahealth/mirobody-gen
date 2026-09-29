"""Optional language-model layer: offline enrichment that never touches the truth.

Language models work after the truth is fixed and before the gates: they may change how a sentence
is phrased or how a page looks, never who, when or what value. Their output is untrusted text and is
audited like any other output (contract checks here, then the privacy gate without any vocabulary
exemption). See docs/zh-CN/llm-integration-2026-09-29.md.
"""
