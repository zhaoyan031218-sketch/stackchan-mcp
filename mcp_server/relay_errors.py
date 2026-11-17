"""Safe public diagnostics: never copy arbitrary device or proxy response text."""

RELAY_ERRORS = {
    "device_offline": "Device outbound channel is offline.",
    "device_disconnected": "Device disconnected before its reply completed; command was not retried.",
    "binary_reply_interrupted": "Device announced binary data, then disconnected before it arrived; command was not retried.",
    "reply_invalid_json": "Device reply was not valid JSON.",
    "reply_invalid_message": "Device reply was not a valid protocol message.",
    "reply_text_too_large": "Device reply metadata exceeded its size limit.",
    "reply_invalid_status": "Device reply contained an invalid HTTP status.",
    "reply_invalid_binary_size": "Device reply announced an invalid or oversized binary payload.",
    "reply_wrong_content_type": "Device reply content type did not match the requested image/audio endpoint.",
    "reply_missing_body": "Device reply contained neither binary metadata nor a JSON object body.",
    "reply_invalid_binary_frame": "Device binary reply had an invalid header or frame size.",
    "reply_missing_binary_metadata": "Device sent binary data before announcing its size.",
    "reply_binary_length_mismatch": "Device binary reply length did not match its announced size.",
    "command_timeout": "Device command timed out; completion unknown, not retried.",
    "channel_write_failed": "Device channel write failed; completion unknown, not retried.",
}

# Older firmware does not send error codes. Only these exact known messages
# may be displayed; unknown bodies could contain secrets or user content.
DEVICE_ERRORS = {
    "response allocation failed": "Device could not allocate the image/audio reply buffer.",
    "binary response too large": "Device image/audio reply exceeded its size limit.",
    "capture failed": "Device camera capture or camera-bus recovery failed.",
}
