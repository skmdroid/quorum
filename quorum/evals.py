"""Detection eval harness.

Runs the full panel over a labeled corpus of diffs and measures the two things
we can label with confidence:

* **Recall** — of the planted defects, how many did the panel catch? (scored on
  the defect-bearing diffs)
* **False alarms** — how many findings did it raise on the *clean* diffs, where
  the right answer is "nothing"? (scored on the clean diffs)

We deliberately do **not** report a global precision/F1. On a diff that contains
a known defect, a thorough reviewer also flags *other* real issues that the
corpus doesn't label — counting those as false positives would punish good
reviewing and conflate "wrong" with "unlabeled-but-valid". So extra findings on
defect diffs are reported separately, as information, not as errors.

With the deterministic `mock` provider the score never moves, so CI can gate on
`--min-recall` / `--max-clean-fp` with no API key. `--provider claude-cli` (or
any real provider) measures the live panel on the same corpus.

Methodology mirrors metric-driven LLM-eval practice (a golden dataset scored on
detection) — kept dependency-free so the harness ships with the package.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .diff import parse_diff
from .dispatcher import dispatch
from .providers import get_provider
from .synthesizer import synthesize

# The defect categories we benchmark. The "tests" agent only emits a
# "missing coverage" nudge — a process signal, not a defect — so it is
# excluded from scoring.
SCORE_CATEGORIES = ("security", "performance", "correctness", "style")

# A predicted line within this many lines of the labeled line still counts as
# the same defect (models occasionally point one line off).
LINE_TOL = 2


def default_dataset_dir() -> str:
    """`<repo>/evals/dataset` resolved relative to this file (works in a clone/CI)."""
    here = os.path.dirname(os.path.abspath(__file__))   # .../quorum
    return os.path.join(os.path.dirname(here), "evals", "dataset")


@dataclass
class Counts:
    labels: int = 0        # planted defects in scope
    detected: int = 0      # planted defects the panel caught
    clean_fp: int = 0      # findings raised on clean diffs (false alarms)
    defect_extra: int = 0  # extra findings on defect diffs (unlabeled; informational)

    @property
    def recall(self) -> float:
        return self.detected / self.labels if self.labels else 1.0


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _hit(pred_line, label_line) -> bool:
    if label_line is None or pred_line is None:
        return True   # can't disambiguate by line — accept a same-category match
    return abs(pred_line - label_line) <= LINE_TOL


def _match(preds: list, labels: list) -> tuple[int, int]:
    """Greedy 1:1 match within a single category. Returns (detected, unmatched_preds)."""
    matched = set()
    detected = 0
    for lab in labels:
        for i, p in enumerate(preds):
            if i in matched:
                continue
            if _hit(p.line, lab.get("line")):
                matched.add(i)
                detected += 1
                break
    return detected, len(preds) - len(matched)


def load_dataset(dataset_dir: str) -> list:
    with open(os.path.join(dataset_dir, "labels.json"), encoding="utf-8") as fh:
        labels = json.load(fh)
    cases = []
    for fname in sorted(labels):
        with open(os.path.join(dataset_dir, fname), encoding="utf-8") as fh:
            diff_text = fh.read()
        cases.append({
            "name": fname[:-5] if fname.endswith(".diff") else fname,
            "description": labels[fname].get("description", ""),
            "diff_text": diff_text,
            "expected": [e for e in labels[fname].get("expected", [])
                         if e.get("category") in SCORE_CATEGORIES],
        })
    return cases


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
@dataclass
class EvalReport:
    provider: str
    dataset_dir: str
    n_cases: int
    n_defect_cases: int
    n_clean_cases: int
    by_category: dict          # category -> Counts
    overall: Counts
    cases: list = field(default_factory=list)

    def recall(self) -> float:
        return self.overall.recall

    def clean_false_positives(self) -> int:
        return self.overall.clean_fp

    def to_dict(self) -> dict:
        def row(c: Counts) -> dict:
            return {"labels": c.labels, "detected": c.detected,
                    "recall": round(c.recall, 3),
                    "clean_fp": c.clean_fp, "defect_extra": c.defect_extra}
        return {
            "provider": self.provider,
            "dataset": self.dataset_dir,
            "cases": self.n_cases,
            "defect_cases": self.n_defect_cases,
            "clean_cases": self.n_clean_cases,
            "overall": row(self.overall),
            "by_category": {c: row(self.by_category[c]) for c in SCORE_CATEGORIES},
            "case_detail": self.cases,
        }


def evaluate(provider_spec: str = "mock", dataset_dir: str | None = None) -> EvalReport:
    dataset_dir = dataset_dir or default_dataset_dir()
    provider, model_override = get_provider(provider_spec)
    cases = load_dataset(dataset_dir)

    by_cat = {c: Counts() for c in SCORE_CATEGORIES}
    overall = Counts()
    case_detail = []
    n_defect = n_clean = 0

    for case in cases:
        files = parse_diff(case["diff_text"])
        agent_results = dispatch(files, provider, provider.name, model_override)
        result = synthesize(agent_results, provider, model_override)
        preds = [f for f in result.findings if f.category in SCORE_CATEGORIES]
        expected = case["expected"]
        is_clean = not expected
        n_clean += is_clean
        n_defect += not is_clean

        c_det = c_extra = c_cfp = 0
        for cat in SCORE_CATEGORIES:
            cat_preds = [p for p in preds if p.category == cat]
            cat_labels = [e for e in expected if e["category"] == cat]
            detected, unmatched = _match(cat_preds, cat_labels)
            by_cat[cat].labels += len(cat_labels)
            by_cat[cat].detected += detected
            if is_clean:
                by_cat[cat].clean_fp += len(cat_preds)
                c_cfp += len(cat_preds)
            else:
                by_cat[cat].defect_extra += unmatched
                c_extra += unmatched
            c_det += detected

        overall.labels += len(expected)
        overall.detected += c_det
        overall.clean_fp += c_cfp
        overall.defect_extra += c_extra
        case_detail.append({"name": case["name"], "clean": is_clean,
                            "labels": len(expected), "detected": c_det,
                            "clean_fp": c_cfp, "defect_extra": c_extra})

    return EvalReport(provider=provider.name, dataset_dir=dataset_dir,
                      n_cases=len(cases), n_defect_cases=n_defect, n_clean_cases=n_clean,
                      by_category=by_cat, overall=overall, cases=case_detail)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _rows(report: EvalReport) -> list:
    rows = []
    for cat in SCORE_CATEGORIES:
        c = report.by_category[cat]
        rows.append((cat, c.detected, c.labels, c.recall, c.clean_fp))
    o = report.overall
    rows.append(("overall", o.detected, o.labels, o.recall, o.clean_fp))
    return rows


def to_text(report: EvalReport) -> str:
    out = [f"Quorum detection eval — provider: {report.provider}  ·  "
           f"{report.n_cases} cases ({report.n_defect_cases} with planted defects, "
           f"{report.n_clean_cases} clean)",
           "",
           f"{'category':<13}{'detected':>10}{'recall':>9}{'clean-FP':>10}",
           "-" * 42]
    for name, det, lab, rec, cfp in _rows(report):
        if name == "overall":
            out.append("-" * 42)
        out.append(f"{name:<13}{f'{det}/{lab}':>10}{rec:>9.2f}{cfp:>10}")
    out.append("")
    out.append(f"extra findings on defect diffs (unlabeled, may be valid): "
               f"{report.overall.defect_extra}")
    return "\n".join(out)


def to_markdown(report: EvalReport) -> str:
    out = [f"**Quorum detection eval** — provider `{report.provider}`, "
           f"{report.n_defect_cases} defect + {report.n_clean_cases} clean cases",
           "",
           "| Category | Detected | Recall | False alarms (clean) |",
           "|---|--:|--:|--:|"]
    for name, det, lab, rec, cfp in _rows(report):
        label = f"**{name}**" if name == "overall" else name
        out.append(f"| {label} | {det}/{lab} | {rec:.2f} | {cfp} |")
    out.append("")
    out.append(f"_Extra findings on defect diffs (unlabeled, may be valid): "
               f"{report.overall.defect_extra}._")
    return "\n".join(out)
