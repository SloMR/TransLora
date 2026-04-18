import {
  SubtitleBlock,
  parseSrt,
  serializeSrt,
  splitBatches,
  validateBatch,
} from './srt-parser';

const SAMPLE =
  '1\n' +
  '00:00:01,000 --> 00:00:02,500\n' +
  'Hello world\n' +
  '\n' +
  '2\n' +
  '00:00:03,000 --> 00:00:04,500\n' +
  'Two\n' +
  'lines\n';

describe('srt-parser', () => {
  describe('parseSrt', () => {
    it('parses basic blocks', () => {
      const blocks = parseSrt(SAMPLE);
      expect(blocks.map((b) => b.number)).toEqual([1, 2]);
      expect(blocks[0].timestamp).toBe('00:00:01,000 --> 00:00:02,500');
      expect(blocks[1].text).toBe('Two\nlines');
    });

    it('strips BOM and normalizes CRLF', () => {
      const raw = '\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\nHi\r\n';
      const blocks = parseSrt(raw);
      expect(blocks.length).toBe(1);
      expect(blocks[0].text).toBe('Hi');
    });

    it('skips malformed blocks', () => {
      const raw =
        'not-a-number\n' +
        '00:00:01,000 --> 00:00:02,500\n' +
        'text\n' +
        '\n' +
        '2\n' +
        '00:00:03,000 --> 00:00:04,500\n' +
        'good\n';
      expect(parseSrt(raw).map((b) => b.number)).toEqual([2]);
    });
  });

  describe('serializeSrt', () => {
    it('round-trips through parse', () => {
      const blocks = parseSrt(SAMPLE);
      expect(parseSrt(serializeSrt(blocks))).toEqual(blocks);
    });
  });

  describe('splitBatches', () => {
    const make = (n: number): SubtitleBlock[] =>
      Array.from({ length: n }, (_, i) => ({
        number: i + 1,
        timestamp: '00:00:00,000 --> 00:00:01,000',
        text: 'x',
      }));

    it('splits into exact-size batches with a remainder', () => {
      expect(splitBatches(make(7), 3).map((b) => b.length)).toEqual([3, 3, 1]);
    });

    it('returns a single batch when size >= length', () => {
      expect(splitBatches(make(7), 10).map((b) => b.length)).toEqual([7]);
    });

    it('handles empty input', () => {
      expect(splitBatches([], 5)).toEqual([]);
    });
  });

  describe('validateBatch', () => {
    const b = (n: number, ts: string, text = ''): SubtitleBlock => ({
      number: n,
      timestamp: ts,
      text,
    });

    it('passes on matching structure', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000')];
      const out = [b(1, '00:00:01,000 --> 00:00:02,000', 'translated')];
      expect(validateBatch(a, out).ok).toBeTrue();
    });

    it('fails on count mismatch', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000')];
      const result = validateBatch(a, []);
      expect(result.ok).toBeFalse();
      expect(result.error.toLowerCase()).toContain('count');
    });

    it('fails on number mismatch', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000')];
      const out = [b(2, '00:00:01,000 --> 00:00:02,000')];
      expect(validateBatch(a, out).ok).toBeFalse();
    });

    it('fails when timestamp was modified', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000')];
      const out = [b(1, '00:00:01,000 --> 00:00:02,500')];
      const result = validateBatch(a, out);
      expect(result.ok).toBeFalse();
      expect(result.error.toLowerCase()).toContain('timestamp');
    });
  });
});
