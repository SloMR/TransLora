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

  it('round-trips ASS (styles are regenerated — subsrt-ts limitation)', () => {
    const src =
      '[Script Info]\n' +
      'Title: MyTitle\n' +
      'ScriptType: v4.00+\n\n' +
      '[V4+ Styles]\n' +
      'Format: Name, Fontname, Fontsize\n' +
      'Style: Default,Arial,20\n\n' +
      '[Events]\n' +
      'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n' +
      'Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello\n' +
      'Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,Line one\\NLine two\n';
    const doc = parseSubtitle('a.ass', src);
    expect(doc.format).toBe('ass');
    expect(doc.blocks.length).toBe(2);
    const out = doc.rebuild(doc.blocks);
    expect(out).toContain('[Events]');
    expect(out).toContain('Dialogue:');
  });

  it('round-trips SBV', () => {
    const src =
      '0:00:01.000,0:00:02.500\nHello\n\n' +
      '0:00:03.000,0:00:04.500\nTwo\nlines\n';
    const doc = parseSubtitle('a.sbv', src);
    expect(doc.format).toBe('sbv');
    expect(doc.blocks.length).toBe(2);
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
});
