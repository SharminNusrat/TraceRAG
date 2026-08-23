"""Architecture documentation to architecture model TLR on BigBlueButton.

Scores the recovered links against the ArDoCo gold standard. All of the
translation between our identifiers and theirs happens here - the pipeline
knows nothing about any dataset's naming.

    ours : bigbluebutton_1SentPerLine.txt::sentence_3   bbb.uml$9$_0e5u8Fk...
    gold : bigbluebutton_1SentPerLine$4                 bbb$21$_0e5u8Fk...

Both sides are reduced to what actually identifies the element: the sentence's
position in the file, and the model element's UML id.
"""

import csv
import logging
import re
import requests
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_BASE_URL = "http://localhost:8000"
ANALYZE_ENDPOINT = f"{API_BASE_URL}/analyze"

DATASET_DIR = Path("bigbluebutton")
DOC_DIR = DATASET_DIR / "text_2021"
MODEL_DIR = DATASET_DIR / "model_2021" / "uml"
GOLD_FILE = DATASET_DIR / "goldstandards" / "goldstandard_sad-sam.csv"
OUTPUT_CSV = DATASET_DIR / "output_sad-sam.csv"


def to_gold_sentence(identifier: str) -> str | None:
    """`<file>.txt::sentence_<0-based>` -> `<file>$<1-based>`."""
    match = re.fullmatch(r"(.+?)(?:\.[^.]+)?::sentence_(\d+)", identifier)
    if not match:
        return None
    return f"{match.group(1)}${int(match.group(2)) + 1}"


def to_uml_id(identifier: str) -> str:
    """`<file>$<counter>$<uml id>` -> `<uml id>`.

    The counter depends on how the preprocessor walks the model, which is our
    business and not the gold standard's. The UML id is the element itself.
    """
    return identifier.rsplit("$", 1)[-1]


def load_gold() -> set[tuple[str, str]]:
    pairs = set()
    with open(GOLD_FILE, newline='', encoding='utf-8') as handle:
        for row in csv.reader(handle):
            if len(row) == 2:
                pairs.add((row[0].strip(), to_uml_id(row[1].strip())))
    return pairs


def call_api() -> dict:
    """Documentation sentences as the source, model components as the target."""
    payload = {
        "source": {"kind": "requirements", "path": str(DOC_DIR.absolute())},
        "target": {"kind": "architecture", "path": str(MODEL_DIR.absolute())},
        "source_preprocessor": "sentence",
        "target_preprocessor": "model_uml",
        "source_output_level": "sentence",
        "target_output_level": "component",
        "classifier": "simple",
        # LiSSA uses Top-10 for this task; the model has only ~12 components.
        "n_results": 10,
        "dependency_expansion_depth": 0,
        "analysis_mode": "session",
    }

    try:
        logger.info("Calling API with the architecture documentation and model...")
        response = requests.post(ANALYZE_ENDPOINT, json=payload, timeout=3600)

        if response.status_code == 200:
            logger.info(f"✓ API Response - Status: {response.status_code}")
            return response.json()

        logger.error(f"✗ API Error - Status: {response.status_code}")
        logger.error(f"Response: {response.text}")
        return {"trace_links": []}

    except requests.exceptions.ConnectionError:
        logger.error(f"✗ Connection Error: Cannot reach API at {ANALYZE_ENDPOINT}")
        logger.error("Make sure the API server is running: python main.py")
        return {"trace_links": []}
    except requests.exceptions.Timeout:
        logger.error("✗ Timeout: API request took too long")
        return {"trace_links": []}
    except Exception as e:
        logger.error(f"✗ Error: {str(e)}")
        return {"trace_links": []}


def score(predicted: set, gold: set) -> dict:
    correct = predicted & gold
    precision = len(correct) / len(predicted) if predicted else 0.0
    recall = len(correct) / len(gold) if gold else 0.0

    def f_beta(beta: float) -> float:
        weight = beta ** 2
        denominator = weight * precision + recall
        return (1 + weight) * precision * recall / denominator if denominator else 0.0

    return {
        "predicted": len(predicted),
        "gold": len(gold),
        "correct": len(correct),
        "precision": precision,
        "recall": recall,
        "f1": f_beta(1),
        "f2": f_beta(2),
    }


def evaluate():
    logger.info("=" * 60)
    logger.info("BigBlueButton: architecture documentation -> architecture model")
    logger.info("=" * 60)

    for path in (DOC_DIR, MODEL_DIR, GOLD_FILE):
        if not path.exists():
            logger.error(f"Not found: {path.absolute()}")
            logger.error("Place the ArDoCo BigBlueButton dataset under "
                         f"{DATASET_DIR.absolute()}")
            return

    gold = load_gold()
    logger.info(f"Gold standard: {len(gold)} links")

    response = call_api()
    links = response.get("trace_links", [])
    logger.info(f"✓ Received {len(links)} trace links from API")
    if not links:
        return

    targets = response.get("target_elements", [])
    logger.info(f"  model elements reported: {len(targets)}")

    predicted = set()
    rows = []
    for link in links:
        sentence = to_gold_sentence(link.get("source_id", ""))
        if sentence is None:
            continue
        uml_id = to_uml_id(link.get("target_id", ""))
        predicted.add((sentence, uml_id))
        rows.append({
            "Sentence": sentence,
            "ModelElement": uml_id,
            "Confidence": f"{link.get('confidence', 0):.2f}",
            "ConfidenceLevel": link.get("confidence_level", ""),
            "SourceId": link.get("source_id", ""),
            "TargetId": link.get("target_id", ""),
        })

    result = score(predicted, gold)

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  predicted : {result['predicted']}")
    logger.info(f"  gold      : {result['gold']}")
    logger.info(f"  correct   : {result['correct']}")
    logger.info(f"  precision : {result['precision']:.3f}")
    logger.info(f"  recall    : {result['recall']:.3f}")
    logger.info(f"  F1        : {result['f1']:.3f}")
    logger.info(f"  F2        : {result['f2']:.3f}")
    logger.info("=" * 60)

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        for sentence, uml_id in sorted(predicted):
            writer.writerow([sentence, uml_id])
    logger.info(f"✓ Output saved to {OUTPUT_CSV}")

    detailed = DATASET_DIR / "output_sad-sam_detailed.csv"
    with open(detailed, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "Sentence", "ModelElement", "Confidence", "ConfidenceLevel",
            "SourceId", "TargetId",
        ])
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"✓ Detailed output saved to {detailed}")

    missed = DATASET_DIR / "output_sad-sam_missed.csv"
    with open(missed, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(["Sentence", "ModelElement"])
        writer.writerows(sorted(gold - predicted))
    logger.info(f"✓ Missed links saved to {missed}")


if __name__ == "__main__":
    evaluate()
