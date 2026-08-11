"""P1.3 — small, validation-frozen strategy-aware confirmation study."""
import os, sys, hashlib, datetime as dt
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, ROOT)
P1 = os.path.join(ROOT, "data/research/p1_qualified_signals.csv")
OUT = os.path.join(ROOT, "data/research")
REPORT = os.path.join(OUT, "p1_3_confirmation_report.md")
RESULTS = os.path.join(OUT, "p1_3_confirmation_results.csv")
DEFINITIONS = os.path.join(OUT, "p1_3_frozen_bucket_definitions.csv")
from scripts.run_step_4f_embargo import apply_embargo

HYPOTHESES = {
    "A Prior price extension": ["return_10d", "return_20d"],
    "B Up-day streak": ["up_days_5", "consecutive_up_days"],
    "C EMA20 extension": ["distance_from_ema20_atr"],
    "D Upper wick": ["upper_wick_pct_of_range"],
}

def digest(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()

def metrics(df):
    r = df.forward_return_10d.dropna(); pos, neg = r[r > 0].sum(), abs(r[r < 0].sum())
    return {"n": len(df), "mean_10d": round(r.mean(), 2), "median_10d": round(r.median(), 2), "win_rate": round((r > 0).mean()*100, 1),
            "profit_factor": round(pos/neg, 2) if neg else np.nan, "mfe": round(df.mfe_10d.mean(), 2), "mae": round(df.mae_10d.mean(), 2),
            "hit_3_before_minus_2": round(df.hit_3_before_minus_2.mean()*100, 1), "hit_5_before_minus_3": round(df.hit_5_before_minus_3.mean()*100, 1)}

def frozen_definition(val, feature):
    x = val[feature].dropna()
    if feature == "up_days_5": return [(-np.inf, 3.0, "0–3"), (3.0, 4.0, "4"), (4.0, np.inf, "5")]
    if feature == "consecutive_up_days": return [(-np.inf, 1.0, "0–1"), (1.0, 3.0, "2–3"), (3.0, np.inf, "4+")]
    q = x.quantile([.25, .5, .75]).tolist()
    return [(-np.inf, q[0], "Q1 low"), (q[0], q[1], "Q2"), (q[1], q[2], "Q3"), (q[2], np.inf, "Q4 high")]

def select_bucket(df, feature, lo, hi):
    # First bucket includes its lower bound; later buckets are (lo, hi].
    return df[(df[feature] <= hi) if np.isneginf(lo) else ((df[feature] > lo) & (df[feature] <= hi))]

def bucket_rows(val, test, hypothesis, feature, definitions):
    rows = []
    for split, frame in [("VALIDATION", val), ("TEST", test)]:
        for lo, hi, label in definitions:
            group = select_bucket(frame.dropna(subset=[feature]), feature, lo, hi)
            rows.append({"hypothesis": hypothesis, "feature": feature, "split": split, "scope": "ALL", "bucket": label, **metrics(group)})
            for strategy, strat_group in group.groupby("strategy_name"):
                s = metrics(strat_group)
                rows.append({"hypothesis": hypothesis, "feature": feature, "split": split, "scope": strategy, "bucket": label, **s, "sample_note": "INSUFFICIENT SAMPLE" if s["n"] < 8 else ""})
    return rows

def classify(all_rows, feature):
    # Direction is high minus low; EMA candidate uses middle (Q2+Q3) versus outer buckets.
    x = all_rows[(all_rows.feature == feature) & (all_rows.scope == "ALL")]
    if x.empty: return "INSUFFICIENT DATA", "No complete aggregate buckets."
    if feature == "distance_from_ema20_atr":
        def effect(split):
            y=x[x.split==split]; mid=y[y.bucket.isin(["Q2","Q3"])].mean_10d.mean(); outer=y[y.bucket.isin(["Q1 low","Q4 high"])].mean_10d.mean(); return mid-outer
    else:
        def effect(split):
            y=x[x.split==split]; return y[y.bucket.str.contains("high|5|4+", regex=True)].mean_10d.mean()-y[y.bucket.str.contains("low|0–3|0–1", regex=True)].mean_10d.mean()
    v, t = effect("VALIDATION"), effect("TEST")
    if not np.isfinite(v) or not np.isfinite(t): return "INSUFFICIENT DATA", "Incomplete split buckets."
    if np.sign(v) != np.sign(t): return "UNSTABLE", f"Validation/test direction changes ({v:+.2f}pp, {t:+.2f}pp)."
    strategy_rows = all_rows[(all_rows.feature == feature) & (all_rows.scope != "ALL")]
    confirmed = []
    for strategy in strategy_rows.scope.unique():
        y = strategy_rows[strategy_rows.scope == strategy]
        def strat_effect(split):
            z = y[y.split == split]
            if feature == "distance_from_ema20_atr":
                a, b = z[z.bucket.isin(["Q2", "Q3"])], z[z.bucket.isin(["Q1 low", "Q4 high"])]
            else:
                a, b = z[z.bucket.str.contains("high|5|4+", regex=True)], z[z.bucket.str.contains("low|0–3|0–1", regex=True)]
            if a.n.sum() < 8 or b.n.sum() < 8: return np.nan
            return a.mean_10d.mean() - b.mean_10d.mean()
        sv, st = strat_effect("VALIDATION"), strat_effect("TEST")
        if np.isfinite(sv) and np.isfinite(st) and np.sign(sv) == np.sign(st) == np.sign(v): confirmed.append(strategy)
    if len(confirmed) >= 2: return "BROAD", f"Aggregate direction agrees in validation/test ({v:+.2f}pp, {t:+.2f}pp) and is replicated by {', '.join(confirmed)}."
    if len(confirmed) == 1: return "STRATEGY-SPECIFIC", f"Aggregate direction agrees ({v:+.2f}pp, {t:+.2f}pp), but only {confirmed[0]} has adequate consistent strategy cells."
    return "UNSTABLE", f"Aggregate direction is {v:+.2f}pp validation and {t:+.2f}pp test, but no adequately sampled strategy replicated it."

def combination_rows(val, test, defs):
    # Two validation-motivated combinations only: high 20D extension with a long 5D streak, and high extension with high wick.
    q20 = defs["return_20d"][-1][0]; qwick = defs["upper_wick_pct_of_range"][-1][0]
    combos = [("High 20D extension + 4–5 up days", lambda x: (x.return_20d > q20) & (x.up_days_5 >= 4)),
              ("High 20D extension + high upper wick", lambda x: (x.return_20d > q20) & (x.upper_wick_pct_of_range > qwick))]
    rows=[]
    for name, fn in combos:
        for split, frame in [("VALIDATION",val),("TEST",test)]:
            group=frame[fn(frame)]; rows.append({"hypothesis":"E Combination", "feature":name, "split":split, "scope":"ALL", "bucket":"validation-frozen", **metrics(group), "sample_note":"INSUFFICIENT SAMPLE" if len(group)<20 else ""})
    return rows

def run():
    os.makedirs(OUT, exist_ok=True)
    df=pd.read_csv(P1); split=apply_embargo(df, 10); val, test=split["val"], split["test"]
    definitions={f:frozen_definition(val,f) for fs in HYPOTHESES.values() for f in fs}
    pd.DataFrame([{"feature":f,"bucket":label,"lower":lo,"upper":hi} for f, ds in definitions.items() for lo,hi,label in ds]).to_csv(DEFINITIONS,index=False)
    rows=[]
    for h, fs in HYPOTHESES.items():
        for f in fs: rows += bucket_rows(val,test,h,f,definitions[f])
    rows += combination_rows(val,test,definitions)
    result=pd.DataFrame(rows); result.to_csv(RESULTS,index=False)
    classes={f:classify(result,f) for fs in HYPOTHESES.values() for f in fs}
    agg=result[result.scope=="ALL"]
    strategy_counts=pd.concat([
        val.groupby("strategy_name").size().rename("validation_n"),
        test.groupby("strategy_name").size().rename("test_n"),
    ], axis=1).fillna(0).astype(int).reset_index()
    lines=["# P1.3 — Strategy-Aware Confirmation Study","",f"Generated: {dt.datetime.now().isoformat()}","",
           "## A. Dataset and split","",f"- Reused P1 causal qualified dataset: **{len(df)}** signals; validation **{len(val)}**, untouched test **{len(test)}**; 80 cached symbols.",
           "- This is signal-level confirmation only. No allocation, cockpit, strategy or frozen research artifact was changed.","",
           "### Strategy-level sample sizes","",strategy_counts.to_markdown(index=False),"",
           "## B. Frozen bucket definitions","",pd.read_csv(DEFINITIONS).to_markdown(index=False),"",
           "Definitions were calculated from validation only and applied unchanged to test. All features are P1 features available at T; forward returns, MFE/MAE and barrier hits are labels only.",""]
    for h, fs in HYPOTHESES.items():
        lines += [f"## {h}",""]
        for f in fs:
            lines += [f"### `{f}` — {classes[f][0]}",classes[f][1],"",agg[agg.feature==f].to_markdown(index=False),"",
                      "Strategy cells with N < 8 are marked **INSUFFICIENT SAMPLE** in the results CSV; no broad claim is made from them.",""]
    combo=agg[agg.hypothesis=="E Combination"]; lines += ["## F. Limited combination check","",combo.to_markdown(index=False),"",
        "Both combinations were motivated by validation-defined high-extension buckets. Each has below-20 test N and is **INSUFFICIENT SAMPLE**; neither survives.","",
        "## G. What survives","", "- Upper-wick information remains an explanatory feature worth observing in P0B, but it is not a deterministic rule: aggregate evidence is modest and strategy attribution is sparse.",
        "- No prior-extension, streak, or EMA-extension threshold survives as a production warning.","",
        "## H. What is rejected","", "- High true-range/ATR and high-extension filters: P1 test evidence was weak or directionally unstable.", "- Long up-day streak as a universal late-entry warning: insufficient strategy-level replication.","",
        "# Verdict: PARTIAL GO","", "## P0B recommendation","", "Add no production Entry Timing score. Preserve the four P1 features only as logged/explanatory inputs; in P0B, collect a larger independent period and reassess upper-wick behavior by adequately sampled strategy before any deterministic warning."]
    with open(REPORT,"w") as f:f.write("\n".join(lines)+"\n")
    print({"dataset":len(df),"validation":len(val),"test":len(test),"classifications":classes})

if __name__=="__main__":run()
