"""Public defaults and tuning constants shared across CLI modules."""

# === Translation defaults (mirrored in TranslationConfig field defaults) ===
DEFAULT_BATCH_SIZE = 10
DEFAULT_CONCURRENCY = 1
DEFAULT_MAX_RETRIES = 5
# Sized for full-quality scans on typical TV episodes; lower on tight-context
# local models (~8k window), raise on large-context cloud models.
DEFAULT_SCAN_CHAR_BUDGET = 24_000
DEFAULT_CONTEXT_OVERLAP = 2

# === Prepass / attribution scan ===
SCAN_MAX_TOKENS = 3000
# 2-char names collide with common target-language words.
MIN_NAME_LEN = 3
# Single-block scenes never need per-block speaker attribution.
ATTRIB_MIN_BLOCKS = 3

# === Batch retry/split ===
ATTEMPTS_BEFORE_SPLIT = 2

# === HTTP ===
REQUEST_TIMEOUT_SECS = 120.0
CRED_QUERY_PARAMS = frozenset({"key", "api_key", "apikey", "access_token"})
