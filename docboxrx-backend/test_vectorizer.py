import json

from app.services.vectorizer import vectorize_email


def main():
    sample = "My tooth hurts and it's bleeding"
    result = vectorize_email(sample)
    print(json.dumps(result, indent=2))
    if result.get("intent_label") == "CLINICAL" and float(result.get("risk_score", 0)) > 0.8:
        print("PASS: intent_label=CLINICAL and risk_score>0.8")
    else:
        print("WARN: Did not meet expected intent/risk thresholds")


if __name__ == "__main__":
    main()
