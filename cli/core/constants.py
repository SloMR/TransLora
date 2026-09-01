"""Public defaults and tuning constants shared across CLI modules."""

# === Translation defaults (mirrored in TranslationConfig field defaults) ===
DEFAULT_BATCH_SIZE = 10
DEFAULT_CONCURRENCY = 1
DEFAULT_PARALLEL_FILES = 1
DEFAULT_MAX_RETRIES = 5
# Sized for full-quality scans on typical TV episodes; lower on tight-context
# local models (~8k window), raise on large-context cloud models.
DEFAULT_SCAN_CHAR_BUDGET = 24_000
DEFAULT_CONTEXT_OVERLAP = 2
DEFAULT_REVIEW = True
DEFAULT_REFINE_ATTRIBUTION = True
# Deterministic line-count/line-length repair after a batch validates.
DEFAULT_REFLOW = True
# One focused retry for the batches the run flagged, capped at a fraction of
# the file so an opt-out is about taste, not cost.
DEFAULT_FIX_FLAGGED = True
# Back-translation spot check and unconditional speaker attribution both add
# calls, so both are opt-in.
DEFAULT_VERIFY_ADEQUACY = False
DEFAULT_FULL_ATTRIBUTION = False
# "auto" = let the model follow the source's own register.
DEFAULT_FORMALITY = "auto"
FORMALITY_CHOICES = ("auto", "formal", "informal")
# "" = let the prepass scan name the target variant.
DEFAULT_DIALECT = ""
# "auto" = try utf-8, then the fallback codepages in translator.py.
DEFAULT_ENCODING = "auto"

# === Prepass / attribution scan ===
SCAN_MAX_TOKENS = 3000
# 2-char names collide with common target-language words.
MIN_NAME_LEN = 3
# Single-block scenes never need per-block speaker attribution.
ATTRIB_MIN_BLOCKS = 3

# === Flagged-batch repair ===
# Re-issued batches are capped at this share of the file, never fewer than
# FIX_FLAGGED_MIN; the rest are reported, never dropped silently.
FIX_FLAGGED_FRACTION = 0.05
FIX_FLAGGED_MIN = 2
# One term drifting across sixteen batches is one problem, not sixteen. Once a
# single cause reaches SYSTEMATIC_CAUSE_MIN batches the ceiling rises, because
# 5% of the file cannot answer a failure that spans a third of it.
SYSTEMATIC_REPAIR_FRACTION = 0.25
SYSTEMATIC_CAUSE_MIN = 4

# === Adequacy spot check ===
# Batches sampled for back-translation, and the share of the source cue's
# tokens the back-translation has to return before the cue counts as adequate.
ADEQUACY_SAMPLE_FRACTION = 0.2
ADEQUACY_MIN_BATCHES = 2
ADEQUACY_MIN_OVERLAP = 0.35

# === Target-variant drift ===
# Share of the finished file's cues that must carry a variant's own function
# words before the run says so. A warning only — never a rewrite.
VARIANT_DRIFT_FRACTION = 0.15

# === Throughput reporting ===
# Measured on a local 30B model: 6 serial requests in 20.7s. Only ever used
# for the pre-run estimate, which says so.
ESTIMATED_SECS_PER_CALL = 3.5

# === Batch retry/split ===
# Validation failures only — transport failures get their own max_retries budget.
ATTEMPTS_BEFORE_SPLIT = 2
MAX_RETRY_DELAY_SECS = 30.0

# === HTTP ===
REQUEST_TIMEOUT_SECS = 120.0
CRED_QUERY_PARAMS = frozenset({"key", "api_key", "apikey", "access_token"})
REQUEST_TEMPERATURE = 0.1

# === Provider request dialect ===
# The permissive shape every endpoint is asked for first, narrowed only by the
# server's own 400 — a model list would rot, and the same quirks turn up on
# other providers.
TOKEN_PARAM_DEFAULT = "max_tokens"
TOKEN_PARAM_COMPLETION = "max_completion_tokens"
DEFAULT_SEND_TEMPERATURE = True
# One 400 names one quirk, so a request needing both pays two round trips.
# Reasoning models spend the whole token budget thinking before emitting any
# text. Subtitle translation needs none of it: on gpt-5-mini this cut a reply
# from 476 tokens (448 of them reasoning) to 27 with the same translation.
REASONING_EFFORT_MINIMAL = "minimal"
MAX_DIALECT_CORRECTIONS = 2
# A reasoning model can spend a whole budget thinking before it writes a word.
# The budget is doubled up to this multiple of the request's own size.
REASONING_BUDGET_MULTIPLIER = 4
