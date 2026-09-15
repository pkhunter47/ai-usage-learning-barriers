"""Privacy-safe composite-score screening analysis for the study model.

This is not an exact PLS-SEM reproduction. It needs a de-identified input CSV and
never writes row-level participant data to the output directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED = ("constraints", "frustration", "tool_switching", "learning_effectiveness", "prompting_skill")


def cronbach_alpha(frame: pd.DataFrame) -> float:
    values = frame.to_numpy(dtype=float)
    item_variance = values.var(axis=0, ddof=1).sum()
    total_variance = values.sum(axis=1).var(ddof=1)
    count = values.shape[1]
    return float(count / (count - 1) * (1 - item_variance / total_variance))


def mean_abs_off_diagonal(correlation: np.ndarray) -> float:
    if correlation.shape[0] < 2:
        return float("nan")
    return float(np.abs(correlation[np.triu_indices_from(correlation, k=1)]).mean())


def htmt(frame: pd.DataFrame, left: list[str], right: list[str]) -> float:
    cross = frame[left + right].corr().loc[left, right].abs().to_numpy().mean()
    left_mono = mean_abs_off_diagonal(frame[left].corr().to_numpy())
    right_mono = mean_abs_off_diagonal(frame[right].corr().to_numpy())
    return float(cross / np.sqrt(left_mono * right_mono))


def zscore(series: pd.Series) -> pd.Series:
    return (series - series.mean()) / series.std(ddof=1)


def coefficient(data: pd.DataFrame, outcome: str, predictors: list[str], target: str) -> float:
    design = np.column_stack([np.ones(len(data)), data[predictors].to_numpy(dtype=float)])
    estimates, *_ = np.linalg.lstsq(design, data[outcome].to_numpy(dtype=float), rcond=None)
    return float(estimates[1 + predictors.index(target)])


def path_estimates(scores: pd.DataFrame) -> dict[str, float]:
    data = scores.copy()
    data["constraints_x_prompting"] = data["constraints"] * data["prompting_skill"]
    a = coefficient(data, "frustration", ["constraints", "prompting_skill", "constraints_x_prompting"], "constraints")
    moderation = coefficient(data, "frustration", ["constraints", "prompting_skill", "constraints_x_prompting"], "constraints_x_prompting")
    b = coefficient(data, "tool_switching", ["frustration"], "frustration")
    c = coefficient(data, "learning_effectiveness", ["tool_switching"], "tool_switching")
    return {"constraints_to_frustration": a, "prompting_moderation": moderation, "frustration_to_switching": b, "switching_to_learning": c, "sequential_indirect": a * b * c}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    constructs = config["constructs"]
    missing_constructs = sorted(set(REQUIRED) - set(constructs))
    if missing_constructs:
        raise ValueError(f"Missing constructs in config: {missing_constructs}")
    columns = [item for name in REQUIRED for item in constructs[name]]
    raw = pd.read_csv(args.csv)
    missing_columns = sorted(set(columns) - set(raw.columns))
    if missing_columns:
        raise ValueError(f"CSV is missing configured columns: {missing_columns}")
    frame = raw[columns].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < 30:
        raise ValueError("Fewer than 30 complete responses remain after numeric conversion/dropna")

    scores = pd.DataFrame({name: zscore(frame[items].mean(axis=1)) for name, items in constructs.items()})
    reliability = {name: cronbach_alpha(frame[items]) for name, items in constructs.items()}
    htmt_rows = []
    names = list(constructs)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            htmt_rows.append({"construct_a": left, "construct_b": right, "htmt": htmt(frame, constructs[left], constructs[right])})

    estimates = path_estimates(scores)
    rng = np.random.default_rng(int(config.get("seed", 47)))
    samples = int(config.get("bootstrap_samples", 10000))
    indirect = np.empty(samples)
    for index in range(samples):
        positions = rng.integers(0, len(scores), len(scores))
        indirect[index] = path_estimates(scores.iloc[positions].reset_index(drop=True))["sequential_indirect"]
    estimates["sequential_indirect_ci_2.5"] = float(np.percentile(indirect, 2.5))
    estimates["sequential_indirect_ci_97.5"] = float(np.percentile(indirect, 97.5))
    estimates["complete_responses"] = int(len(frame))

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps({"reliability": reliability, "paths": estimates}, indent=2) + "\n")
    pd.DataFrame(htmt_rows).to_csv(args.output / "htmt.csv", index=False)
    print(json.dumps(estimates, indent=2))


if __name__ == "__main__":
    main()
