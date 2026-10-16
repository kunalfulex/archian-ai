from PIL import Image
import pytesseract
import boto3

def extract_text_from_image_with_textract(file_path):
    client = boto3.client("textract", region_name="us-west-1")

    with open(file_path, "rb") as document:
        image_bytes = document.read()

    # Step 1: Structured key-value pairs
    try:
        kv_response = client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["FORMS"]
        )
    except Exception as e:
        kv_response = {"Blocks": []}

    block_map = {block["Id"]: block for block in kv_response.get("Blocks", [])}
    key_value_pairs = {}

    for block in kv_response.get("Blocks", []):
        if block["BlockType"] == "KEY_VALUE_SET" and "KEY" in block.get("EntityTypes", []):
            key_text = ""
            value_text = ""

            for rel in block.get("Relationships", []):
                if rel["Type"] == "CHILD":
                    key_text = " ".join([
                        block_map[child_id]["Text"]
                        for child_id in rel["Ids"]
                        if block_map[child_id].get("Text")
                    ])
                elif rel["Type"] == "VALUE":
                    value_block = block_map.get(rel["Ids"][0])
                    if value_block:
                        for val_rel in value_block.get("Relationships", []):
                            if val_rel["Type"] == "CHILD":
                                value_text = " ".join([
                                    block_map[child_id]["Text"]
                                    for child_id in val_rel["Ids"]
                                    if block_map[child_id].get("Text")
                                ])

            if key_text and value_text:
                key = key_text.strip().replace(":", "").title()
                value = value_text.strip()
                key_value_pairs[key] = value

    # Step 2: Full OCR raw text
    try:
        text_response = client.detect_document_text(Document={"Bytes": image_bytes})
        full_text = " ".join([
            block["Text"] for block in text_response["Blocks"]
            if block["BlockType"] == "LINE"
        ])
    except Exception as e:
        full_text = ""

    return {
        "key_value_data": key_value_pairs,
        "ocr_raw_text": full_text
    }



def extract_text_from_image(file_path):
    """
    Primary function to call from Archian AI.
    Tries Textract first, then falls back to Tesseract if needed.
    Returns the OCR raw text as a string.
    """
    try:
        print(f"🔍 Starting OCR extraction for file: {file_path}")
        result = extract_text_from_image_with_textract(file_path)
        print(f"✅ Textract extraction successful. Result type: {type(result)}")
        
        # Extract the raw text from the result
        if isinstance(result, dict):
            raw_text = result.get("ocr_raw_text", "")
            print(f"📝 Extracted text length: {len(raw_text)} characters")
            return raw_text
        else:
            print(f"⚠️ Unexpected result type from Textract: {type(result)}")
            return str(result)
            
    except Exception as textract_error:
        print(f"❌ Textract failed: {str(textract_error)}")
        print("🔄 Falling back to Tesseract...")
        try:
            tesseract_result = pytesseract.image_to_string(Image.open(file_path))
            print(f"✅ Tesseract extraction successful. Text length: {len(tesseract_result)}")
            return tesseract_result
        except Exception as tesseract_error:
            print(f"❌ Tesseract also failed: {str(tesseract_error)}")
            return ""
