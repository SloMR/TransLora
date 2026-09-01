import {
  DIACRITIC_CUE_MIN,
  detectCrossCueShift,
  detectVariantDrift,
  dialogueDashLines,
  enforceLineLength,
  findTags,
  normalizeDiacritics,
  normalizeRtlPunctuation,
  reflowToLineCount,
  repairTags,
  restoreDialogueDashes,
  restoreTerminalPunctuation,
  dropEmptyTagPairs,
  scriptLeaks,
  variantDriftMessage,
  visibleLength,
} from './repair';
import { normsFor, scriptFor } from './languages';
import {
  BLED_PAIRS,
  COLLAPSED_LINES_CUE,
  CRLF_SAMPLE_SRT,
  DASH_DROPPED_CUE,
  DASH_INTACT_CUE,
  DASH_MERGED_CUE,
  DROPPED_WRAP_CUE,
  EMPTY_PAIR_CUE,
  FLATTENED_MARK_CUES,
  LEAKED_HAN_CUE,
  LONG_LINE_CUE,
  OVER_CJK_LIMIT_CUE,
  PER_SCRIPT_BUDGET_CUE,
  SCRIPT_LANDMARKS,
  SHARED_WORD_PAIR,
  ScriptLandmarks,
  TargetLanguage,
  VOCALIZED_CUES,
  WELDED_LATIN_CUE,
  cue,
  cuesFor,
} from './testdata/aligned-cues';
import {
  SubtitleBlock,
  parseLite,
  parseSrt,
  serializeLite,
  serializeSrt,
  splitBatches,
  validateBatch,
} from './srt-parser';
import { parseSubtitle } from './subtitle-formats';

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

    it('ignores a blank output timestamp (wire blocks carry none)', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000', 'hi')];
      const out = [b(1, '', 'hola')];
      expect(validateBatch(a, out).ok).toBeTrue();
    });

    it('fails on shifted numbering', () => {
      const ts = (n: number) => `00:00:0${n},000 --> 00:00:0${n + 1},000`;
      const a = [b(1, ts(1), 'one'), b(2, ts(2), 'two'), b(3, ts(3), 'three')];
      const out = [b(2, ts(1), 'deux'), b(3, ts(2), 'trois'), b(4, ts(3), 'quatre')];
      const result = validateBatch(a, out);
      expect(result.ok).toBeFalse();
      expect(result.error).toContain('index 0');
    });

    it('fails when a non-empty source block comes back empty', () => {
      const a = [
        b(1, '00:00:01,000 --> 00:00:02,000', 'hi'),
        b(2, '00:00:03,000 --> 00:00:04,000', 'there'),
      ];
      const out = [
        b(1, '00:00:01,000 --> 00:00:02,000', 'hola'),
        b(2, '00:00:03,000 --> 00:00:04,000', '   '),
      ];
      const result = validateBatch(a, out);
      expect(result.ok).toBeFalse();
      expect(result.error.toLowerCase()).toContain('empty');
    });

    it('allows an empty output for an empty source block', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000', '')];
      const out = [b(1, '00:00:01,000 --> 00:00:02,000', '')];
      expect(validateBatch(a, out).ok).toBeTrue();
    });

    it('fails when a timestamp line leaked into the text', () => {
      const a = [b(1, '00:00:01,000 --> 00:00:02,000', 'hi')];
      const out = [
        b(1, '00:00:01,000 --> 00:00:02,000', 'hola\n00:00:05,000 --> 00:00:06,000'),
      ];
      const result = validateBatch(a, out);
      expect(result.ok).toBeFalse();
      expect(result.error.toLowerCase()).toContain('leaked');
    });

    it('passes a clean multi-block batch', () => {
      const a = [
        b(1, '00:00:01,000 --> 00:00:02,000', 'one'),
        b(2, '00:00:03,000 --> 00:00:04,000', 'two'),
      ];
      const out = [
        b(1, '00:00:01,000 --> 00:00:02,000', 'un'),
        b(2, '00:00:03,000 --> 00:00:04,000', 'deux'),
      ];
      expect(validateBatch(a, out)).toEqual({ ok: true, error: '' });
    });
  });

  describe('parseLite', () => {
    it('parses number + text blocks without timestamps', () => {
      const blocks = parseLite('1\nUno\n\n2\nDos\ndos');
      expect(blocks.map((b) => b.number)).toEqual([1, 2]);
      expect(blocks[0]!.timestamp).toBe('');
      expect(blocks[1]!.text).toBe('Dos\ndos');
    });

    it('strips BOM and normalizes CRLF', () => {
      expect(parseLite('\ufeff1\r\nUno\r\n')).toEqual([
        { number: 1, timestamp: '', text: 'Uno' },
      ]);
    });

    it('rejects "N)" numbering instead of making an empty-text block', () => {
      // A block here would carry the whole line as its number and no text,
      // which then passes validation and ships a blank subtitle.
      expect(parseLite('1)\nUno\n')).toEqual([]);
      expect(parseLite('1) Uno\n\n2) Dos')).toEqual([]);
    });

    it('skips commentary before the first block', () => {
      const blocks = parseLite('Here is the translation:\n\n1\nUno\n\n2\nDos\n');
      expect(blocks.map((b) => b.number)).toEqual([1, 2]);
    });

    it('rejects a numbered line with trailing junk', () => {
      expect(parseLite('1.\nUno')).toEqual([]);
    });

    it('treats a blank line as a block boundary', () => {
      // Why serializeLite collapses blank lines: the tail would be read as a
      // separate block, and "second" is not a block number, so it is dropped.
      expect(parseLite('1\nfirst\n\nsecond')).toEqual([
        { number: 1, timestamp: '', text: 'first' },
      ]);
    });
  });

  describe('serializeLite', () => {
    it('emits number + text only', () => {
      const blocks: SubtitleBlock[] = [
        { number: 1, timestamp: '00:00:01,000 --> 00:00:02,000', text: 'Uno' },
        { number: 2, timestamp: '00:00:03,000 --> 00:00:04,000', text: 'Dos' },
      ];
      expect(serializeLite(blocks)).toBe('1\nUno\n\n2\nDos\n');
    });

    it('collapses a blank line inside a block so the wire keeps one block', () => {
      const blocks: SubtitleBlock[] = [
        { number: 1, timestamp: '00:00:01,000 --> 00:00:02,000', text: 'first\n\nsecond' },
      ];
      expect(serializeLite(blocks)).toBe('1\nfirst\nsecond\n');
      expect(parseLite(serializeLite(blocks)).length).toBe(1);
    });
  });
});

// The repair cases are ported from cli/tests/test_repair.py — kept in the same
// order and shape so drift between the two repair passes is greppable. Every one
// is drawn from a real 372-block English->Arabic run: 16 of 24 formatting tags
// dropped undetected, 85 cues (23%) collapsed two source lines into one, and a
// clause migrated from block 236 into 237.

const ITALIC_OPEN = '{\\i1}';
const ITALIC_CLOSE = '{\\i0}';

function block(number: number, text: string): SubtitleBlock {
  return { number, timestamp: '', text };
}

describe('findTags', () => {
  const cases: [string, string[]][] = [
    ['{\\i1}Hello{\\i0}', ['{\\i1}', '{\\i0}']],
    ['<i>Hello</i>', ['<i>', '</i>']],
    ['<font color="#fff">Hi</font>', ['<font color="#fff">', '</font>']],
    ['{\\an8}Top of screen', ['{\\an8}']],
    ['Plain text', []],
    // 3 < 4 is not a tag: a tag opens with a letter.
    ['3 < 4 and 5 > 2', []],
  ];

  for (const [text, tags] of cases) {
    it(`lists both tag forms in reading order: ${JSON.stringify(text)}`, () => {
      expect(findTags(text)).toEqual(tags);
    });
  }

  it('measures a visible length that ignores tags', () => {
    expect(visibleLength('{\\i1}Hello{\\i0}')).toBe('Hello'.length);
    expect(visibleLength('<i>Hi</i> there')).toBe('Hi there'.length);
  });
});

describe('repairTags', () => {
  it('passes matching tags through untouched', () => {
    expect(repairTags('<i>Hello</i>', '<i>مرحبا</i>'))
      .toEqual({ text: '<i>مرحبا</i>', ok: true });
  });

  it('is a no-op when neither side carries a tag', () => {
    expect(repairTags('Hello', 'مرحبا')).toEqual({ text: 'مرحبا', ok: true });
  });

  // The observed defect: blocks 235-239, 242, 292 and 363 lost their italics.
  const wrapping: [string, string][] = [
    ['{\\i1}Hello there{\\i0}', '{\\i1}مرحبا{\\i0}'],
    ['<i>Hello there</i>', '<i>مرحبا</i>'],
  ];
  for (const [source, expected] of wrapping) {
    it(`restores a dropped wrapping pair: ${JSON.stringify(source)}`, () => {
      expect(repairTags(source, 'مرحبا')).toEqual({ text: expected, ok: true });
    });
  }

  it('restores a dropped leading tag', () => {
    expect(repairTags('{\\an8}Hello', 'مرحبا'))
      .toEqual({ text: '{\\an8}مرحبا', ok: true });
  });

  const unrepairable: [string, string][] = [
    // Interior tags: where they belong in the translation is a guess.
    ['A <i>b</i> c', 'س ص ع'],
    ['Hello{\\i0} there', 'مرحبا'],
    // Trailing-only tag: prefixing it would move it.
    ['Hello{\\i0}', 'مرحبا'],
  ];
  for (const [source, output] of unrepairable) {
    it(`reports rather than guesses an unrepairable loss: ${JSON.stringify(source)}`, () => {
      expect(repairTags(source, output)).toEqual({ text: output, ok: false });
    });
  }

  const changed: [string, string][] = [
    ['<i>Hello</i>', '<b>مرحبا</b>'], // wrong tag
    ['<i>Hello</i>', '<i>مرحبا'], // closing tag dropped
    ['Hello', '<i>مرحبا</i>'], // tag invented
    ['<i>a</i> <i>b</i>', '<i>س ص</i>'], // pair merged
  ];
  for (const [source, output] of changed) {
    it(`never rewrites changed tags: ${JSON.stringify(output)}`, () => {
      expect(repairTags(source, output)).toEqual({ text: output, ok: false });
    });
  }
});

describe('reflowToLineCount', () => {
  it('restores two source lines the model collapsed into one', () => {
    // 85 of 372 cues did this; max line length went from 47 to 98 chars.
    const oneLine = 'متى يتجاوز التعليق أو الفعل الخط الأحمر ويصبح شيئا آخر تماما';
    const out = reflowToLineCount(oneLine, 2, 42, 'arabic');
    expect(out.split('\n').length).toBe(2);
    expect(out.replace(/\n/g, ' ')).toBe(oneLine);
    expect(Math.max(...out.split('\n').map((line) => line.length))).toBeLessThanOrEqual(42);
  });

  it('minimises the longest line rather than filling greedily', () => {
    // A greedy fill would give "aaa bbb ccc ddd eee" / "fff".
    expect(reflowToLineCount('aaa bbb ccc ddd eee fff', 2, 42, 'latin'))
      .toBe('aaa bbb ccc\nddd eee fff');
  });

  it('produces three lines when the source had three', () => {
    expect(reflowToLineCount('aaa bbb ccc ddd eee fff', 3, 42, 'latin'))
      .toBe('aaa bbb\nccc ddd\neee fff');
  });

  it('returns a cue that already matches unchanged', () => {
    const text = 'first line\nsecond line';
    expect(reflowToLineCount(text, 2, 42, 'latin')).toBe(text);
  });

  // Those dashes encode speaker turns; re-flowing them merges two speakers.
  const dashCues: [string, number][] = [
    ['- Yes, I did.\n- No, you did not do that at all', 1],
    ['– Yes, I did.\n– No, you did not do that at all', 1],
    ['- Yes, I did. - No, you did not do that at all', 2],
    ['{\\i1}- Yes.{\\i0}\n- No, not at all, I promise you that', 1],
  ];
  for (const [text, target] of dashCues) {
    it(`never reflows a dialogue-dash cue: ${JSON.stringify(text)}`, () => {
      expect(reflowToLineCount(text, target, 42, 'latin')).toBe(text);
    });
  }

  const noSpace: [string, string, string][] = [
    ['你好世界，这是测试。', 'han', '你好世界，\n这是测试。'],
    [
      'これはテストです。もう一度お願いします。',
      'japanese',
      'これはテストです。\nもう一度お願いします。',
    ],
  ];
  for (const [text, script, expected] of noSpace) {
    it(`breaks a ${script} cue on characters, not spaces`, () => {
      expect(reflowToLineCount(text, 2, 16, script)).toBe(expected);
    });
  }

  it('prefers a CJK punctuation break over the even split', () => {
    // A 6/6 split would open the second line with "，"; 7/5 reads correctly.
    const out = reflowToLineCount('一二三四五六，七八九十。', 2, 6, 'han');
    expect(out).toBe('一二三四五六，\n七八九十。');
    for (const line of out.split('\n')) {
      expect('、。，．！？；：）」'.includes(line[0]!)).toBeFalse();
    }
  });

  it('never splits a formatting tag across lines', () => {
    const text = '{\\i1}it is not always easy to notice when the tide turns{\\i0}';
    const out = reflowToLineCount(text, 2, 42, 'latin');
    expect(out.split('\n')[0]!.startsWith(ITALIC_OPEN)).toBeTrue();
    expect(out.split('\n')[1]!.endsWith(ITALIC_CLOSE)).toBeTrue();
    expect(findTags(out)).toEqual(findTags(text));
  });

  it('moves a tag glued to a word along with it', () => {
    expect(reflowToLineCount('abcdefgh {\\i1}ijklmnop{\\i0} qrstuvwx', 2, 12, 'latin'))
      .toBe('abcdefgh\n{\\i1}ijklmnop{\\i0} qrstuvwx');
  });

  const impossible: [string, number][] = [
    ['Hi', 3], // fewer words than lines
    ['', 2],
    ['   ', 2],
    ['Hello there', 0],
    ['Hello there', -1],
  ];
  for (const [text, target] of impossible) {
    it(`returns the text unchanged when the reflow is impossible: ${JSON.stringify(text)} into ${target}`, () => {
      expect(reflowToLineCount(text, target, 42, 'latin')).toBe(text);
    });
  }

  it('never loses or reorders words', () => {
    const text = 'the quick brown fox jumps over the lazy dog again';
    const out = reflowToLineCount(text, 2, 42, 'latin');
    expect(out.split(/\s+/)).toEqual(text.split(/\s+/));
  });
});

describe('enforceLineLength', () => {
  it('turns a single over-long line into two', () => {
    const text = 'a'.repeat(30) + ' ' + 'b'.repeat(30);
    expect(enforceLineLength(text, 42, 'latin')).toBe('a'.repeat(30) + '\n' + 'b'.repeat(30));
  });

  it('leaves a line within the limit alone', () => {
    expect(enforceLineLength('short enough', 42, 'latin')).toBe('short enough');
  });

  it('never splits a two-line cue into three', () => {
    // Two lines is the professional maximum, over-long or not.
    const text = 'a'.repeat(60) + '\n' + 'b'.repeat(60);
    expect(enforceLineLength(text, 42, 'latin')).toBe(text);
  });

  it('measures the limit without tags', () => {
    const text = '{\\i1}' + 'a'.repeat(40) + '{\\i0}';
    expect(enforceLineLength(text, 42, 'latin')).toBe(text);
  });

  it('leaves an unsplittable long word alone', () => {
    const text = 'a'.repeat(60);
    expect(enforceLineLength(text, 42, 'latin')).toBe(text);
  });
});

describe('normalizeRtlPunctuation', () => {
  const repointed: [string, string][] = [
    ['مرحبا, كيف حالك?', 'مرحبا، كيف حالك؟'],
    ['نعم; لا', 'نعم؛ لا'],
    ['ماذا?', 'ماذا؟'],
  ];
  for (const [text, expected] of repointed) {
    it(`repoints Arabic punctuation: ${JSON.stringify(text)}`, () => {
      expect(normalizeRtlPunctuation(text, 'arabic')).toBe(expected);
    });
  }

  it('reaches across a formatting tag', () => {
    expect(normalizeRtlPunctuation('{\\i1}مرحبا{\\i0}, بك', 'arabic'))
      .toBe('{\\i1}مرحبا{\\i0}، بك');
  });

  // Hebrew is RTL but keeps ASCII punctuation.
  for (const script of ['hebrew', 'latin', 'han', 'default']) {
    it(`leaves a ${script} target untouched`, () => {
      expect(normalizeRtlPunctuation('שלום, מה?', script)).toBe('שלום, מה?');
    });
  }

  const protectedSpans: [string, string][] = [
    ['Hello, world?', 'no Arabic anywhere'],
    ['2x02 : الحلقة', 'episode code, Latin/digit run'],
    ['1,000 دولار', 'digit group separator'],
    ['زر http://x.com/a,b?q=1 الآن', 'URL query string'],
    ['زر www.example.com/a?b الآن', 'bare host form'],
    ['{\\pos(10,20)}مرحبا', 'comma inside an ASS override'],
    ['<font face="A,B">مرحبا</font>', 'comma inside an HTML attribute'],
  ];
  for (const [text, why] of protectedSpans) {
    it(`keeps ASCII punctuation in a protected span (${why})`, () => {
      expect(normalizeRtlPunctuation(text, 'arabic')).toBe(text);
    });
  }

  it('leaves a comma directly after a URL as ASCII', () => {
    // The URL pattern eats the trailing comma; leaving it alone is the safe
    // side of the trade, since a comma can be part of the address.
    expect(normalizeRtlPunctuation('www.example.com, مرحبا', 'arabic'))
      .toBe('www.example.com, مرحبا');
    expect(normalizeRtlPunctuation('مرحبا, www.example.com', 'arabic'))
      .toBe('مرحبا، www.example.com');
  });

  it('handles empty text', () => {
    expect(normalizeRtlPunctuation('', 'arabic')).toBe('');
  });
});

describe('detectCrossCueShift', () => {
  function pair(
    aSrc: string, bSrc: string, aOut: string, bOut: string,
  ): [SubtitleBlock[], SubtitleBlock[]] {
    return [
      [block(236, aSrc), block(237, bSrc)],
      [block(236, aOut), block(237, bOut)],
    ];
  }

  it('reports a run the output pair shares and the source pair does not', () => {
    // Block 236 kept 237's only clause; block count and numbering stayed
    // intact, so validation passed and nothing else would have caught it.
    const [source, output] = pair(
      '{\\i1}when the tide turns in the channel,{\\i0}', '{\\i1}gates must close early.{\\i0}',
      '{\\i1}متى يتجاوز التعليق أو الفعل الخط.{\\i0}', '{\\i1}الخط.{\\i0}',
    );
    expect(detectCrossCueShift(source, output)).toEqual([
      "Blocks 236-237: 'الخط' appears in both cues - text may have shifted "
      + 'between them',
    ]);
  });

  it('reads through the formatting tags wrapping both cues', () => {
    const [source, output] = pair(
      'when the tide turns in the channel,', 'gates must close early.',
      '<i>متى يتجاوز التعليق أو الفعل الخط.</i>', '<i>الخط.</i>',
    );
    expect(detectCrossCueShift(source, output).length).toBe(1);
  });

  it('stays quiet when the two source cues repeat the run as well', () => {
    // A refrain both cues really say is repetition, not a shift.
    const [source, output] = pair(
      'The company always wins the day.', 'The company never loses at all.',
      'الشركة تفوز دائما باليوم.', 'الشركة لا تخسر أبدا.',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('skips a pair whose source cue is too short to mean anything', () => {
    const [source, output] = pair(
      'Oh!', 'That is quite a long line of dialogue.',
      'الخط.', 'يا الهي الخط كم هذا مدهش حقا',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('ignores a run too short to be distinctive', () => {
    const [source, output] = pair(
      'when the tide turns in the channel,', 'gates must close and lock.',
      'متى يتجاوز التعليق أو الفعل', 'من الخط الأحمر ويؤذي.',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('ignores a run that accounts for too little of the shorter cue', () => {
    // 'الشركة' is 6 of the 23 comparable characters in the shorter cue: a
    // word both cues happen to use, not a clause that moved.
    const [source, output] = pair(
      'The firm always wins the day.', 'Nobody ever loses at all here.',
      'الشركة تفوز دائما باليوم وهذا معروف.', 'الشركة لا تخسر أبدا هنا.',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('ignores a run the source pair already shares half of', () => {
    // The output run is only 1.5x the source run, under the 2x the rule wants.
    const [source, output] = pair(
      'The safety briefing starts.', 'The briefing is over now.',
      'السلامة انتهت.', 'السلامة.',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('names the run carrying the most characters, not the most words', () => {
    // Both cues share 'في ذلك' (two words, 5 characters) and 'بالانضمام'
    // (one word, 9). Scoring by word count would pick the shorter run and
    // then find it covers too little of the cue to report at all.
    const [source, output] = pair(
      'and now I am asking whether anyone here', 'would like to sign up?',
      'و الآن أتساءل إن كان في ذلك من يرغب بالانضمام',
      'يرغب في ذلك بالانضمام',
    );
    expect(detectCrossCueShift(source, output)).toEqual([
      "Blocks 236-237: 'بالانضمام' appears in both cues - text may have "
      + 'shifted between them',
    ]);
  });

  it('does not flag an ordinary translation', () => {
    const [source, output] = pair(
      'when the tide turns in the channel,', 'gates must close and lock.',
      'متى يتجاوز التعليق أو الفعل', 'يتخطى الحدود ويؤذي.',
    );
    expect(detectCrossCueShift(source, output)).toEqual([]);
  });

  it('reports nothing for an empty batch', () => {
    expect(detectCrossCueShift([], [])).toEqual([]);
  });
});

// The cases above pin the rule on cues built to trip it. A whole-file pass is
// the only place its false-positive rate shows: the fixture plants two bleeds
// among 47 adjacent pairs, several of which reuse a word on purpose.
describe('detectCrossCueShift over the whole synthetic run', () => {
  const arabic = cuesFor('Arabic');
  const source = arabic.map((c) => block(c.n, c.en));
  const output = arabic.map((c) => block(c.n, c.target));
  const messages = detectCrossCueShift(source, output);
  const flagged = messages.map((m) => m.slice('Blocks '.length, m.indexOf(':')));
  const planted = BLED_PAIRS.map(([first, second]) => `${first}-${second}`);

  it('reads every cue of the Arabic file, numbered 1..n', () => {
    expect(arabic.length).toBe(48);
    expect(arabic.map((c) => c.n))
      .toEqual(Array.from({ length: 48 }, (_, i) => i + 1));
  });

  it('catches every bleed the fixture plants', () => {
    for (const pair of planted) expect(flagged).toContain(pair);
  });

  it('flags nothing else across the remaining adjacent pairs', () => {
    expect(flagged).toEqual(planted);
  });

  it('leaves the pair that legitimately repeats a name alone', () => {
    const [first, second] = SHARED_WORD_PAIR;
    expect(flagged).not.toContain(`${first}-${second}`);
  });

  it('names the run that moved', () => {
    expect(messages).toContain(
      "Blocks 25-26: 'أوراق التأمين' appears in both cues - text may have "
      + 'shifted between them',
    );
  });
});

describe('normalizeDiacritics', () => {
  const plain = (n: number) => block(n, 'مرحبا بك في المكتب');
  // Ten marks: past the per-cue threshold on its own.
  const vocalized = (n: number) => block(n, 'جِبْهُ وَأَعْطِنِي قَبْلَةً، تَعَلَّمْ!');

  it('strips a vocalized cue in a file that is otherwise bare', () => {
    const out = normalizeDiacritics([plain(1), plain(2), plain(3), vocalized(4)], 'arabic');
    expect(out[3]!.text).toBe('جبه وأعطني قبلة، تعلم!');
    expect(out[0]!.text).toBe(plain(1).text);
  });

  it('leaves a file that is vocalized throughout alone', () => {
    const blocks = [vocalized(1), vocalized(2), vocalized(3)];
    expect(normalizeDiacritics(blocks, 'arabic')).toEqual(blocks);
  });

  it('leaves a cue carrying only a stray mark or two alone', () => {
    const stray = block(1, 'مرحبًا بك');
    expect(normalizeDiacritics([stray, plain(2), plain(3)], 'arabic')[0]!.text)
      .toBe(stray.text);
  });

  it('touches nothing outside the Arabic script', () => {
    const blocks = [plain(1), vocalized(2)];
    for (const script of ['latin', 'hebrew', 'han']) {
      expect(normalizeDiacritics(blocks, script)).toBe(blocks);
    }
  });

  it('reports nothing for an empty file', () => {
    expect(normalizeDiacritics([], 'arabic')).toEqual([]);
  });

  it('strips exactly the cues that switched register', () => {
    const blocks = cuesFor('Arabic').map((c) => block(c.n, c.target));
    const marks = (text: string) => (text.match(/[\u064B-\u0652\u0670]/g) ?? []).length;
    const before = blocks.filter((b) => marks(b.text) >= DIACRITIC_CUE_MIN);
    expect(before.map((b) => b.number)).toEqual(VOCALIZED_CUES);

    const after = normalizeDiacritics(blocks, 'arabic');
    expect(after.filter((b) => marks(b.text) >= DIACRITIC_CUE_MIN)).toEqual([]);
    expect(after.find((b) => b.number === 39)!.text).toBe('وتبدأ نوبتك أخيرا.');
    // Letters, not just marks, have to survive the strip.
    expect(after.find((b) => b.number === 44)!.text)
      .toBe('تطفئ نادية مصباح المكتب.');
  });
});

describe('detectVariantDrift', () => {
  // Invented cues: two Egyptian, one Levantine, the rest standard written
  // Arabic.
  const EGYPTIAN = ['مش عارف اعمل ايه', 'ده مكتب مش مدرسة'];
  const LEVANTINE = ['شو صار بالاجتماع'];
  const STANDARD = [
    'لا أعرف ماذا أفعل', 'هذا مكتب وليس مدرسة', 'سأعود بعد قليل',
    'أغلق الباب من فضلك', 'الاجتماع في الثالثة', 'لدينا مشكلة كبيرة',
  ];

  /** `texts` followed by enough standard cues to make `total`. */
  function among(texts: string[], total: number): SubtitleBlock[] {
    return Array.from({ length: total }, (_, i) => block(
      i + 1,
      i < texts.length ? texts[i]! : STANDARD[i % STANDARD.length]!,
    ));
  }

  /** `egyptian` Egyptian cues followed by enough standard ones to make
   * `total`. */
  function file(egyptian: number, total: number): SubtitleBlock[] {
    return among(Array.from({ length: egyptian },
      (_, i) => EGYPTIAN[i % EGYPTIAN.length]!), total);
  }

  it('names the variant a file drifted into and how much of it did', () => {
    expect(detectVariantDrift(file(2, 8), 'arabic'))
      .toEqual({ variant: 'Egyptian', cues: 2, total: 8 });
  });

  it('says nothing about the variant the run asked for', () => {
    expect(detectVariantDrift(file(8, 8), 'arabic', 'Egyptian Arabic')).toBeNull();
    // A different variant by name is still drift.
    expect(detectVariantDrift(file(8, 8), 'arabic', 'Levantine Arabic')?.variant)
      .toBe('Egyptian');
  });

  it('leaves a handful of colloquial cues alone', () => {
    // 1 of 8 is under the threshold; 2 of 8 is over it.
    expect(detectVariantDrift(file(1, 8), 'arabic')).toBeNull();
  });

  it('counts a marker only as a word of its own', () => {
    // 'مشكلة' opens with the letters of 'مش', and 'وحده' ends with 'ده'.
    const blocks = [
      block(1, 'لدينا مشكلة كبيرة'), block(2, 'تركته وحده هناك'),
      block(3, 'مرحبا'), block(4, 'مرحبا'),
    ];
    expect(detectVariantDrift(blocks, 'arabic')).toBeNull();
  });

  it('sees a marker under vocalisation the file kept', () => {
    const blocks = [block(1, 'مِش عارف'), block(2, 'ده صحيح'), block(3, 'مرحبا')];
    expect(detectVariantDrift(blocks, 'arabic')?.cues).toBe(2);
  });

  it('checks nothing for a script with no variants listed, or an empty file', () => {
    expect(detectVariantDrift(file(8, 8), 'latin')).toBeNull();
    expect(detectVariantDrift([], 'arabic')).toBeNull();
  });

  // Invented cues in forms spoken everywhere and written nowhere: none of them
  // belongs to Egyptian or Levantine, so a file built out of them named no
  // dialect and crossed no named bucket's threshold.
  const PAN_DIALECTAL = ['مين عنده الملف', 'هذي مو فكرة جيدة', 'لسه ما وصل'];

  it('catches a file that drifted without committing to a named dialect', () => {
    const blocks = [
      ...PAN_DIALECTAL.map((text, i) => block(i + 1, text)),
      ...STANDARD.map((text, i) => block(i + 4, text)),
    ];
    expect(detectVariantDrift(blocks, 'arabic'))
      .toEqual({ variant: 'Colloquial', cues: 3, total: 9 });
  });

  it('stops watching for colloquial forms once any dialect was asked for', () => {
    // Asking for a regional variant accepts colloquial writing by definition,
    // so the pan-dialectal bucket goes quiet alongside the one named — and it
    // goes quiet for a dialect with no bucket of its own too.
    const blocks = [
      ...PAN_DIALECTAL.map((text, i) => block(i + 1, text)),
      ...STANDARD.map((text, i) => block(i + 4, text)),
    ];
    expect(detectVariantDrift(blocks, 'arabic', 'Egyptian Arabic')).toBeNull();
    expect(detectVariantDrift(blocks, 'arabic', 'Gulf Arabic')).toBeNull();
  });

  it('scores colloquial writing as a whole, not one bucket at a time', () => {
    // 2 Egyptian + 1 Levantine + 3 pan-dialectal cues of 20: not one bucket
    // reaches 15% on its own, yet 30% of the file left the written form.
    const blocks = among([...EGYPTIAN, ...LEVANTINE, ...PAN_DIALECTAL], 20);
    // The union is what crossed the line; the loudest bucket names it.
    expect(detectVariantDrift(blocks, 'arabic'))
      .toEqual({ variant: 'Colloquial', cues: 6, total: 20 });
  });

  it('counts a cue several buckets claim only once', () => {
    // 'مش' and 'كده' are Egyptian, 'لازم' pan-dialectal: one drifting cue.
    const blocks = among(['مش لازم كده', ...PAN_DIALECTAL], 20);
    expect(detectVariantDrift(blocks, 'arabic')?.cues).toBe(4);
  });

  it('breaks a tie on the marker table, not on the file', () => {
    // One Egyptian cue and one Levantine, either order: the label has to be
    // the same file to file.
    expect(detectVariantDrift(among([...EGYPTIAN.slice(0, 1), ...LEVANTINE], 8),
      'arabic')?.variant).toBe('Egyptian');
    expect(detectVariantDrift(among([...LEVANTINE, ...EGYPTIAN.slice(0, 1)], 8),
      'arabic')?.variant).toBe('Egyptian');
  });

  it('counts a colloquial marker only as a word of its own', () => {
    // 'بسبب' opens with the letters of 'بس', 'موعد' with those of 'مو', and
    // 'الحاجة' is one token that is not 'حاجة'.
    const blocks = [
      block(1, 'تأخرت بسبب الازدحام'), block(2, 'لدينا موعد في الثالثة'),
      block(3, 'الحاجة أم الاختراع'), block(4, 'مرحبا'),
    ];
    expect(detectVariantDrift(blocks, 'arabic')).toBeNull();
  });

  it('reads as a sentence naming the count and the way out', () => {
    expect(variantDriftMessage({ variant: 'Egyptian', cues: 211, total: 372 }))
      .toBe('Output looks like Egyptian rather than the standard written form '
        + '(211 of 372 cues). Pass --dialect to ask for it deliberately, or rerun.');
  });
});

describe('restoreTerminalPunctuation', () => {
  it('puts back the mark the model swapped out', () => {
    expect(restoreTerminalPunctuation("That ship has sailed !", 'هذا ما قالت.', 'arabic'))
      .toBe('هذا ما قالت!');
  });

  it('maps the source question mark to the target one', () => {
    expect(restoreTerminalPunctuation('Any re-orders today ?', 'هل هناك بريد.', 'arabic'))
      .toBe('هل هناك بريد؟');
  });

  it('accepts a question mark already re-pointed for the target', () => {
    expect(restoreTerminalPunctuation('Any re-orders today ?', 'هل هناك بريد؟', 'arabic'))
      .toBe('هل هناك بريد؟');
  });

  // normalizeRtlPunctuation runs first; an ASCII mark it deliberately left
  // alone is not this pass's to overrule. Mirrored in cli/tests/test_srt_parser.py.
  it('accepts the source mark spelled the ASCII way', () => {
    expect(restoreTerminalPunctuation('Any re-orders today ?', 'هل هناك بريد?', 'arabic'))
      .toBe('هل هناك بريد?');
  });

  it('leaves a mark frozen inside a URL where it is', () => {
    const output = 'انظر www.example.com?';
    expect(normalizeRtlPunctuation(output, 'arabic')).toBe(output);
    expect(restoreTerminalPunctuation('See www.example.com?', output, 'arabic'))
      .toBe(output);
  });

  it('leaves a mark closing a run of Latin where it is', () => {
    const output = 'الفيلم "Titanic"?';
    expect(normalizeRtlPunctuation(output, 'arabic')).toBe(output);
    expect(restoreTerminalPunctuation('The movie "Titanic"?', output, 'arabic'))
      .toBe(output);
  });

  it('still re-points a mark of the wrong class', () => {
    expect(restoreTerminalPunctuation('Any re-orders today ?', 'هل هناك بريد اليوم.', 'arabic'))
      .toBe('هل هناك بريد اليوم؟');
  });

  it('reaches past a closing tag to the mark it wraps', () => {
    expect(restoreTerminalPunctuation(
      '{\\i1}Get the ropes off !{\\i0}', '{\\i1}أنزل الحبال.{\\i0}', 'arabic'))
      .toBe('{\\i1}أنزل الحبال!{\\i0}');
  });

  it('leaves an output that ends in no mark at all alone', () => {
    expect(restoreTerminalPunctuation('Hello !', 'مرحبا', 'arabic')).toBe('مرحبا');
    expect(restoreTerminalPunctuation('Hello !', '{\\i1}مرحبا{\\i0}', 'arabic'))
      .toBe('{\\i1}مرحبا{\\i0}');
  });

  it('leaves an output alone when the source ends in no mark', () => {
    expect(restoreTerminalPunctuation('and then,', 'ثم.', 'arabic')).toBe('ثم.');
    expect(restoreTerminalPunctuation('', 'ثم.', 'arabic')).toBe('ثم.');
  });

  it('keeps the ASCII question mark for a Latin target', () => {
    expect(restoreTerminalPunctuation('Really ?', 'Vraiment.', 'latin')).toBe('Vraiment?');
  });

  it('leaves a doubled mark alone', () => {
    expect(restoreTerminalPunctuation('Hello !', 'مرحبا...', 'arabic')).toBe('مرحبا...');
    expect(restoreTerminalPunctuation('Really.', 'حقا؟!', 'arabic')).toBe('حقا؟!');
  });
});

// Built by parsing a real CRLF file rather than a hand-typed "\n" fixture:
// hand-typed inputs are exactly why the CR residue shipped unnoticed.
describe('repairs on text from a parsed CRLF file', () => {
  const parsed = parseSubtitle('night-ferry.srt', CRLF_SAMPLE_SRT).blocks;
  const translated = 'في مكان ما نورس يضحك.';

  it('carries no CR into any cue', () => {
    for (const b of parsed) expect(b.text).not.toContain('\r');
  });

  it('reports one line for a one-line cue', () => {
    expect(parsed[0]!.text.split('\n').length).toBe(1);
  });

  it('reports two lines for the two-line cue that follows it', () => {
    expect(parsed[1]!.text.split('\n').length).toBe(2);
  });

  it('restores the italics the model dropped', () => {
    expect(repairTags(parsed[0]!.text, translated))
      .toEqual({ text: `${ITALIC_OPEN}${translated}${ITALIC_CLOSE}`, ok: true });
  });

  it('leaves a one-line cue as one line', () => {
    const targetLines = parsed[0]!.text.split('\n').length;
    expect(reflowToLineCount(translated, targetLines, 42, 'arabic')).toBe(translated);
  });

  it('restores a wrapping pair through stray whitespace anyway', () => {
    expect(repairTags(`${cue(7).en}\r\n\r`, translated))
      .toEqual({ text: `${ITALIC_OPEN}${translated}${ITALIC_CLOSE}`, ok: true });
  });
});

describe('empty tag pairs', () => {
  it('drops renderless pairs and keeps real ones', () => {
    expect(dropEmptyTagPairs('{\\i1}{\\i0}{\\i1}x{\\i0}{\\i1}{\\i0}')).toBe('{\\i1}x{\\i0}');
    expect(dropEmptyTagPairs('<i></i><i>x</i>')).toBe('<i>x</i>');
    expect(dropEmptyTagPairs('{\\i1}x{\\i0}')).toBe('{\\i1}x{\\i0}');
    expect(dropEmptyTagPairs('{\\an8}x')).toBe('{\\an8}x');
  });

  it('normalises a duplicated wrap, the shape a real run shipped', () => {
    const r = repairTags(cue(EMPTY_PAIR_CUE).en, cue(EMPTY_PAIR_CUE).target);
    expect(r.text).toBe('{\\i1}انتهى العبور.{\\i0}');
    expect(r.ok).toBeTrue();
  });
});

describe('dialogue dashes', () => {
  it('counts the lines that open a speaker turn, tags and spaces skipped', () => {
    expect(dialogueDashLines('- Hello\n- Goodbye')).toBe(2);
    expect(dialogueDashLines('{\\i1}- Hello{\\i0}\n  – Goodbye')).toBe(2);
    expect(dialogueDashLines('Hello there')).toBe(0);
    expect(dialogueDashLines('')).toBe(0);
  });

  it('leaves a cue whose dashes all survived alone', () => {
    expect(restoreDialogueDashes('- A\n- B', '- س\n- ص'))
      .toEqual({ text: '- س\n- ص', ok: true });
    expect(restoreDialogueDashes('One speaker', 'متحدث واحد'))
      .toEqual({ text: 'متحدث واحد', ok: true });
  });

  it('restores only the lines whose source counterpart had a dash', () => {
    expect(restoreDialogueDashes('Narration\n- B', 'سرد\nص'))
      .toEqual({ text: 'سرد\n- ص', ok: true });
  });

  it('leaves a line that kept its own dash untouched', () => {
    expect(restoreDialogueDashes('- A\n- B', '- س\nص'))
      .toEqual({ text: '- س\n- ص', ok: true });
  });

  it('refuses to guess when the line counts no longer match', () => {
    expect(restoreDialogueDashes('- A\n- B', 'س ص'))
      .toEqual({ text: 'س ص', ok: false });
  });

  it('refuses to touch a cue that gained a dash instead of losing one', () => {
    expect(restoreDialogueDashes('A\nB', '- س\n- ص'))
      .toEqual({ text: '- س\n- ص', ok: false });
  });
});

describe('scriptLeaks', () => {
  it('reports a script neither the target nor the source cue uses', () => {
    expect(scriptLeaks('He said hello.', 'قال 你好.', 'arabic')).toEqual([{
      script: 'han',
      message: "han characters appear in the translation ('你好')",
    }]);
  });

  it('says nothing about a script the source cue itself carries', () => {
    expect(scriptLeaks('Meet me at the Ritz.', 'قابلني في Ritz.', 'arabic')).toEqual([]);
  });

  it('reports a target-script word welded to a Latin one', () => {
    // The alien script, not the target's own, is what the leak is named by:
    // it is the cause a leak repeated across a file is grouped under.
    expect(scriptLeaks('Meet me at the motel.', 'قابلني في المotel.', 'arabic'))
      .toEqual([{
        script: 'latin',
        message: "'المotel' welds arabic to latin with no separator",
      }]);
    expect(scriptLeaks('Rafiq called.', 'وrafiq اتصل.', 'arabic')).toEqual([{
      script: 'latin',
      message: "'وrafiq' welds arabic to latin with no separator",
    }]);
  });

  it('treats digits, punctuation and tags as separators, not letters', () => {
    expect(scriptLeaks('Room 302 tonight.', 'الغرفة 302 الليلة.', 'arabic')).toEqual([]);
    expect(scriptLeaks('<i>Hello</i> there.', '<i>مرحبا</i>hello', 'arabic')).toEqual([]);
  });

  it('accepts the two scripts a Japanese target is written in', () => {
    expect(scriptLeaks('I eat.', '食べます。', 'japanese')).toEqual([]);
  });

  it('checks nothing for a target whose script we cannot name', () => {
    expect(scriptLeaks('Hello.', 'ሰላም 你好.', 'default')).toEqual([]);
  });

  it('stays quiet on an ordinary translation', () => {
    expect(scriptLeaks('Nadia signs the log.', 'نادية توقع السجل.', 'arabic')).toEqual([]);
  });
});

// Each shape the fixture plants, measured by the pass that exists for it.
describe('the defects the synthetic fixture plants', () => {
  it('restores the wrapping italics one cue lost entirely', () => {
    const c = cue(DROPPED_WRAP_CUE);
    expect(repairTags(c.en, c.target)).toEqual({ text: `<i>${c.target}</i>`, ok: true });
  });

  it('puts the two-line layout back on the cue that collapsed', () => {
    const c = cue(COLLAPSED_LINES_CUE);
    expect(c.en.split('\n').length).toBe(2);
    expect(c.target.split('\n').length).toBe(1);
    expect(reflowToLineCount(c.target, 2, 42, 'arabic').split('\n').length).toBe(2);
  });

  it('leaves a cue whose lines are speaker turns unreflowed', () => {
    const c = cue(DASH_INTACT_CUE);
    expect(dialogueDashLines(c.target)).toBe(2);
    expect(reflowToLineCount(c.target, 1, 42, 'arabic')).toBe(c.target);
  });

  it('gives both speakers their dash back when the lines survived', () => {
    const c = cue(DASH_DROPPED_CUE);
    expect(dialogueDashLines(c.target)).toBe(0);
    expect(restoreDialogueDashes(c.en, c.target))
      .toEqual({ text: '- أين البيان؟\n- تحت الراديو.', ok: true });
  });

  it('refuses to guess where the dashes went when the turns merged', () => {
    const c = cue(DASH_MERGED_CUE);
    expect(restoreDialogueDashes(c.en, c.target)).toEqual({ text: c.target, ok: false });
  });

  it('breaks the one line that runs past the limit', () => {
    const c = cue(LONG_LINE_CUE);
    expect(visibleLength(c.target)).toBeGreaterThan(42);
    const lines = enforceLineLength(c.target, 42, 'arabic').split('\n');
    expect(lines.length).toBe(2);
    for (const line of lines) expect(visibleLength(line)).toBeLessThanOrEqual(42);
  });

  it('leaves a line inside the Latin limit and breaks it at the CJK one', () => {
    const c = cue(OVER_CJK_LIMIT_CUE);
    expect(visibleLength(c.target)).toBeGreaterThan(16);
    expect(enforceLineLength(c.target, 42, 'arabic')).toBe(c.target);
    expect(enforceLineLength(c.target, 16, 'arabic').split('\n').length).toBe(2);
  });

  it('puts back every terminal mark the run flattened, and only those', () => {
    const restored = cuesFor('Arabic').filter(
      (c) => restoreTerminalPunctuation(c.en, c.target, 'arabic') !== c.target);
    expect(restored.map((c) => c.n)).toEqual(FLATTENED_MARK_CUES);
    expect(restoreTerminalPunctuation(cue(8).en, cue(8).target, 'arabic'))
      .toBe('أنزل الحبال عن العمود!');
  });
});

// Everything above measures the repairs against one script. These measure the
// script-dependent ones against the four the fixture adds, because Arabic
// cannot tell a per-script rule from a global one: it shares Latin's 42-column
// budget and Latin's word breaks, and only Arabic takes the RTL map at all.
describe('repairs that depend on the target script', () => {
  const LANGS: TargetLanguage[] = ['Arabic', 'Chinese', 'Japanese', 'Russian', 'Spanish'];
  /** The over-budget, two-line, dashed or tagged cue of one target file. */
  const landmark = (lang: TargetLanguage, shape: keyof ScriptLandmarks) =>
    cue(SCRIPT_LANDMARKS[lang][shape]);

  it('gives every script a file carrying all four shapes', () => {
    for (const lang of LANGS) {
      const marks = SCRIPT_LANDMARKS[lang];
      const norms = normsFor(lang);
      const over = landmark(lang, 'overBudget');
      expect(cuesFor(lang).map((c) => c.n)).toContain(marks.overBudget);
      expect(visibleLength(over.target)).toBeGreaterThan(norms.maxCharsPerLine);
      expect(over.target.split('\n').length).toBe(1);
      expect(landmark(lang, 'twoLines').en.split('\n').length).toBe(2);
      expect(dialogueDashLines(landmark(lang, 'dashes').target)).toBe(2);
      expect(findTags(landmark(lang, 'tagged').target).length).toBe(2);
    }
  });

  it('routes each target language to the script whose norms it is written to', () => {
    expect(LANGS.map((lang) => scriptFor(lang)))
      .toEqual(['arabic', 'han', 'japanese', 'cyrillic', 'latin']);
    expect(LANGS.map((lang) => normsFor(lang).maxCharsPerLine))
      .toEqual([42, 16, 16, 42, 42]);
  });

  describe('reflow', () => {
    /** The over-budget cue of one file, rewrapped to two lines against its own
     * script's budget. */
    function rewrap(lang: TargetLanguage): string[] {
      const c = landmark(lang, 'overBudget');
      const norms = normsFor(lang);
      return reflowToLineCount(c.target, 2, norms.maxCharsPerLine, norms.script)
        .split('\n');
    }

    for (const lang of ['Japanese', 'Chinese'] as const) {
      it(`breaks a ${lang} cue between characters, not between words`, () => {
        const c = landmark(lang, 'overBudget');
        const budget = normsFor(lang).maxCharsPerLine;
        const lines = rewrap(lang);
        expect(lines.length).toBe(2);
        // Joined with nothing at all: the break landed inside the writing, and
        // no separator was invented to hold the halves apart.
        expect(lines.join('')).toBe(c.target);
        for (const line of lines) expect(visibleLength(line)).toBeLessThanOrEqual(budget);
        // The word reflow has one unit to work with and cannot split at all,
        // which is exactly what shipped before the script was consulted.
        expect(reflowToLineCount(c.target, 2, budget, 'latin')).toBe(c.target);
      });
    }

    for (const lang of ['Russian', 'Spanish'] as const) {
      it(`breaks a ${lang} cue between words, not between characters`, () => {
        const c = landmark(lang, 'overBudget');
        const budget = normsFor(lang).maxCharsPerLine;
        const lines = rewrap(lang);
        expect(lines.length).toBe(2);
        // Rejoining on the space proves no word was cut in half.
        expect(lines.join(' ')).toBe(c.target);
        for (const line of lines) expect(visibleLength(line)).toBeLessThanOrEqual(budget);
        // The same cue under a no-space script breaks mid-word instead.
        const charWrapped = reflowToLineCount(c.target, 2, budget, 'han').split('\n');
        expect(charWrapped.length).toBe(2);
        expect(charWrapped.join(' ')).not.toBe(c.target);
      });
    }
  });

  it('measures one 40-character cue against the target script, not a global limit', () => {
    const c = cue(PER_SCRIPT_BUDGET_CUE);
    expect(visibleLength(c.target)).toBe(40);
    const latin = normsFor('Spanish');
    const han = normsFor('Chinese');
    expect(enforceLineLength(c.target, latin.maxCharsPerLine, latin.script)).toBe(c.target);
    expect(enforceLineLength(c.target, han.maxCharsPerLine, han.script).split('\n').length)
      .toBe(2);
  });

  it('re-points ASCII punctuation for Arabic and leaves the other scripts byte-identical', () => {
    expect(normalizeRtlPunctuation('أين البيان? تحت الراديو', 'arabic'))
      .toBe('أين البيان؟ تحت الراديو');
    for (const lang of ['Russian', 'Japanese', 'Spanish'] as const) {
      for (const c of cuesFor(lang)) {
        expect(normalizeRtlPunctuation(c.target, normsFor(lang).script)).toBe(c.target);
        // Not just the script gate: Arabic's own map finds nothing to re-point
        // in a cue with no Arabic letter beside the mark.
        expect(normalizeRtlPunctuation(c.target, 'arabic')).toBe(c.target);
      }
    }
  });

  it('leaves a file outside the Arabic script unvocalized', () => {
    for (const lang of ['Russian', 'Japanese', 'Spanish', 'Chinese'] as const) {
      const blocks = cuesFor(lang).map((c) => block(c.n, c.target));
      expect(normalizeDiacritics(blocks, normsFor(lang).script)).toBe(blocks);
      // Even asked for the Arabic pass, there are no marks to strip.
      expect(normalizeDiacritics(blocks, 'arabic').map((b) => b.text))
        .toEqual(blocks.map((b) => b.text));
    }
  });

  it('finds no variant drift in a file with no Arabic markers to drift into', () => {
    for (const lang of ['Russian', 'Japanese'] as const) {
      const blocks = cuesFor(lang).map((c) => block(c.n, c.target));
      expect(detectVariantDrift(blocks, normsFor(lang).script)).toBeNull();
      // The check is table-driven, so the Arabic table finding nothing here is
      // the assertion that matters — an unlisted script exits one line earlier.
      expect(detectVariantDrift(blocks, 'arabic')).toBeNull();
    }
  });

  describe('script leaks', () => {
    /** Every cue of one file the leak detector has something to say about. */
    function flagged(lang: TargetLanguage): number[] {
      return cuesFor(lang)
        .filter((c) => scriptLeaks(c.en, c.target, normsFor(lang).script).length)
        .map((c) => c.n);
    }

    it('flags the Latin welded into a Japanese cue', () => {
      const c = cue(WELDED_LATIN_CUE);
      // The English source is Latin too, so a bare Latin word would be excused;
      // welding it to the kana around it is what breaks the rendering.
      expect(scriptLeaks(c.en, c.target, 'japanese')).toEqual([{
        script: 'latin',
        message: "'ラフィクがpierから電話した' welds kana to latin with no separator",
      }]);
      expect(flagged('Japanese')).toEqual([WELDED_LATIN_CUE]);
    });

    it('flags the Han characters left in a Russian cue', () => {
      const c = cue(LEAKED_HAN_CUE);
      expect(scriptLeaks(c.en, c.target, 'cyrillic')).toEqual([{
        script: 'han',
        message: "han characters appear in the translation ('你好')",
      }]);
      expect(flagged('Russian')).toEqual([LEAKED_HAN_CUE]);
    });

    it('says nothing about the files that planted no leak', () => {
      expect(flagged('Chinese')).toEqual([]);
      expect(flagged('Spanish')).toEqual([]);
    });
  });
});
