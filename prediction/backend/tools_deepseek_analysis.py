"""Local operator CLI; run from backend/: python tools_deepseek_analysis.py --input file.json --enable-network"""
import argparse
import json
import sys
from app.services.deepseek_analysis import AnalysisError, analyze_with_deepseek


def main():
    parser = argparse.ArgumentParser(description="Explicitly opted-in, local-only football evidence analysis")
    parser.add_argument("--input", required=True, help="Local, manually reviewed JSON file")
    parser.add_argument("--enable-network", action="store_true", help="Permit one DeepSeek request when server env also permits it")
    args = parser.parse_args()
    try:
        with open(args.input, "rb") as f:
            raw = f.read(32001)
        if len(raw) > 32000:
            raise AnalysisError("input file too large")
        data = json.loads(raw)
        print(json.dumps(analyze_with_deepseek(data, enabled=args.enable_network), ensure_ascii=False, indent=2))
    except (AnalysisError, ValueError, OSError) as exc:
        print("analysis unavailable: " + (str(exc) if isinstance(exc, AnalysisError) else "invalid local input"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
