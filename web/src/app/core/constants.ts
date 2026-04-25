// Public defaults and tuning constants shared across web modules.

// === Translation defaults ===
export const DEFAULT_MAX_RETRIES = 5;
export const DEFAULT_BATCH_SIZE = 10;
export const DEFAULT_CONCURRENCY = 5;
export const DEFAULT_PARALLEL_FILES = 1;
export const DEFAULT_CONTEXT_OVERLAP = 2;
export const DEFAULT_REVIEW = true;
export const DEFAULT_REFINE_ATTRIBUTION = true;
// Sized for full-quality scans on typical TV episodes; lower on tight-context
// local models (~8k window), raise on large-context cloud models.
export const DEFAULT_SCAN_BUDGET = 24_000;

// === Prepass / attribution scan ===
export const SCAN_MAX_TOKENS = 3000;
// 2-char names collide with common target-language words.
export const MIN_NAME_LEN = 3;
// Single-block scenes never need per-block speaker attribution.
export const ATTRIB_MIN_BLOCKS = 3;

// === Batch retry/split ===
export const ATTEMPTS_BEFORE_SPLIT = 2;

// === HTTP ===
export const CRED_QUERY_PARAMS = ['key', 'api_key', 'apikey', 'access_token'] as const;
