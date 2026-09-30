"""Command-line entry point: ``mirobody-gen <command> ...`` (or ``python -m mirobody_gen``).

Each command delegates to the module that implements it; the modules keep their own
``argparse`` parsers so they stay runnable as ``python -m mirobody_gen.<module>`` too.
"""

from __future__ import annotations

import importlib
import sys

COMMANDS: dict[str, tuple[str, str]] = {
    "build": ("mirobody_gen.build", "Generate a corpus: truth layer, files, pairs, devices, journal, genomics"),
    "audit-clinical": ("mirobody_gen.audit.clinical", "Identities, physiological bounds, flags, longitudinal checks"),
    "audit-readability": ("mirobody_gen.audit.readability", "Every printed truth is on the page; --ocr for image tiers"),
    "audit-privacy": ("mirobody_gen.audit.privacy", "Replay detection, PII predicates, name/institution allow-list"),
    "audit-fidelity": ("mirobody_gen.audit.fidelity", "Shape statistics against the reference-set aggregates (report only)"),
    "score": ("mirobody_gen.harness.score", "Score extraction predictions against files.jsonl / pairs.jsonl"),
    "baselines": ("mirobody_gen.harness.baselines", "Rule-based and oracle baselines"),
    "medrep-view": ("mirobody_gen.harness.medrep_view", "Export labels in the MedRepBench format"),
    "resolve-coverage": ("mirobody_gen.harness.resolve_coverage", "Vocabulary coverage of the printed names (needs mirobody)"),
    "synthea-check": ("mirobody_gen.harness.synthea_check", "Consistency checks over FHIR bundles (for PySynthea)"),
    "llm-panel": ("mirobody_gen.harness.llm_panel", "Multi-model cross-annotation panel (not a gate)"),
    "paraphrase": ("mirobody_gen.llm.paraphrase", "Offline paraphrase enrichment of narrative templates (dry run by default)"),
    "compare": ("mirobody_gen.harness.compare", "Compare two builds of one seed: wording diversity and truth-layer invariance"),
}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        width = max(len(k) for k in COMMANDS)
        print("usage: mirobody-gen <command> [options]\n\ncommands:")
        for name, (_, help_text) in COMMANDS.items():
            print(f"  {name:<{width}}  {help_text}")
        sys.exit(0 if argv else 2)
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        sys.exit(f"mirobody-gen: unknown command {name!r} (try --help)")
    module = importlib.import_module(COMMANDS[name][0])
    sys.argv = [f"mirobody-gen {name}"] + rest
    module.main()


if __name__ == "__main__":
    main()
