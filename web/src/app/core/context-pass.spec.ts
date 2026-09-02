// Ported from cli/tests/test_context_pass.py — the test names are kept close
// to the Python ones so drift between the two glossary parsers is greppable.

import { parseAttributionResponse, parseContextResponse } from './context-parse';
import {
  CONSISTENCY_MIN_CHARS,
  CONSISTENCY_MIN_OCCURRENCES,
  CharacterHint,
  FileContext,
  Gender,
  MAX_IDIOMS,
  MAX_TERMS,
  PHRASE_LIMIT,
  PhraseSplit,
  SceneHint,
  TermHint,
  driftCause,
  enrichScenesWithBlockText,
  findInconsistentPhrases,
  needsAttribution,
  phraseSplitMessage,
  recurringPhrases,
  serializeForScan,
} from './context-pass';
import { SubtitleBlock } from './srt-parser';
import {
  CONSISTENT_PHRASE,
  REPEATED_PHRASES,
  SPLIT_PHRASE,
  cuesFor,
} from './testdata/aligned-cues';

const TEST_BUDGET = 24_000;

function block(n: number, text: string): SubtitleBlock {
  return { number: n, timestamp: '00:00:00,000 --> 00:00:01,000', text };
}

function char(source: string, target: string, gender: Gender): CharacterHint {
  return { source, target, gender };
}

function scene(
  start: number,
  end: number,
  description: string,
  participants: string[] = [],
  attribution: Record<number, string> = {},
): SceneHint {
  return { start, end, description, participants, attribution };
}

function ctx(parts: {
  register?: string;
  characters?: CharacterHint[];
  terms?: TermHint[];
  idioms?: TermHint[];
  scenes?: SceneHint[];
  notes?: string[];
}): FileContext {
  return new FileContext(
    parts.register ?? '',
    parts.characters ?? [],
    parts.terms ?? [],
    parts.idioms ?? [],
    parts.scenes ?? [],
    parts.notes ?? [],
  );
}

describe('parseContextResponse', () => {
  it('parses a well-formed tagged response', () => {
    const raw = `
<register>
Target language, neutral register
</register>
<characters>
Alice => TargetAlice | female
Bob => TargetBob | male
Stranger => TargetStranger | unknown
</characters>
<terms>
headquarters => TargetHQ
</terms>
<notes>
- Workplace drama
- Casual register
</notes>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.register).toBe('Target language, neutral register');
    expect(parsed.characters).toEqual([
      char('Alice', 'TargetAlice', 'female'),
      char('Bob', 'TargetBob', 'male'),
      char('Stranger', 'TargetStranger', 'unknown'),
    ]);
    expect(parsed.terms).toEqual([{ source: 'headquarters', target: 'TargetHQ' }]);
    expect(parsed.notes).toEqual(['Workplace drama', 'Casual register']);
  });

  it('collapses whitespace and a bullet in the register line', () => {
    const raw = `
<register>
  - Target language,
    casual
</register>
<characters>
</characters>
`;
    expect(parseContextResponse(raw).register).toBe('Target language, casual');
  });

  it('tolerates a missing closing tag', () => {
    // Scan models sometimes drop </scenes> before the next section; the body
    // should still parse up to the next opening tag.
    const raw = `
<register>
Target variant
</register>
<characters>
Alice => آليس | female
</characters>
<scenes>
1-5 => Alice speaks
6-10 => Alice continues
<notes>
- tone note
</notes>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.register).toBe('Target variant');
    expect(parsed.scenes.length).toBe(2);
    expect(parsed.notes).toEqual(['tone note']);
  });

  it('tolerates missing sections and bullet markers', () => {
    const raw = `
<characters>
- Alice => TargetAlice | female
* Bob => TargetBob | MALE
</characters>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.characters.map((h) => h.source)).toEqual(['Alice', 'Bob']);
    expect(parsed.characters[1]!.gender).toBe('male');
    expect(parsed.terms).toEqual([]);
    expect(parsed.notes).toEqual([]);
  });

  it('returns an empty context for garbage', () => {
    expect(parseContextResponse('').isEmpty()).toBe(true);
    expect(parseContextResponse('sorry I cannot help').isEmpty()).toBe(true);
  });

  it('parses scene ranges and single-block scenes', () => {
    const raw = `
<scenes>
97-117 => Alice and Carol discuss a concern
279-284 => Dave talks about his daughters
42 => Bob monologues
</scenes>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.scenes.map((s) => [s.start, s.end, s.description])).toEqual([
      [97, 117, 'Alice and Carol discuss a concern'],
      [279, 284, 'Dave talks about his daughters'],
      [42, 42, 'Bob monologues'],
    ]);
    // No characters section, so no participants can be detected.
    for (const s of parsed.scenes) expect(s.participants).toEqual([]);
  });

  it('detects scene participants from the character list', () => {
    const raw = `
<characters>
Alice => Alice | female
Carol => Carol | female
Dave => Dave | male
</characters>
<scenes>
97-117 => Alice tells Carol her worries
279-284 => Dave complains about his daughters
</scenes>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.scenes[0]!.participants).toEqual(['Alice', 'Carol']);
    expect(parsed.scenes[1]!.participants).toEqual(['Dave']);
  });

  it('resolves scene participants written with the target-language name', () => {
    const raw = `
<characters>
Alice => آليس | female
Carol => كارول | female
</characters>
<scenes>
97-117 => آليس تخبر كارول بمخاوفها
</scenes>
`;
    expect(parseContextResponse(raw).scenes[0]!.participants).toEqual(['Alice', 'Carol']);
  });

  it('rejects a short alias that only matches inside another word', () => {
    // "لو" would substring-match inside "الوقوف"; aliases under MIN_NAME_LEN
    // are never registered, so it cannot.
    const raw = `
<characters>
Lou => لو | male
Alice => آليس | female
</characters>
<scenes>
10-20 => نصائح حول الوقوف وتأثيره على الصحة
21-25 => آليس تطمئن
</scenes>
`;
    const parsed = parseContextResponse(raw);
    expect(parsed.scenes[0]!.participants).toEqual([]);
    expect(parsed.scenes[1]!.participants).toEqual(['Alice']);
  });

  it('skips malformed scene lines', () => {
    const raw = `
<scenes>
- 10-20 => Two characters (M, F)
- no-range => missing range
- 30 40 => bad separator
- 50-60 =>
- 70-80 => good one
</scenes>
`;
    expect(parseContextResponse(raw).scenes.map((s) => [s.start, s.end])).toEqual([
      [10, 20],
      [70, 80],
    ]);
  });

  it('swaps a reversed scene range', () => {
    const parsed = parseContextResponse('<scenes>\n200-100 => Accidentally reversed\n</scenes>');
    expect(parsed.scenes[0]!.start).toBe(100);
    expect(parsed.scenes[0]!.end).toBe(200);
  });

  it('caps characters, terms, scenes and notes', () => {
    const lines = (n: number, make: (i: number) => string) =>
      Array.from({ length: n }, (_, i) => make(i + 1)).join('\n');
    const raw =
      `<characters>\n${lines(25, (i) => `C${i} => T${i} | female`)}\n</characters>\n` +
      `<terms>\n${lines(30, (i) => `term${i} => t${i}`)}\n</terms>\n` +
      `<scenes>\n${lines(50, (i) => `${i} => scene ${i}`)}\n</scenes>\n` +
      `<notes>\n${lines(8, (i) => `- note ${i}`)}\n</notes>`;
    const parsed = parseContextResponse(raw);
    expect(parsed.characters.length).toBe(20);
    expect(parsed.terms.length).toBe(MAX_TERMS);
    expect(parsed.scenes.length).toBe(40);
    expect(parsed.notes.length).toBe(4);
  });
});

describe('FileContext.renderForBatch', () => {
  it('includes the register line even when nothing else matches', () => {
    const c = ctx({
      register: 'Target language, neutral',
      characters: [char('Alice', 'TargetAlice', 'female')],
    });
    const rendered = c.renderForBatch([block(1, 'Nobody named here.')]);
    expect(rendered).toContain('Target register: Target language, neutral');
    expect(rendered).not.toContain('Alice');
  });

  it('includes only the characters and terms named in the batch', () => {
    const c = ctx({
      characters: [char('Alice', 'TargetAlice', 'female'), char('Bob', 'TargetBob', 'male')],
      terms: [{ source: 'headquarters', target: 'TargetHQ' }],
      notes: ['Workplace drama'],
    });
    const rendered = c.renderForBatch([block(1, 'Alice, come here.'), block(2, "I'm tired.")]);
    expect(rendered).toContain('Alice');
    expect(rendered).not.toContain('Bob');
    expect(rendered).not.toContain('headquarters');
    expect(rendered).toContain('Workplace drama');
  });

  it('is empty when nothing matches and there are no notes', () => {
    const c = ctx({ characters: [char('Alice', 'TargetAlice', 'female')] });
    expect(c.renderForBatch([block(1, "I'm tired.")])).toBe('');
  });

  it('does not match a name inside a longer Latin word', () => {
    const c = ctx({ characters: [char('Alice', 'TargetAlice', 'female')] });
    expect(c.renderForBatch([block(1, 'Alicebot is online.')])).not.toContain('Alice');
  });

  it('matches a name written next to Arabic or CJK letters', () => {
    // Uncased scripts run words together (clitics, no spaces), so an adjacent
    // letter there is not evidence the name is part of a longer word.
    const arabic = ctx({ characters: [char('آليس', 'آليس', 'female')] });
    expect(arabic.renderForBatch([block(1, 'وآليس تطمئن')])).toContain('آليس');
    const cjk = ctx({ characters: [char('田中', '田中', 'male')] });
    expect(cjk.renderForBatch([block(1, '田中さんです')])).toContain('田中');
  });

  it('does not match a name touching a digit or underscore', () => {
    const c = ctx({ characters: [char('Alice', 'TargetAlice', 'female')] });
    expect(c.renderForBatch([block(1, 'Alice_2 signed in.')])).toBe('');
  });

  it('includes only scenes overlapping the batch', () => {
    const c = ctx({
      scenes: [scene(1, 5, 'Scene A'), scene(10, 20, 'Scene B'), scene(50, 60, 'Scene C')],
    });
    const rendered = c.renderForBatch([block(15, 'line'), block(25, 'line')]);
    expect(rendered).toContain('Scene B');
    expect(rendered).not.toContain('Scene A');
    expect(rendered).not.toContain('Scene C');
    expect(rendered).toContain('Blocks 10-20');
  });

  it('counts a boundary touch as an overlap', () => {
    const c = ctx({ scenes: [scene(5, 10, 'Boundary scene')] });
    expect(c.renderForBatch([block(10, 'line'), block(15, 'line')])).toContain('Boundary scene');
  });

  it('includes scene participants even when unnamed in the batch text', () => {
    const c = ctx({
      characters: [char('Carol', 'Carol', 'female'), char('Dave', 'Dave', 'male')],
      scenes: [scene(1, 2, 'A conversation', ['Carol'])],
    });
    const out = c.renderForBatch([block(1, 'Drink water.'), block(2, 'Oh, right.')]);
    expect(out).toContain('Carol => Carol (female)');
    expect(out).not.toContain('Dave');
  });

  it('tags scene participants with their gender', () => {
    const c = ctx({
      characters: [char('Alice', 'Alice', 'female'), char('Bob', 'Bob', 'male')],
      scenes: [scene(10, 20, 'Alice gives Bob an update', ['Alice', 'Bob'])],
    });
    const rendered = c.renderForBatch([block(10, 'x'), block(20, 'y')]);
    expect(rendered).toContain('[Alice (F), Bob (M)]');
    expect(rendered).toContain('Alice gives Bob an update');
  });

  it('renders the speakers line when attribution is present', () => {
    const c = ctx({
      characters: [char('Alice', 'Alice', 'female'), char('Bob', 'Bob', 'male')],
      scenes: [
        scene(10, 12, 'Alice advises Bob', ['Alice', 'Bob'], {
          10: 'Alice',
          11: 'Alice',
          12: 'Bob',
        }),
      ],
    });
    const out = c.renderForBatch([block(10, 'x'), block(11, 'y'), block(12, 'z')]);
    expect(out).toContain('speakers: 10=Alice 11=Alice 12=Bob');
  });

  it('falls back to the description when a scene has no participants', () => {
    const c = ctx({ scenes: [scene(1, 5, 'Crowd murmurs')] });
    expect(c.renderForBatch([block(1, 'x')])).toContain('- Blocks 1-5: Crowd murmurs');
  });
});

describe('FileContext.hasCorrections', () => {
  it('is true when the batch names a character', () => {
    const c = ctx({ characters: [char('Alice', 'TargetAlice', 'female')] });
    expect(c.hasCorrections([block(1, 'Alice, wait.')])).toBe(true);
  });

  it('is true for an idiom-only slice', () => {
    // The reviewer's idiom fix could otherwise only fire on a batch that
    // happened to also name a character or a term.
    const c = ctx({ idioms: [{ source: 'break a leg', target: 'بالتوفيق' }] });
    expect(c.hasCorrections([block(1, 'Break a leg out there.')])).toBe(true);
  });

  it('is false when the batch uses none of the pinned idioms', () => {
    const c = ctx({ idioms: [{ source: 'break a leg', target: 'بالتوفيق' }] });
    expect(c.hasCorrections([block(1, 'Nothing to correct.')])).toBe(false);
  });

  it('is false for a register-only slice the reviewer could not act on', () => {
    const c = ctx({ register: 'formal', notes: ['tone note'] });
    expect(c.hasCorrections([block(1, 'Nothing to correct.')])).toBe(false);
  });
});

describe('FileContext.isEmpty', () => {
  it('considers the register', () => {
    expect(new FileContext().isEmpty()).toBe(true);
    expect(ctx({ register: 'Target language' }).isEmpty()).toBe(false);
  });

  it('considers scenes', () => {
    expect(ctx({ scenes: [scene(1, 2, 'x')] }).isEmpty()).toBe(false);
  });
});

describe('enrichScenesWithBlockText', () => {
  it('pulls names from block text when the description omits them', () => {
    const c = ctx({
      characters: [char('Alice', 'Alice', 'female'), char('Dave', 'Dave', 'male')],
      scenes: [scene(1, 3, 'A tense conversation')],
    });
    const blocks = [
      block(1, 'Alice, I need a word with you.'),
      block(2, 'About what?'),
      block(3, "Dave said he's leaving."),
    ];
    expect(enrichScenesWithBlockText(c, blocks).scenes[0]!.participants).toEqual([
      'Alice',
      'Dave',
    ]);
  });

  it('keeps description order and appends the rest', () => {
    const c = ctx({
      characters: [char('Alice', 'Alice', 'female'), char('Dave', 'Dave', 'male')],
      scenes: [scene(1, 2, 'Dave talks to someone', ['Dave'])],
    });
    const blocks = [block(1, 'Alice, look at this.'), block(2, 'Dave, calm down.')];
    expect(enrichScenesWithBlockText(c, blocks).scenes[0]!.participants).toEqual([
      'Dave',
      'Alice',
    ]);
  });

  it('drops a description-named participant absent from the blocks', () => {
    const c = ctx({
      characters: [char('Alice', 'Alice', 'female'), char('Dave', 'Dave', 'male')],
      scenes: [scene(1, 2, 'Alice and Dave talk', ['Alice', 'Dave'])],
    });
    const blocks = [block(1, 'Dave, are you okay?'), block(2, "I'm fine.")];
    expect(enrichScenesWithBlockText(c, blocks).scenes[0]!.participants).toEqual(['Dave']);
  });

  it('clamps a scene range to the blocks the file actually has', () => {
    const c = ctx({ scenes: [scene(1, 9_999, 'Hallucinated range')] });
    const blocks = [block(1, 'a'), block(2, 'b'), block(3, 'c')];
    const enriched = enrichScenesWithBlockText(c, blocks).scenes[0]!;
    expect([enriched.start, enriched.end]).toEqual([1, 3]);
  });

  it('drops a scene that falls entirely outside the file', () => {
    const c = ctx({ scenes: [scene(900, 999, 'Not in this file'), scene(1, 2, 'Real')] });
    const blocks = [block(1, 'a'), block(2, 'b')];
    expect(
      enrichScenesWithBlockText(c, blocks).scenes.map((s) => s.description),
    ).toEqual(['Real']);
  });
});

describe('needsAttribution', () => {
  const genders = new Map<string, Gender>([
    ['alice', 'female'],
    ['bob', 'male'],
    ['stranger', 'unknown'],
  ]);

  it('triggers when a multi-block scene mixes known genders', () => {
    expect(needsAttribution(scene(1, 5, 'x', ['Alice', 'Bob']), genders)).toBe(true);
    // One participant: nobody to tell apart, whatever the target.
    expect(needsAttribution(scene(1, 5, 'x', ['Alice']), genders)).toBe(false);
    expect(needsAttribution(scene(1, 5, 'x', []), genders)).toBe(false);
  });

  it('triggers on an unknown gender when the target inflects for one', () => {
    // The gap the call exists to close: a scene whose genders the scan could
    // not name is exactly the one an Arabic or Hebrew target needs answered.
    const pair = scene(1, 5, 'x', ['Alice', 'Stranger']);
    expect(needsAttribution(pair, genders, false, true)).toBe(true);
    expect(needsAttribution(pair, genders, false, false)).toBe(false);
    // A target that does not inflect gains nothing from a lone speaker either.
    expect(needsAttribution(scene(1, 5, 'x', ['Alice']), genders, false, true))
      .toBe(false);
  });

  it('skips scenes shorter than the minimum block count', () => {
    expect(needsAttribution(scene(1, 2, 'x', ['Alice', 'Bob']), genders)).toBe(false);
    expect(needsAttribution(scene(1, 2, 'x', ['Alice', 'Bob']), genders, false, true))
      .toBe(false);
  });

  describe('with full attribution asked for', () => {
    it('fires for any scene with two participants, gender known or not', () => {
      expect(needsAttribution(scene(1, 5, 'x', ['Alice', 'Stranger']), genders, true))
        .toBe(true);
    });

    it('fires for a lone participant whatever the target', () => {
      expect(needsAttribution(scene(1, 5, 'x', ['Alice']), genders, true, true))
        .toBe(true);
      expect(needsAttribution(scene(1, 5, 'x', ['Alice']), genders, true, false))
        .toBe(true);
    });

    it('still needs a participant and the minimum block count', () => {
      expect(needsAttribution(scene(1, 5, 'x', []), genders, true, true)).toBe(false);
      expect(needsAttribution(scene(1, 2, 'x', ['Alice', 'Bob']), genders, true, true))
        .toBe(false);
    });
  });
});

describe('parseContextResponse idioms', () => {
  it('parses the section into its own list, separate from the terms', () => {
    const parsed = parseContextResponse(`
<terms>
headquarters => المقر
</terms>
<idioms>
- that ship has sailed => تعبير مجازي
turns at the gate => يتجاوز الحدود
</idioms>
`);
    expect(parsed.terms).toEqual([{ source: 'headquarters', target: 'المقر' }]);
    expect(parsed.idioms).toEqual([
      { source: "that ship has sailed", target: 'تعبير مجازي' },
      { source: 'turns at the gate', target: 'يتجاوز الحدود' },
    ]);
  });

  it('caps the list the prompt asked for', () => {
    const lines = Array.from({ length: MAX_IDIOMS + 6 },
      (_, i) => `idiom ${i} => target ${i}`).join('\n');
    expect(parseContextResponse(`<idioms>\n${lines}\n</idioms>`).idioms.length)
      .toBe(MAX_IDIOMS);
  });

  it('drops an idiom whose source is already a term', () => {
    // The worst defect the graded run produced: one phrase in both tables, the
    // idiom's value a dictionary definition, pasted over the punchline.
    const parsed = parseContextResponse(`
<terms>
that's what she said => هذا ما قالته هي
</terms>
<idioms>
That's What She Said => هذا تعبير ساخر يستخدم للرد على جملة تحتمل معنى مزدوجا
</idioms>
`);
    expect(parsed.terms.map((t) => t.source)).toEqual(["that's what she said"]);
    expect(parsed.idioms).toEqual([]);
  });

  it('matches the two tables on a folded key, not on spelling', () => {
    const parsed = parseContextResponse(`
<terms>
That  Ship   Has Sailed => فات الأوان
</terms>
<idioms>
that ship has sailed => لقد فات أوان ذلك تماما
</idioms>
`);
    expect(parsed.idioms).toEqual([]);
  });

  it('drops a target that explains the idiom instead of translating it', () => {
    const parsed = parseContextResponse(`
<idioms>
break a leg => بالتوفيق
piece of cake => عبارة تقال للدلالة على أن الأمر سهل للغاية ولا يحتاج جهدا
</idioms>
`);
    expect(parsed.idioms).toEqual([{ source: 'break a leg', target: 'بالتوفيق' }]);
  });

  it('keeps a long target that a long source earns', () => {
    const source = 'the early bird catches the worm and keeps it';
    const target = 'من جد وجد ومن زرع حصد ومن سار على الدرب وصل إليه';
    expect(parseContextResponse(`<idioms>\n${source} => ${target}\n</idioms>`).idioms)
      .toEqual([{ source, target }]);
  });

  it('prunes before the cap, so a poisoned entry costs no slot', () => {
    const lines = Array.from({ length: MAX_IDIOMS + 1 },
      (_, i) => `idiom ${i} => target ${i}`).join('\n');
    const parsed = parseContextResponse(
      `<terms>\nidiom 0 => الهدف\n</terms>\n<idioms>\n${lines}\n</idioms>`);
    expect(parsed.idioms.length).toBe(MAX_IDIOMS);
    expect(parsed.idioms.map((h) => h.source)).not.toContain('idiom 0');
  });

  it('reads an empty section as no idioms rather than as the next one', () => {
    const parsed = parseContextResponse(`
<idioms>
</idioms>
<scenes>
1-5 => Alice speaks
</scenes>
`);
    expect(parsed.idioms).toEqual([]);
    expect(parsed.scenes.length).toBe(1);
  });
});

describe('idiom hints', () => {
  const c = ctx({
    idioms: [
      { source: "that ship has sailed", target: 'تعبير مجازي' },
      { source: 'turns at the gate', target: 'يتجاوز الحدود' },
    ],
  });

  it('renders under a heading that forbids a word-for-word rendering', () => {
    expect(c.renderForBatch([block(1, 'Ha! That ship has sailed.')])).toBe(
      'Idioms - render by meaning, never word for word:\n'
      + "- that ship has sailed => تعبير مجازي",
    );
  });

  it('injects only the idioms the batch actually uses', () => {
    const rendered = c.renderForBatch([block(1, 'When the ferry turns at the gate.')]);
    expect(rendered).toContain('turns at the gate => يتجاوز الحدود');
    expect(rendered).not.toContain("that ship has sailed");
  });

  it('matches an idiom the cue broke over two lines', () => {
    expect(c.renderForBatch([block(1, 'That ship\nhas sailed.')]))
      .toContain("that ship has sailed");
  });

  it('says nothing when the batch uses none of them', () => {
    expect(c.renderForBatch([block(1, 'A perfectly literal line.')])).toBe('');
  });

  it('renders after the terms it is not', () => {
    const both = ctx({
      terms: [{ source: 'headquarters', target: 'المقر' }],
      idioms: [{ source: 'turns at the gate', target: 'يتجاوز الحدود' }],
    });
    const rendered = both.renderForBatch([block(1, 'headquarters turns at the gate')]);
    expect(rendered.indexOf('Terms:')).toBeLessThan(rendered.indexOf('Idioms -'));
  });

  it('counts towards a non-empty context', () => {
    expect(c.isEmpty()).toBe(false);
  });
});

describe('parseAttributionResponse', () => {
  const characters = [char('Alice', 'Alice', 'female'), char('Bob', 'Bob', 'male')];
  const target = scene(10, 12, 'x', ['Alice', 'Bob']);

  it('keeps in-range lines naming a known character', () => {
    const raw = '10=Alice\n11 = Bob\n12="Alice"';
    expect(parseAttributionResponse(raw, target, characters)).toEqual({
      10: 'Alice',
      11: 'Bob',
      12: 'Alice',
    });
  });

  it('accepts the literal "unknown" and drops everything else', () => {
    const raw = '10=unknown\n11=Carol\n99=Alice\nchatter\n';
    expect(parseAttributionResponse(raw, target, characters)).toEqual({ 10: 'unknown' });
  });
});

describe('serializeForScan', () => {
  it('returns every line when under budget', () => {
    const blocks = Array.from({ length: 5 }, (_, i) => block(i + 1, `Line ${i + 1}.`));
    const out = serializeForScan(blocks, TEST_BUDGET);
    for (let i = 1; i <= 5; i++) {
      expect(out).toContain(`[${i}] Line ${i}.`);
    }
  });

  it('stride-samples a file over budget across its whole length', () => {
    const long = 'x'.repeat(500);
    const blocks = Array.from({ length: 499 }, (_, i) => block(i + 1, `${long}-${i + 1}`));
    const out = serializeForScan(blocks, TEST_BUDGET);
    expect(out.length).toBeLessThanOrEqual(TEST_BUDGET * 1.1);
    const has = (from: number, to: number) => {
      for (let i = from; i <= to; i++) if (out.includes(`-${i}\n`) || out.endsWith(`-${i}`)) return true;
      return false;
    };
    expect(has(1, 20)).toBe(true);
    expect(has(450, 499)).toBe(true);
  });

  it('joins multi-line block text onto the [N] line', () => {
    const out = serializeForScan([block(1, 'First line\nSecond line')], TEST_BUDGET);
    expect(out).toBe('[1] First line Second line');
  });
});

describe('multi-word glossary terms', () => {
  const c = ctx({ terms: [{ source: 'safety briefing', target: 'جلسة السلامة' }] });

  it('injects a phrase the batch contains', () => {
    expect(c.renderForBatch([block(94, 'This is a safety briefing session.')]))
      .toContain('safety briefing => جلسة السلامة');
  });

  it('finds a phrase the cue broke over two lines', () => {
    // Whole-word search on the raw source string used to miss every phrase
    // whose words landed on different subtitle lines.
    expect(c.renderForBatch([block(94, 'This is a safety\nbriefing session.')]))
      .toContain('safety briefing');
  });

  it('finds a phrase split across two cues in the batch', () => {
    expect(c.renderForBatch([block(94, 'a safety'), block(95, 'briefing session')]))
      .toContain('safety briefing');
  });

  it('still refuses a phrase buried inside a longer Latin word', () => {
    expect(c.renderForBatch([block(94, 'presafety briefingish')])).toBe('');
  });

  it('does not inject a phrase the batch never uses', () => {
    expect(c.renderForBatch([block(94, 'This is a session.')])).toBe('');
  });

  it('treats a phrase term as something the reviewer can correct', () => {
    expect(c.hasCorrections([block(94, 'a safety briefing session')])).toBe(true);
  });
});

describe('FileContext.driftWarnings', () => {
  const c = ctx({ terms: [{ source: 'safety briefing', target: 'جلسة السلامة' }] });
  const batch = [block(94, 'A safety briefing session.')];

  it('names a term the output rendered some other way', () => {
    expect(c.driftWarnings(batch, [block(94, 'ندوة عن المضايقة الجنسية.')])).toEqual([
      "Block 94: glossary term 'safety briefing' was not rendered as 'جلسة السلامة'",
    ]);
  });

  it('stays quiet when the pinned rendering is there', () => {
    expect(c.driftWarnings(batch, [block(94, 'ندوة جلسة السلامة.')])).toEqual([]);
  });

  it("ignores terms the batch's own source never used", () => {
    expect(c.driftWarnings([block(94, 'Nothing to see.')], [block(94, 'لا شيء.')]))
      .toEqual([]);
  });

  it('reports nothing for an empty batch or an empty glossary', () => {
    expect(c.driftWarnings([], [])).toEqual([]);
    expect(new FileContext().driftWarnings(batch, [block(94, 'x')])).toEqual([]);
  });

  describe('over character names', () => {
    // The graded run spelled one name two ways across three cues and nothing
    // noticed: the check covered terms only.
    const cast = ctx({
      characters: [char('Phyllis', 'فيليس', 'female'), char('Jim', 'جيم', 'male')],
      scenes: [scene(94, 96, 'Phyllis and Jim talk', ['Phyllis', 'Jim'])],
    });
    const named = [block(94, 'Phyllis, hold on.')];

    it('names a character the output spelled another way', () => {
      expect(cast.driftWarnings(named, [block(94, 'فيلس، انتظري.')])).toEqual([
        "Block 94: character name 'Phyllis' was not rendered as 'فيليس'",
      ]);
    });

    it('stays quiet when the pinned spelling is there', () => {
      expect(cast.driftWarnings(named, [block(94, 'فيليس، انتظري.')])).toEqual([]);
    });

    it('asks nothing of a character the batch never names', () => {
      // Jim is a participant of the overlapping scene, so his gender still
      // reaches the prompt — but a name nobody said owes no rendering, and
      // only Phyllis is reported here.
      expect(cast.driftWarnings(named, [block(94, 'فيلس، انتظري.')]).length).toBe(1);
    });

    it('carries a cause naming what drifted, never where', () => {
      const [drift] = cast.driftEntries(named, [block(94, 'فيلس، انتظري.')]);
      expect(driftCause(drift!)).toBe('name:Phyllis');
      expect(driftCause({ ...drift!, kind: 'term' })).toBe('term:Phyllis');
    });
  });
});

describe('recurringPhrases', () => {
  /** The same phrase in a different sentence each time — the shape a real file
   * has, and what stops a longer n-gram from swallowing it. */
  function scattered(phrase: string, times: number, start = 1): SubtitleBlock[] {
    return Array.from({ length: times }, (_, i) =>
      block(start + i, `pad${start + i} ${phrase} tail${start + i}`));
  }

  it('pins a phrase the file repeats three times', () => {
    expect(recurringPhrases(scattered('safety briefing', 3)))
      .toEqual(['safety briefing']);
  });

  it('ignores a phrase seen only twice', () => {
    expect(recurringPhrases(scattered('safety briefing', 2))).toEqual([]);
  });

  it('ignores a phrase shorter than nine characters', () => {
    // "the desk" repeats plenty but is too short to be a rendering decision.
    expect(recurringPhrases(scattered('the desk', 5))).toEqual([]);
  });

  it('ignores a run made only of function words', () => {
    expect(recurringPhrases(scattered('that you are', 4))).toEqual([]);
  });

  it('drops a phrase only ever seen inside a longer one', () => {
    expect(recurringPhrases(scattered('deliver it like it is', 3)))
      .toEqual(['deliver it like it is']);
  });

  it('keeps a shorter phrase that also stands on its own', () => {
    const phrases = recurringPhrases([
      ...scattered('deliver it like it is', 3),
      ...scattered('deliver it', 2, 90),
    ]);
    expect(phrases).toContain('deliver it like it is');
    expect(phrases).toContain('deliver it');
  });

  it('reads through formatting tags', () => {
    expect(recurringPhrases(scattered('{\\i1}safety briefing{\\i0}', 3)))
      .toEqual(['safety briefing']);
  });

  it('ranks by how much the file spends on the phrase', () => {
    const phrases = recurringPhrases([
      ...scattered('safety briefing', 3),
      ...scattered('all right', 12, 100),
    ]);
    // 12 x "all right" (9 chars) outweighs 3 x "safety briefing" (17).
    expect(phrases[0]).toBe('all right');
    expect(phrases).toContain('safety briefing');
  });

  it('caps the list so the scan is seeded, not flooded', () => {
    const blocks = Array.from({ length: 120 }, (_, i) =>
      block(i + 1, `distinctive phrase number ${i % 40}`));
    expect(recurringPhrases(blocks).length).toBe(PHRASE_LIMIT);
  });

  it('returns nothing for an empty file', () => {
    expect(recurringPhrases([])).toEqual([]);
  });

  it('seeds exactly the phrases the whole file repeats, most-spent-on first', () => {
    const phrases = recurringPhrases(cuesFor('Arabic').map((c) => block(c.n, c.en)));
    // 4 x "night shift" outweighs 3 x the longer "ferry terminal".
    expect(phrases).toEqual(REPEATED_PHRASES);
    expect(phrases.length).toBeLessThanOrEqual(PHRASE_LIMIT);
  });

  it('mines a shorter phrase only when asked for one', () => {
    // The scan's floor keeps fragments out of a 25-term budget; the
    // consistency check has no budget and would miss an 8-character motif.
    expect(recurringPhrases(scattered('the line', 5))).toEqual([]);
    expect(recurringPhrases(scattered('the line', 5), CONSISTENCY_MIN_CHARS))
      .toEqual(['the line']);
  });
});

describe('findInconsistentPhrases', () => {
  /** `renderings` answers cue i; the source repeats `phrase` in every cue. */
  function run(phrase: string, renderings: string[]): PhraseSplit[] {
    const source = renderings.map((_, i) =>
      block(i + 1, `pad${i + 1} ${phrase} tail${i + 1}`));
    const output = renderings.map((text, i) => block(i + 1, text));
    return findInconsistentPhrases(source, output);
  }

  const FOUR_WAYS = ['نوبة الليل', 'النوبة الليلية', 'مناوبة المساء', 'دوام ليلي'];

  it('reports a phrase whose cues share no wording at all', () => {
    const [split] = run('night shift', FOUR_WAYS);
    expect(split).toEqual({
      phrase: 'night shift',
      occurrences: 4,
      distinctRenderings: 4,
      blocks: [1, 2, 3, 4],
    });
  });

  it('says how many ways it was rendered and across how many cues', () => {
    expect(phraseSplitMessage(run('night shift', FOUR_WAYS)[0]!)).toBe(
      "'night shift' is rendered 4 different ways across 4 cues; "
      + 'no wording is shared by all of them',
    );
  });

  it('stays quiet while one word survives every rendering', () => {
    expect(run('night shift', [
      'نوبة الليل', 'نوبة المساء', 'نوبة طويلة', 'نوبة أخرى',
    ])).toEqual([]);
  });

  it('counts two cues that agree word for word as one rendering', () => {
    const [split] = run('night shift', [...FOUR_WAYS, FOUR_WAYS[0]!]);
    expect(split!.occurrences).toBe(5);
    expect(split!.distinctRenderings).toBe(4);
  });

  it('leaves a phrase below the occurrence floor alone', () => {
    const short = FOUR_WAYS.slice(0, CONSISTENCY_MIN_OCCURRENCES - 1);
    expect(run('night shift', short)).toEqual([]);
  });

  it('sees a phrase the glossary never pinned', () => {
    // The drift check can only ask whether a pinned target was used; this is
    // the complement — nothing in the glossary mentions "the line".
    expect(run('the line', ['الخط', 'الحد', 'الحدود', 'الخط الفاصل'])
      .map((s) => s.phrase)).toEqual(['the line']);
  });

  it('pairs a cue with its output by number, so a hole costs one cue', () => {
    const source = [1, 2, 3, 4, 5].map((n) => block(n, `a night shift b${n}`));
    // Block 1 never came back; pairing by position would shift every rendering
    // up one and report cues that say nothing of the kind.
    const output = [block(2, 'باء'), block(3, 'ألف'), block(4, 'ألف'), block(5, 'ألف')];
    const [split] = findInconsistentPhrases(source, output);
    expect(split!.blocks).toEqual([2, 3, 4, 5]);
    expect(split!.occurrences).toBe(4);
  });

  it('returns nothing for an empty file', () => {
    expect(findInconsistentPhrases([], [])).toEqual([]);
    expect(findInconsistentPhrases([block(1, 'a night shift b')], [])).toEqual([]);
  });

  it('flags the fixture phrase the file splits and nothing else', () => {
    const splits = findInconsistentPhrases(
      cuesFor('Arabic').map((c) => block(c.n, c.en)),
      cuesFor('Arabic').map((c) => block(c.n, c.target)),
    );
    expect(splits.map((s) => s.phrase)).toEqual([SPLIT_PHRASE]);
    expect(splits[0]!.blocks.length).toBe(4);
    expect(recurringPhrases(cuesFor('Arabic').map((c) => block(c.n, c.en)),
      CONSISTENCY_MIN_CHARS)).toContain(CONSISTENT_PHRASE);
  });
});
