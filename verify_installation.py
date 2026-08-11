import sys
import importlib

packages = [
    "yfinance",
    "pandas",
    "pandas_ta",
    "bs4",
    "requests",
    "streamlit",
    "pydantic",
]

print(f"Python Version: {sys.version}")
print("-" * 50)

all_passed = True
for pkg in packages:
    try:
        mod = importlib.import_module(pkg)
        version = getattr(mod, "__version__", "Installed (no __version__)")
        print(f"✓ {pkg:<15} : {version}")
    except ImportError as e:
        print(f"✗ {pkg:<15} : FAILED ({e})")
        all_passed = False

if all_passed:
    print("-" * 50)
    print("SUCCESS: All required packages are installed and working properly!")
else:
    print("-" * 50)
    print("ERROR: Some packages failed to import.")
    sys.exit(1)
