import os
import pandas as pd

OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_known_discrepancies.csv")

def create_known_discrepancies_csv():
    discrepancy_rows = [
        {
            "date": "2024-03-31",
            "expected_or_prior_count": 413,
            "current_count": 412,
            "difference": -1,
            "symbol": "ANGELONE",
            "reason": "ANGELONE addition event on 2023-12-07 was identified as a sub-index factor replacement (Nifty500 Quality 50) rather than a parent broad-market Nifty 500 event. Excluding it prevents broad-market state contamination and shifts 2024-03-31 reconstructed state from 413 to 412.",
            "severity": "INFORMATIONAL / INTENTIONAL_FILTER"
        }
    ]

    df_disc = pd.DataFrame(discrepancy_rows)
    df_disc.to_csv(OUT_CSV, index=False)
    print(f"Known Discrepancies CSV created -> {OUT_CSV}")

if __name__ == "__main__":
    create_known_discrepancies_csv()
