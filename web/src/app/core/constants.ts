// Public defaults and tuning constants shared across web modules.

// === Translation defaults ===
export const DEFAULT_MAX_RETRIES = 5;
export const DEFAULT_BATCH_SIZE = 10;
export const DEFAULT_CONCURRENCY = 5;
export const DEFAULT_PARALLEL_FILES = 1;
export const DEFAULT_CONTEXT_OVERLAP = 2;
export const DEFAULT_REVIEW = true;
export const DEFAULT_REFINE_ATTRIBUTION = true;
// Deterministic line-count/line-length repair after a batch validates.
export const DEFAULT_REFLOW = true;
// One focused retry for the batches the run flagged, capped at a fraction of
// the file so an opt-out is about taste, not cost.
export const DEFAULT_FIX_FLAGGED = true;
// Back-translation spot check and unconditional speaker attribution both add
// calls, so both are opt-in.
export const DEFAULT_VERIFY_ADEQUACY = false;
export const DEFAULT_FULL_ATTRIBUTION = false;
// "auto" = let the model follow the source's own register.
export const DEFAULT_FORMALITY = 'auto';
export const FORMALITY_CHOICES = ['auto', 'formal', 'informal'] as const;
/** Address the viewer formally, informally, or however the model sees fit. */
export type Formality = (typeof FORMALITY_CHOICES)[number];
// "" = let the prepass scan name the target variant.
export const DEFAULT_DIALECT = '';
// Sized for full-quality scans on typical TV episodes; lower on tight-context
// local models (~8k window), raise on large-context cloud models.
export const DEFAULT_SCAN_BUDGET = 24_000;

// === Prepass / attribution scan ===
export const SCAN_MAX_TOKENS = 3000;
// 2-char names collide with common target-language words.
export const MIN_NAME_LEN = 3;
// Single-block scenes never need per-block speaker attribution.
export const ATTRIB_MIN_BLOCKS = 3;
// A subtitle equivalent is short; an explanation is not. An idiom target past
// both of these is a dictionary definition, and pasting one over a punchline
// ships a cue no one can read in time.
export const IDIOM_MAX_EXPANSION = 2.5;
export const IDIOM_MAX_TARGET_CHARS = 40;

// === Flagged-batch repair ===
// Re-issued batches are capped at this share of the file, never fewer than
// FIX_FLAGGED_MIN; the rest are reported, never dropped silently.
export const FIX_FLAGGED_FRACTION = 0.05;
export const FIX_FLAGGED_MIN = 2;
// One term drifting across sixteen batches is one problem, not sixteen: once a
// single cause reaches this many flagged batches the ceiling widens to the
// larger share, so a systematic failure cannot be left mostly unrepaired.
export const SYSTEMATIC_CAUSE_MIN = 4;
export const SYSTEMATIC_REPAIR_FRACTION = 0.25;

// === Target-variant drift ===
// Share of the finished file's cues carrying a variant's own function words
// before the run says so. Report only — it never rewrites a cue.
export const VARIANT_DRIFT_FRACTION = 0.15;

// === Adequacy spot check ===
// Batches sampled for back-translation, and the share of the source cue's
// tokens the back-translation has to return before the cue counts as adequate.
export const ADEQUACY_SAMPLE_FRACTION = 0.2;
export const ADEQUACY_MIN_BATCHES = 2;
export const ADEQUACY_MIN_OVERLAP = 0.35;

// === Throughput reporting ===
// Measured on a local 30B model: 6 serial requests in 20.7s. Only ever used
// for the pre-run estimate, which says so.
export const ESTIMATED_SECS_PER_CALL = 3.5;

// === Batch retry/split ===
// Validation failures only; transport failures use maxRetries instead.
export const ATTEMPTS_BEFORE_SPLIT = 2;
// Also caps a server-supplied Retry-After.
export const MAX_RETRY_DELAY_SECS = 30;

// === HTTP ===
// Default only; QualityOptions.timeoutMs overrides it for slow local inference.
export const REQUEST_TIMEOUT_SECS = 120;
export const CRED_QUERY_PARAMS = ['key', 'api_key', 'apikey', 'access_token'] as const;
export const REQUEST_TEMPERATURE = 0.1;

// === Provider request dialect ===
// The permissive shape every endpoint is asked for first, narrowed only by the
// server's own 400 — a model list would rot, and the same quirks turn up on
// other providers.
export const TOKEN_PARAM_DEFAULT = 'max_tokens';
export const TOKEN_PARAM_COMPLETION = 'max_completion_tokens';
export const DEFAULT_SEND_TEMPERATURE = true;
// One 400 names one quirk, so a request needing both pays two round trips.
export const MAX_DIALECT_CORRECTIONS = 2;
// A reasoning model can spend a whole budget thinking before it writes a word.
// The budget is doubled up to this multiple of the request's own size.
export const REASONING_BUDGET_MULTIPLIER = 4;
