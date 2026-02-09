import vertexai
from vertexai.generative_models import GenerativeModel, Part
from google.oauth2 import service_account
import json

# ==========================================
# 0. COST ESTIMATION SETTINGS
# ==========================================

# Prices are per 1M tokens unless noted otherwise. Update as pricing changes:
# https://cloud.google.com/vertex-ai/generative-ai/pricing
MODEL_PRICING_USD_PER_1M = {
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "audio_input": 1.25},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "audio_input": 1.0},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "audio_input": 0.3},
}


def _usage_get(usage_metadata, key_snake, key_camel=None):
    if usage_metadata is None:
        return None
    if isinstance(usage_metadata, dict):
        if key_snake in usage_metadata:
            return usage_metadata[key_snake]
        if key_camel and key_camel in usage_metadata:
            return usage_metadata[key_camel]
        return None
    if hasattr(usage_metadata, key_snake):
        return getattr(usage_metadata, key_snake)
    if key_camel and hasattr(usage_metadata, key_camel):
        return getattr(usage_metadata, key_camel)
    return None


def _normalize_modality(modality):
    if modality is None:
        return None
    if hasattr(modality, "name"):
        modality = modality.name
    elif hasattr(modality, "value"):
        modality = modality.value
    if isinstance(modality, str):
        return modality.strip().lower()
    return None


def _get_model_pricing(model_name):
    if model_name in MODEL_PRICING_USD_PER_1M:
        return MODEL_PRICING_USD_PER_1M[model_name]
    for key, pricing in MODEL_PRICING_USD_PER_1M.items():
        if model_name.startswith(key):
            return pricing
    return None


def estimate_call_cost(usage_metadata, model_name):
    pricing = _get_model_pricing(model_name)
    if not pricing:
        return None

    prompt_tokens = _usage_get(usage_metadata, "prompt_token_count", "promptTokenCount") or 0
    candidates_tokens = _usage_get(
        usage_metadata, "candidates_token_count", "candidatesTokenCount"
    ) or 0
    thoughts_tokens = _usage_get(usage_metadata, "thoughts_token_count", "thoughtsTokenCount") or 0
    total_tokens = _usage_get(usage_metadata, "total_token_count", "totalTokenCount")

    # Try to compute input cost by modality if prompt token breakdown is available.
    prompt_details = _usage_get(usage_metadata, "prompt_tokens_details", "promptTokensDetails")
    input_cost = 0.0
    if prompt_details:
        for detail in prompt_details:
            if isinstance(detail, dict):
                modality = _normalize_modality(detail.get("modality"))
                token_count = detail.get("token_count") or detail.get("tokenCount") or 0
            else:
                modality = _normalize_modality(getattr(detail, "modality", None))
                token_count = getattr(detail, "token_count", 0)
            if not token_count:
                continue
            if modality == "audio":
                rate = pricing.get("audio_input", pricing["input"])
            else:
                rate = pricing["input"]
            input_cost += (token_count / 1_000_000) * rate
    else:
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]

    output_tokens = candidates_tokens + thoughts_tokens
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    estimated_cost = input_cost + output_cost

    return {
        "prompt_tokens": prompt_tokens,
        "candidates_tokens": candidates_tokens,
        "thoughts_tokens": thoughts_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost,
    }

# ==========================================
# 1. SETUP & AUTHENTICATION
# ==========================================

# Path to the JSON key file you created
key_path = '/Users/TAP/Documents/Git/rag_service/rag_service/core/service_account_key.json'

# Load credentials from the JSON file
credentials = service_account.Credentials.from_service_account_file(key_path)

# Extract project ID from the credentials file automatically
with open(key_path) as f:
    project_id = json.load(f)['project_id']

# Initialize Vertex AI with the credentials
# location='us-central1' is standard, but change if your bucket is elsewhere
vertexai.init(project=project_id, location='us-central1', credentials=credentials) 

# ==========================================
# 2. DEFINE DATA & PROMPTS
# ==========================================

# The Video URI (MUST be gs:// format for best performance)


# video_uri = "gs://your-bucket-name/student_art_submission.mp4" 

video_public = "https://storage.googleapis.com/bucket_tap_1/uploads/dance_l2_create/20251007140203_C455110_F32580_M16075921.mp4"
video_uri  = video_public.replace("https://storage.googleapis.com/", "gs://")

# The Assignment Context
assignment_description = """
ASSIGNMENT: "Pendulum"
Task: Create a pendulumm from household items.
The student must record a video showing the work.
"""

# The Grading Rubric
grading_rubric = """
RUBRIC (Total 20 pts):
1. Pendulum Construction (5 pts): Is the pendulum properly constructed using household items?
2. Creativity (5 pts): Does the pendulum show creative use of materials?
3. Video Quality (5 pts): Is the video clear and shows the pendulum in motion?
4. Verbal Explanation (5 pts): Did the student clearly articulate their intent in the video?
"""

# MODEL_NAME = "gemini-2.5-flash"
MODEL_NAME = "gemini-2.5-flash-lite"

# ==========================================
# 3. ANALYSIS LOGIC
# ==========================================

def analyze_student_video():
    print(f"Loading model and analyzing video: {video_uri}...")
    
    # Load the Model (Gemini 1.5 Flash is cost-efficient and fast for this)
    
    model = GenerativeModel(MODEL_NAME)

    # Create the Multimodal Prompt
    # We combine the video file (referenced in Cloud Storage) with our text prompt
    video_part = Part.from_uri(
        uri=video_uri,
        mime_type="video/mp4"
    )

    prompt = f"""
    You are an expert science instructor. 
    
    {assignment_description}
    
    Please analyze the attached video based strictly on the following rubric:
    {grading_rubric}
    
    OUTPUT FORMAT:
    Provide the output in JSON format with the following keys:
    - total_score (integer)
    - breakdown (object with score for each category)
    - feedback_summary (string)
    - specific_evidence (list of strings, citing timestamps from video if applicable)
    """

    # Generate the content
    # temperature=0 ensures the grading is consistent and less "creative"
    response = model.generate_content(
        [video_part, prompt],
        generation_config={"temperature": 0, "response_mime_type": "application/json"}
    )

    return response

# ==========================================
# 4. EXECUTION
# ==========================================

if __name__ == "__main__":
    try:
        response = analyze_student_video()
        print("\n--- GRADING RESULTS ---")
        print(response.text)

        print("\n--- RAW RESPONSE OBJECT ---")
        print(response)

        cost_info = estimate_call_cost(response.usage_metadata, model_name=MODEL_NAME)
        if cost_info:
            print("\n--- ESTIMATED API COST ---")
            print(
                f"Prompt tokens: {cost_info['prompt_tokens']}, "
                f"Output tokens: {cost_info['candidates_tokens']}, "
                f"Thoughts tokens: {cost_info['thoughts_tokens']}, "
                f"Total tokens: {cost_info['total_tokens']}"
            )
            print(f"Estimated cost: ${cost_info['estimated_cost_usd']:.6f} USD")
        else:
            print("\n--- ESTIMATED API COST ---")
            print("Pricing for this model is not configured yet.")
    except Exception as e:
        print(f"Error: {e}")
