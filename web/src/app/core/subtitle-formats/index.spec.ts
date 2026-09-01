import { parseSubtitle } from './index';

describe('parseSubtitle', () => {
  it('round-trips SRT preserving timestamps and italic tags', () => {
    const src =
      '1\n00:00:01,000 --> 00:00:02,500\nHello <i>world</i>\n\n' +
      '2\n00:00:03,000 --> 00:00:04,500\nTwo\nlines\n';
    const doc = parseSubtitle('a.srt', src);
    expect(doc.format).toBe('srt');
    expect(doc.blocks.length).toBe(2);
    const out = doc.rebuild(doc.blocks);
    expect(out).toContain('00:00:01,000 --> 00:00:02,500');
    expect(out).toContain('<i>');
  });

  it('round-trips VTT and keeps the WEBVTT header', () => {
    const src =
      'WEBVTT\n\n' +
      '00:00:01.000 --> 00:00:02.500\nHello\n\n' +
      '00:00:03.000 --> 00:00:04.500\nTwo\nlines\n';
    const doc = parseSubtitle('a.vtt', src);
    expect(doc.format).toBe('vtt');
    expect(doc.blocks.length).toBe(2);
    expect(doc.rebuild(doc.blocks).startsWith('WEBVTT')).toBeTrue();
  });

  it('preserves ASS script info, styles and \\N line breaks', () => {
    const src =
      '[Script Info]\n' +
      'Title: MyTitle\n' +
      'ScriptType: v4.00+\n\n' +
      '[V4+ Styles]\n' +
      'Format: Name, Fontname, Fontsize\n' +
      'Style: Default,Arial,20\n\n' +
      '[Events]\n' +
      'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n' +
      'Comment: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,not dialogue\n' +
      'Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello\n' +
      'Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,Line one\\NLine two\n';
    const doc = parseSubtitle('a.ass', src);
    expect(doc.format).toBe('ass');
    // Comment events are never translated.
    expect(doc.blocks.length).toBe(2);
    expect(doc.blocks[0]!.timestamp).toBe('00:00:01,000 --> 00:00:02,500');
    expect(doc.blocks[1]!.text).toBe('Line one\nLine two');
    const out = doc.rebuild(doc.blocks);
    expect(out).toContain('Title: MyTitle');
    expect(out).toContain('Style: Default,Arial,20');
    expect(out).toContain('Comment: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,not dialogue');
    expect(out).toContain('Line one\\NLine two');
  });

  it('round-trips SSA', () => {
    const src =
      '[Script Info]\n' +
      'ScriptType: v4.00\n\n' +
      '[V4 Styles]\n' +
      'Format: Name, Fontname, Fontsize\n' +
      'Style: Default,Arial,20\n\n' +
      '[Events]\n' +
      'Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n' +
      'Dialogue: Marked=0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hi there\n';
    const doc = parseSubtitle('a.ssa', src);
    expect(doc.format).toBe('ssa');
    expect(doc.blocks.length).toBe(1);
    expect(doc.blocks[0]!.text).toBe('Hi there');
    const out = doc.rebuild([{ ...doc.blocks[0]!, text: 'Salut' }]);
    expect(out).toContain('[Events]');
    expect(out).toContain('Dialogue: Marked=0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Salut');
  });

  it('round-trips SBV', () => {
    const src =
      '0:00:01.000,0:00:02.500\nHello\n\n' +
      '0:00:03.000,0:00:04.500\nTwo\nlines\n';
    const doc = parseSubtitle('a.sbv', src);
    expect(doc.format).toBe('sbv');
    expect(doc.blocks.length).toBe(2);
    expect(doc.blocks[0]!.text).toBe('Hello');
    expect(doc.blocks[1]!.text).toBe('Two\nlines');
    const out = doc.rebuild([
      { ...doc.blocks[0]!, text: 'Salut' },
      { ...doc.blocks[1]! },
    ]);
    expect(out).toContain('00:00:01.000,00:00:02.500');
    expect(out).toContain('Salut');
  });

  it('round-trips MicroDVD .sub and re-emits every line break as |', () => {
    const src = '{1}{2}Line one|Line two\n{3}{4}Another\n';
    const doc = parseSubtitle('a.sub', src);
    expect(doc.format).toBe('sub');
    expect(doc.blocks.length).toBe(2);
    // MicroDVD '|' line-break becomes '\n' in normalized text.
    expect(doc.blocks[0]!.text).toBe('Line one\nLine two');
    const out = doc.rebuild([
      { ...doc.blocks[0]!, text: 'Un\nDeux\nTrois' },
      { ...doc.blocks[1]!, text: 'Autre' },
    ]);
    // Every break, not just the first, or the tail lines are lost.
    expect(out).toContain('{1}{2}Un|Deux|Trois');
    expect(out).toContain('{3}{4}Autre');
  });

  it('normalizes blocks to sequential numbers starting at 1', () => {
    const src = '1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:03,000 --> 00:00:04,000\nB\n';
    const blocks = parseSubtitle('a.srt', src).blocks;
    expect(blocks.map((b) => b.number)).toEqual([1, 2]);
  });

  it('throws on unsupported extension', () => {
    expect(() => parseSubtitle('a.xyz', 'irrelevant')).toThrowError(/Unsupported/);
  });

  it('applies translated text on rebuild', () => {
    const src = '1\n00:00:01,000 --> 00:00:02,500\nhello\n';
    const doc = parseSubtitle('a.srt', src);
    const translated = doc.blocks.map((b) => ({ ...b, text: b.text.split('').reverse().join('') }));
    const out = doc.rebuild(translated);
    expect(out).toContain('olleh');
    expect(out).not.toContain('hello');
  });

  it('keeps the source text for cues the translated list does not cover', () => {
    // A short list means a batch went missing; the untranslated cue must
    // survive rather than shift the whole file up by one.
    const cases: [string, string, string][] = [
      ['a.srt', '1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n2\n00:00:03,000 --> 00:00:04,000\nsecond\n', 'second'],
      ['a.vtt', 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nfirst\n\n00:00:03.000 --> 00:00:04.000\nsecond\n', 'second'],
      ['a.ass', '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,first\nDialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,second\n', 'second'],
      ['a.sub', '{1}{2}first\n{3}{4}second\n', 'second'],
    ];
    for (const [name, src, tail] of cases) {
      const doc = parseSubtitle(name, src);
      const out = doc.rebuild([{ ...doc.blocks[0]!, text: 'TRANSLATED' }]);
      expect(out).withContext(name).toContain('TRANSLATED');
      expect(out).withContext(name).toContain(tail);
    }
  });
});

// Real-world subtitle files are CRLF, and pysubs2/subsrt-ts both hand back the
// residue: a stray \r plus a phantom trailing line, so a one-line cue reported
// two and every downstream repair worked on the wrong text.
describe('CRLF cue text', () => {
  const fixtures: [string, string][] = [
    [
      'a.srt',
      '1\r\n00:00:01,000 --> 00:00:02,500\r\n{\\i1}Little package !{\\i0}\r\n\r\n' +
        '2\r\n00:00:03,000 --> 00:00:04,500\r\nTwo\r\nlines\r\n',
    ],
    [
      'a.vtt',
      'WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.500\r\n{\\i1}Little package !{\\i0}\r\n\r\n' +
        '00:00:03.000 --> 00:00:04.500\r\nTwo\r\nlines\r\n',
    ],
    [
      'a.ass',
      '[Events]\r\n' +
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n' +
        'Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}Little package !{\\i0}\r\n' +
        'Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,Two\\Nlines\r\n',
    ],
    [
      'a.sbv',
      '0:00:01.000,0:00:02.500\r\n{\\i1}Little package !{\\i0}\r\n\r\n' +
        '0:00:03.000,0:00:04.500\r\nTwo\r\nlines\r\n',
    ],
    ['a.sub', '{1}{2}{\\i1}Little package !{\\i0}\r\n{3}{4}Two|lines\r\n'],
  ];

  for (const [name, src] of fixtures) {
    it(`hands ${name} cue text with no CR and a true line count`, () => {
      const blocks = parseSubtitle(name, src).blocks;
      expect(blocks.length).withContext(name).toBe(2);
      for (const b of blocks) {
        expect(b.text).withContext(name).not.toContain('\r');
      }
      expect(blocks[0]!.text.split('\n').length).withContext(name).toBe(1);
      expect(blocks[1]!.text.split('\n').length).withContext(name).toBe(2);
    });
  }

  it('keeps an interior blank line, which serializeLite is what collapses', () => {
    const src = '1\r\n00:00:01,000 --> 00:00:02,500\r\nfirst\r\n \r\nsecond\r\n';
    expect(parseSubtitle('a.srt', src).blocks[0]!.text).toBe('first\n \nsecond');
  });

  it('still rebuilds a CRLF file', () => {
    const src = '1\r\n00:00:01,000 --> 00:00:02,500\r\nhello\r\n';
    const doc = parseSubtitle('a.srt', src);
    expect(doc.rebuild([{ ...doc.blocks[0]!, text: 'bonjour' }]))
      .toBe('1\n00:00:01,000 --> 00:00:02,500\nbonjour\n');
  });
});
