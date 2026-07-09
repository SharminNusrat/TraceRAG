import requests
import csv
from pathlib import Path
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# API configuration
API_BASE_URL = "http://localhost:8000"
ANALYZE_ENDPOINT = f"{API_BASE_URL}/analyze"

# Project paths (relative to backend directory)
ETOUR_TEST_DIR = Path("eTour_test")
REQ_DIR = ETOUR_TEST_DIR / "req"
CODE_DIR = ETOUR_TEST_DIR / "code"
OUTPUT_CSV = ETOUR_TEST_DIR / "output.csv"


def extract_class_name_from_target_id(target_id: str) -> str:
    """
    Extract class name from target_id.

    Supports:
    - Customer.java
    - path/to/Customer.java
    - Customer.java::Customer
    - Customer.java::Customer::login(String)
    """

    parts = target_id.split("::")

    # CodeMethodPreprocessor identifiers
    if len(parts) >= 2:
        return parts[1]

    # Fallback for old file-level identifiers
    class_name = target_id.split("/")[-1].split("\\")[-1]

    if class_name.endswith(".java"):
        class_name = class_name[:-5]

    return class_name


def call_api() -> dict:
    """
    Call the analyze API endpoint.
    
    Uses DocumentProvider to load all requirement files from the folder.
    DocumentProvider will:
    - Read all UC*.txt files from the requirements folder
    - Create artifacts with identifier as the UC ID (e.g., "UC1", "UC2")
    """
    payload = {
        "source_type": "document",
        "requirements_path": str(REQ_DIR.absolute()),
        "codebase_path": str(CODE_DIR.absolute()),
        "source_preprocessor": "single",
        "target_preprocessor": "method",
        "classifier": "simple",
        "n_results": 12,
        "source_granularity": 0,
        "target_granularity": 1,
        "dependency_expansion_depth": 1,
        "analysis_mode": "project"
    }
    
    try:
        logger.info("Calling API with all requirements folder...")
        response = requests.post(
            ANALYZE_ENDPOINT,
            json=payload,
            timeout=900  # 10 minute timeout (processing all UCs)
        )
        
        if response.status_code == 200:
            logger.info(f"✓ API Response - Status: {response.status_code}")
            return response.json()
        else:
            logger.error(f"✗ API Error - Status: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return {"trace_links": []}
    
    except requests.exceptions.ConnectionError:
        logger.error(f"✗ Connection Error: Cannot reach API at {ANALYZE_ENDPOINT}")
        logger.error("Make sure the API server is running: python main.py")
        return {"trace_links": []}
    except requests.exceptions.Timeout:
        logger.error(f"✗ Timeout: API request took too long (processing might still be happening)")
        return {"trace_links": []}
    except Exception as e:
        logger.error(f"✗ Error: {str(e)}")
        return {"trace_links": []}


def evaluate():
    """
    Main evaluation function.
    
    Process:
    1. Call API with requirement folder path (DocumentProvider loads all UC files)
    2. Extract source_id (UC identifier) and target_id (class name) from response
    3. Save to CSV in gold standard format: UC,ClassName
    """
    logger.info("=" * 60)
    logger.info("TraceRAG Evaluation Script")
    logger.info("=" * 60)
    logger.info("")
    
    # Verify directories exist
    if not REQ_DIR.exists():
        logger.error(f"Requirement directory not found: {REQ_DIR}")
        return
    
    if not CODE_DIR.exists():
        logger.error(f"Code directory not found: {CODE_DIR}")
        return
    
    logger.info(f"Requirements folder: {REQ_DIR.absolute()}")
    logger.info(f"Codebase folder: {CODE_DIR.absolute()}")
    logger.info("")
    
    # Call API once for all requirements (DocumentProvider handles the folder)
    api_response = call_api()
    
    # Extract trace links
    trace_links = api_response.get("trace_links", [])
    logger.info(f"✓ Received {len(trace_links)} trace links from API")
    logger.info("")
    
    # Collect all results
    results = []
    seen = set()

    for link in trace_links:
        source_id = link.get("source_id", "")
        target_id = link.get("target_id", "")

        class_name = extract_class_name_from_target_id(target_id)

        pair = (source_id, class_name)

        if pair in seen:
            continue

        seen.add(pair)

        results.append({
            "uc_id": source_id,
            "class_name": class_name,
            "confidence": link.get("confidence", 0),
            "confidence_level": link.get("confidence_level", ""),
            "target_id": target_id,
            "explanation": link.get("explanation", "")
        })
    
    logger.info("=" * 60)
    logger.info(f"Total trace links found: {len(results)}")
    logger.info(f"Saving results to: {OUTPUT_CSV}")
    logger.info("=" * 60)
    logger.info("")
    
    # Save to CSV in gold standard format: UC,ClassName
    with open(OUTPUT_CSV, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        # Write results in gold standard format (UC, ClassName)
        for result in results:
            writer.writerow([result["uc_id"], result["class_name"]])
    
    logger.info(f"✓ Output saved to {OUTPUT_CSV}")
    
    # Also create a detailed output file for reference
    detailed_output = ETOUR_TEST_DIR / "output_detailed.csv"
    file_exists = detailed_output.exists()

    with open(detailed_output, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=["UC", "ClassName", "Confidence", "ConfidenceLevel", "TargetId"]
        )
        if not file_exists:
            writer.writeheader()
        
        for result in results:
            writer.writerow({
                "UC": result["uc_id"],
                "ClassName": result["class_name"],
                "Confidence": f"{result['confidence']:.2f}",
                "ConfidenceLevel": result["confidence_level"],
                "TargetId": result["target_id"]
            })
    
    logger.info(f"✓ Detailed output saved to {detailed_output}")
    logger.info("")
    logger.info("=" * 60)
    logger.info("Evaluation complete!")
    logger.info("=" * 60)


if __name__ == "__main__":
    evaluate()
