# Documentation

Current, authoritative:

- [ARCHITECTURE.md](ARCHITECTURE.md) — layers, modules, invariants, how to extend.
- [PRIVACY.md](PRIVACY.md) — the privacy claim, threat model, repository controls, what CI verifies.
- [SCHEMA.md](SCHEMA.md) — every output file, field by field, with enumerations.

Design notes (Chinese, dated; they record reasoning and measurements at the time of writing):

- [zh-CN/plan.md](zh-CN/plan.md) — the design record: difficulty model, layout diversity mechanism,
  acceptance criteria, staged roadmap and what each stage found.
- [zh-CN/degradation.md](zh-CN/degradation.md) — the image tiers: scene table, calibration against the
  reference-set image statistics, what was adopted from PureDocBench, Augraphy and related work.
- [zh-CN/research-2026-09-29.md](zh-CN/research-2026-09-29.md) — upstream PySynthea verification,
  alignment with mirobody's symptom axis, genomics and device contracts, the check-up report review.
- [zh-CN/research-2026-09-29-puredocbench.md](zh-CN/research-2026-09-29-puredocbench.md) — close reading of
  PureDocBench, OmniDocBench and Synthetic Hospital; item-by-item comparison; the three-layer fusion
  design (structure / values / degradation); pipeline v2 (seven stages, eight gates) and the paper positioning.
- [zh-CN/llm-integration-2026-09-29.md](zh-CN/llm-integration-2026-09-29.md) — where language models fit
  (offline paraphrase enrichment, institution templates, candidate proposals, judging) and where they must not;
  contracts, gates and experiments.
- [zh-CN/numbers.md](zh-CN/numbers.md) — aggregate statistics of the reference set (generated).
- [zh-CN/paper.md](zh-CN/paper.md) — working paper outline.
