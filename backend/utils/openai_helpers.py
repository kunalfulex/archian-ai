import json
import re

def parse_openai_json_response(response_text: str) -> dict:
    """
    Extracts and parses JSON from OpenAI text completion response.
    Handles raw JSON or markdown-wrapped blocks with robust error handling.
    """
    try:
        # Try raw JSON first
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Fallback: extract JSON inside markdown code block
        match = re.search(r"```json\n(.*?)\n```", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                # Try to fix common JSON issues
                fixed_json = fix_malformed_json(match.group(1))
                return json.loads(fixed_json)
        
        # Fallback: extract any JSON object (less safe)
        match = re.search(r"{.*}", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                # Try to fix common JSON issues
                fixed_json = fix_malformed_json(match.group(0))
                return json.loads(fixed_json)
        
        raise ValueError("❌ Could not parse structured JSON from AI service response.")


def fix_malformed_json(json_text: str) -> str:
    """
    Attempts to fix common JSON formatting issues from AI service responses.
    """
    try:
        # Remove trailing commas before closing brackets/braces (but keep field separators)
        json_text = re.sub(r',(\s*[}\]])', r'\1', json_text)
        
        # Remove trailing commas at the end of lines (but keep field separators)
        json_text = re.sub(r',(\s*\n\s*[}\]])', r'\1', json_text)
        
        # Fix missing array brackets if it looks like multiple objects
        if json_text.strip().startswith('{') and not json_text.strip().startswith('['):
            # Check if there are multiple objects (multiple opening braces)
            brace_count = json_text.count('{')
            if brace_count > 1:
                # Wrap in array brackets
                json_text = '[' + json_text + ']'
        
        # Remove any trailing commas before closing array brackets
        json_text = re.sub(r',(\s*\])', r'\1', json_text)
        
        # Try to parse to validate
        json.loads(json_text)
        return json_text
        
    except json.JSONDecodeError:
        # If still can't parse, try more aggressive fixes
        try:
            # Split by objects and reconstruct
            objects = re.findall(r'\{[^}]*\}', json_text)
            if len(objects) > 1:
                fixed_json = '[' + ','.join(objects) + ']'
                json.loads(fixed_json)  # Validate
                return fixed_json
        except:
            pass
        
        # Last resort: try to extract just the first valid object
        try:
            first_object = re.search(r'\{[^}]*\}', json_text)
            if first_object:
                return first_object.group(0)
        except:
            pass
        
        # If all else fails, raise the original error
        raise ValueError(f"❌ Could not fix malformed JSON: {json_text[:100]}...")

