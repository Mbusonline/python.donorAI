"""
API Call Logging Service
Logs API calls (Gemini and OpenAI) with token usage to JSON files
"""

import json
import os
from datetime import datetime
from typing import Optional

import settings


def _append_global_log(provider: str, entry: dict) -> None:
    """
    Append provider call info to a single global log file.

    This is intentionally best-effort: failures here should never break the request.
    """
    try:
        jsonl_path = settings.PROVIDER_API_LOG_PATH
        os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"provider": provider, **entry}, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never fail the main request due to logging.
        print(f"[api_logger] warning: could not write global provider log: {e}")


def log_gemini_api_call(
    model: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    prompt_length: Optional[int] = None,
    response_length: Optional[int] = None,
    image_path: Optional[str] = None,
    operation: str = "unknown",
    success: bool = True,
    error: Optional[str] = None,
    log_dir: str = "logs",
) -> None:
    """
    Log a Gemini API call to JSON file.

    Args:
        model: Model name used
        input_tokens: Number of input tokens (if available from API)
        output_tokens: Number of output tokens (if available from API)
        prompt_length: Length of prompt in characters (fallback if tokens not available)
        response_length: Length of response in characters (fallback if tokens not available)
        image_path: Path to image if this was a vision call
        operation: Operation type (e.g., "section_extraction", "image_analysis")
        success: Whether the call was successful
        error: Error message if call failed
    """
    # Build log entry - always include input_tokens and output_tokens (even if None)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "operation": operation,
        "success": success,
        "input_tokens": input_tokens,  # Always included, even if None
        "output_tokens": output_tokens,  # Always included, even if None
    }

    # Add optional fields only if they have values
    if prompt_length is not None:
        log_entry["prompt_length"] = prompt_length
    if response_length is not None:
        log_entry["response_length"] = response_length
    if image_path is not None:
        log_entry["image_path"] = image_path
    if error is not None:
        log_entry["error"] = error

    # Ensure logs directory exists
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "gemini_api_logs.json")

    # Read existing logs or create new list
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logs = []
    else:
        logs = []

    # Append new log entry
    logs.append(log_entry)

    # Write back to file
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

    _append_global_log("google", log_entry)

    print(
        f"✓ Logged Gemini API call: {operation} ({input_tokens or prompt_length} in, {output_tokens or response_length} out)"
    )


def log_openai_api_call(
    model: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    prompt_length: Optional[int] = None,
    response_length: Optional[int] = None,
    operation: str = "content_assembly",
    success: bool = True,
    error: Optional[str] = None,
    log_dir: str = "logs",
) -> None:
    """
    Log an OpenAI API call to JSON file.

    Args:
        model: Model name used
        input_tokens: Number of input tokens (if available from API)
        output_tokens: Number of output tokens (if available from API)
        prompt_length: Length of prompt in characters (fallback if tokens not available)
        response_length: Length of response in characters (fallback if tokens not available)
        operation: Operation type (e.g., "content_assembly")
        success: Whether the call was successful
        error: Error message if call failed
    """
    # Build log entry - always include input_tokens and output_tokens (even if None)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "operation": operation,
        "success": success,
        "input_tokens": input_tokens,  # Always included, even if None
        "output_tokens": output_tokens,  # Always included, even if None
    }

    # Add optional fields only if they have values
    if prompt_length is not None:
        log_entry["prompt_length"] = prompt_length
    if response_length is not None:
        log_entry["response_length"] = response_length
    if error is not None:
        log_entry["error"] = error

    # Ensure logs directory exists
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "openai_api_logs.json")

    # Read existing logs or create new list
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logs = []
    else:
        logs = []

    # Append new log entry
    logs.append(log_entry)

    # Write back to file
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

    _append_global_log("openai", log_entry)

    print(
        f"✓ Logged OpenAI API call: {operation} ({input_tokens or prompt_length} in, {output_tokens or response_length} out)"
    )
