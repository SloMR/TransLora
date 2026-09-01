// Ported from cli/tests/test_prompt.py — the wiring that puts the glossary, the
// read-only previous context and the per-target directives in front of the
// model, and the rules the static system prompt must keep carrying.

import { FileContext } from './context-pass';
import { SubtitleBlock, serializeLite } from './srt-parser';
import {
  BACK_TRANSLATION_SYSTEM_PROMPT,
  CONTEXT_SYSTEM_PROMPT,
  REVIEW_SYSTEM_PROMPT,
  SYSTEM_PROMPT,
  buildAttributionUserMessage,
  buildBackTranslationUserMessage,
  buildFixFlaggedUserMessage,
  buildReviewUserMessage,
  buildScanUserMessage,
  buildUserMessage,
} from './translation-prompt';

const WIRE = '1\nHello\n\n2\nWorld\n';

function makeBlocks(count: number, start = 1): SubtitleBlock[] {
  return Array.from({ length: count }, (_, i) => {
    const n = start + i;
    return {
      number: n,
      timestamp: `00:00:${String(n).padStart(2, '0')},000 --> `
        + `00:00:${String(n + 1).padStart(2, '0')},000`,
      text: `line ${n}`,
    };
  });
}

describe('buildUserMessage', () => {
  it('names both languages', () => {
    const msg = buildUserMessage('English', 'Arabic', WIRE, '', []);
    expect(msg).toContain('Translate from English to Arabic:');
    expect(msg.endsWith(WIRE)).toBeTrue();
  });

  it('omits the source when auto-detecting', () => {
    const msg = buildUserMessage('', 'Arabic', WIRE, '', []);
    expect(msg).toContain('Translate to Arabic:');
    expect(msg).not.toContain('Translate from');
  });

  it('orders glossary then context then blocks', () => {
    const msg = buildUserMessage(
      'English', 'Arabic', WIRE, 'Characters:\n- Alice => أليس (female)', makeBlocks(2, 8),
    );
    expect(msg.indexOf('Glossary for this scene:'))
      .toBeLessThan(msg.indexOf('Previous context'));
    expect(msg.indexOf('Previous context'))
      .toBeLessThan(msg.indexOf('Translate from English to Arabic:'));
  });

  it('omits empty sections', () => {
    const msg = buildUserMessage('English', 'Arabic', WIRE, '', []);
    expect(msg).not.toContain('Glossary for this scene:');
    expect(msg).not.toContain('Previous context');
  });

  it('flattens and marks previous context blocks', () => {
    const prev = makeBlocks(1, 9);
    prev[0]!.text = 'two\nlines';
    const msg = buildUserMessage('English', 'Arabic', WIRE, '', prev);
    // Newlines inside a previous block would forge a wire block boundary.
    expect(msg).toContain('  [prev #9] two lines');
    expect(msg).not.toContain('9\ntwo');
  });
});

describe('buildReviewUserMessage', () => {
  it('carries the source and the first pass', () => {
    const batch = makeBlocks(2);
    const firstPass = makeBlocks(2).map((b) => ({ ...b, text: `T${b.number}` }));
    const msg = buildReviewUserMessage(batch, firstPass, 'Characters:\n- Alice');

    expect(msg.indexOf('Glossary:')).toBeLessThan(msg.indexOf('Source blocks:'));
    expect(msg.indexOf('Source blocks:')).toBeLessThan(msg.indexOf('First-pass translation:'));
    expect(msg).toContain(serializeLite(batch));
    expect(msg).toContain(serializeLite(firstPass));
  });

  it('carries the target script\'s grammar checks between glossary and source', () => {
    const msg = buildReviewUserMessage(
      makeBlocks(1), makeBlocks(1), 'Characters:\n- Alice',
      'Match gender and number to the referent, including the dual.',
    );
    expect(msg).toContain(
      'Target-language checks: Match gender and number to the referent, '
      + 'including the dual.',
    );
    expect(msg.indexOf('Glossary:')).toBeLessThan(msg.indexOf('Target-language checks:'));
    expect(msg.indexOf('Target-language checks:')).toBeLessThan(msg.indexOf('Source blocks:'));
  });

  it('drops the line entirely for a script with no checks', () => {
    const bare = buildReviewUserMessage(makeBlocks(1), makeBlocks(1), 'g');
    expect(bare).not.toContain('Target-language checks');
    expect(buildReviewUserMessage(makeBlocks(1), makeBlocks(1), 'g', '   '))
      .not.toContain('Target-language checks');
  });

  it('keeps the checks out of the cacheable system prompt', () => {
    expect(REVIEW_SYSTEM_PROMPT).not.toContain('Target-language checks');
  });

  it("puts the batch's idioms in front of the rule that may fix them", () => {
    // The reviewer's fourth permitted correction is a word-for-word idiom, so
    // the glossary slice it is given has to carry the batch's idioms — the
    // same slice, under the same heading, that the translation pass saw.
    const batch = [
      { number: 1, timestamp: '00:00:01,000 --> 00:00:02,000', text: 'Alice, break a leg.' },
    ];
    const context = new FileContext(
      '', [{ source: 'Alice', target: 'أليس', gender: 'female' }], [],
      [{ source: 'break a leg', target: 'بالتوفيق' },
       { source: 'that ship has sailed', target: 'فات الأوان' }],
    );
    const msg = buildReviewUserMessage(batch, batch, context.renderForBatch(batch));

    expect(REVIEW_SYSTEM_PROMPT).toContain(
      'A literal word-for-word rendering of an idiom or set phrase');
    expect(msg).toContain(
      'Idioms - render by meaning, never word for word:\n- break a leg => بالتوفيق');
    // Scoped to the batch, exactly as the translation pass scopes it.
    expect(msg).not.toContain('that ship has sailed');
  });
});

describe('buildFixFlaggedUserMessage', () => {
  it('lists the problems ahead of the request the batch already had', () => {
    const original = buildUserMessage('English', 'Arabic', WIRE, '', []);
    const msg = buildFixFlaggedUserMessage(original, [
      "block 236: text from the next cue appears here ('الخط')",
      'block 149: the formatting tags {\\i1}...{\\i0} were dropped',
    ]);
    expect(msg).toBe(
      'The previous attempt had these problems - fix ONLY these, keep '
      + 'everything else identical:\n'
      + "- block 236: text from the next cue appears here ('الخط')\n"
      + '- block 149: the formatting tags {\\i1}...{\\i0} were dropped\n\n'
      + original,
    );
  });
});

describe('buildBackTranslationUserMessage', () => {
  it('names the language to come back to', () => {
    expect(buildBackTranslationUserMessage('English', WIRE))
      .toBe(`Translate back to English:\n\n${WIRE}`);
  });

  it('falls back to the original language when the source was auto-detected', () => {
    expect(buildBackTranslationUserMessage('', WIRE))
      .toBe(`Translate back to the original language:\n\n${WIRE}`);
  });

  it('asks for a literal rendering, in the wire format', () => {
    expect(BACK_TRANSLATION_SYSTEM_PROMPT).toContain('Translate literally');
    expect(BACK_TRANSLATION_SYSTEM_PROMPT).toContain('Same number of blocks');
  });
});

describe('buildScanUserMessage', () => {
  it('omits the source line when auto-detecting', () => {
    const withSource = buildScanUserMessage('English', 'Arabic', '[1] Hi');
    expect(withSource.startsWith('Source language: English\n')).toBeTrue();
    const without = buildScanUserMessage('', 'Arabic', '[1] Hi');
    expect(without.startsWith('Target language: Arabic')).toBeTrue();
    expect(without).not.toContain('Source language');
  });
});

describe('recurring phrases in the scan message', () => {
  it('lists the seeded phrases the scan must pin', () => {
    const msg = buildScanUserMessage(
      'English', 'Arabic', '[1] Hi', '', ['safety briefing', 'all right'],
    );
    expect(msg).toContain(
      'Recurring phrases - give each one ONE target rendering and use it '
      + 'everywhere:\n- safety briefing\n- all right',
    );
    // The blocks stay last so nothing can be mistaken for one.
    expect(msg.endsWith('[1] Hi')).toBeTrue();
    expect(msg.indexOf('Recurring phrases')).toBeLessThan(msg.indexOf('[1] Hi'));
  });

  it('says nothing when the file repeats nothing', () => {
    expect(buildScanUserMessage('English', 'Arabic', '[1] Hi', '', []))
      .toBe(buildScanUserMessage('English', 'Arabic', '[1] Hi'));
  });

  // The two trees assemble this whitespace differently — Python puts the blank
  // line before the phrase section, this tree after — so the whole rendered
  // message is pinned. The identical golden lives in cli/tests/test_parity.py
  // (SCAN_MESSAGE_GOLDEN); change one and change the other.
  it('renders the whole scan message exactly', () => {
    expect(buildScanUserMessage(
      'English', 'Arabic', '[1] Hi', 'Egyptian Arabic',
      ['safety briefing', "that ship has sailed"],
    )).toBe(
      'Source language: English\n'
      + 'Target language: Arabic\n'
      + 'Target variant: Egyptian Arabic. Use it as the <register> instead of '
      + 'inferring one.\n'
      + '\n'
      + 'Recurring phrases - give each one ONE target rendering and use it '
      + 'everywhere:\n'
      + '- safety briefing\n'
      + "- that ship has sailed\n"
      + '\n'
      + '[1] Hi',
    );
  });

  it('separates the phrase list from the blocks by a blank line', () => {
    for (const dialect of ['', 'Egyptian Arabic']) {
      expect(buildScanUserMessage(
        'English', 'Arabic', '[1] Hi', dialect, ['safety briefing'],
      ).endsWith('- safety briefing\n\n[1] Hi')).toBeTrue();
    }
  });

  it('sits after the target variant', () => {
    const msg = buildScanUserMessage(
      'English', 'Arabic', '[1] Hi', 'Egyptian Arabic', ['all right'],
    );
    expect(msg.indexOf('Target variant')).toBeLessThan(msg.indexOf('Recurring phrases'));
  });

  it('leaves room in the glossary for them', () => {
    // 10 terms could not hold the phrases a 372-cue episode repeats.
    expect(CONTEXT_SYSTEM_PROMPT)
      .toContain('Include up to 20 characters, 25 terms, 40 scenes, 4 notes.');
  });
});

describe('idioms', () => {
  it('asks the scan for an <idioms> section, right after <terms>', () => {
    expect(CONTEXT_SYSTEM_PROMPT).toContain(
      '<terms>\nSOURCE => TARGET\n</terms>\n'
      + '<idioms>\nSOURCE_IDIOM => TARGET_EQUIVALENT\n</idioms>\n'
      + '<scenes>',
    );
    expect(CONTEXT_SYSTEM_PROMPT).toContain('Reply with all six sections below');
  });

  it('asks for the words a subtitle would use, never a definition', () => {
    // A definition pasted into a cue is unreadable at speed: the graded run
    // shipped a 93-character gloss over a four-word punchline.
    expect(CONTEXT_SYSTEM_PROMPT).toContain(
      '- <idioms>: source idioms, set phrases and jokes that must NOT be '
      + 'translated word by word. Give the exact words a subtitle would use in '
      + 'the target language — never a definition or an explanation of the '
      + 'idiom. If no equivalent exists, give the shortest plain rendering of '
      + 'what the speaker means. Include up to 15.',
    );
    // Ordered with the sections: after the character rules, before <scenes>.
    expect(CONTEXT_SYSTEM_PROMPT.indexOf('- TARGET_NAME is'))
      .toBeLessThan(CONTEXT_SYSTEM_PROMPT.indexOf('- <idioms>:'));
    expect(CONTEXT_SYSTEM_PROMPT.indexOf('- <idioms>:'))
      .toBeLessThan(CONTEXT_SYSTEM_PROMPT.indexOf('- <scenes>:'));
  });

  it('tells the translator to render the meaning, not the words', () => {
    expect(SYSTEM_PROMPT).toContain(
      '- Idioms, jokes and set phrases: translate the MEANING, never word by '
      + 'word. If the target has an equivalent expression, use it; if not, say '
      + 'plainly what the speaker means.',
    );
    // Right after the faithfulness rule it qualifies.
    expect(SYSTEM_PROMPT.indexOf('- Translate faithfully'))
      .toBeLessThan(SYSTEM_PROMPT.indexOf('- Idioms, jokes and set phrases'));
  });

  it('lets the reviewer fix a calqued idiom', () => {
    expect(REVIEW_SYSTEM_PROMPT).toContain(
      '- A literal word-for-word rendering of an idiom or set phrase, where '
      + 'the meaning is lost.',
    );
  });
});

describe('register', () => {
  it('states the register rule in its original one-line form', () => {
    // A stronger "never soften" rule was measured on a graded 372-cue benchmark
    // and made things worse: softening rose 13 -> 18, fluency fell 6.4 -> 5.9,
    // and the ten crude cues it targeted produced zero restorations.
    expect(SYSTEM_PROMPT).toContain(
      '- Translate faithfully: profanity, slurs, slang — match the original register.',
    );
    expect(SYSTEM_PROMPT).not.toContain('full strength');
    expect(REVIEW_SYSTEM_PROMPT).not.toContain('Register that was softened');
    expect(REVIEW_SYSTEM_PROMPT).toContain('DEFAULT: output the first-pass UNCHANGED');
  });

  it('keeps the scan register line about the variant and nothing else', () => {
    // Asking this line for coarseness too cost a graded point of structure and
    // 0.8% source echo: the scan answered "informal conversational style"
    // instead of naming Modern Standard Arabic, and the whole file followed it
    // into colloquial. Coarseness is the two prompts above's job; this line
    // steers the variant, and only the variant.
    expect(CONTEXT_SYSTEM_PROMPT).toContain(
      '- <register>: name the exact target variant (e.g. "Modern Standard '
      + 'Arabic, neutral", "Brazilian Portuguese, casual", "Japanese, polite '
      + 'です/ます form"). Pick one for the whole file.',
    );
    expect(CONTEXT_SYSTEM_PROMPT).not.toContain('how coarse');
  });

  it('carries the register line into a batch that matches nothing else', () => {
    // The register line is file-wide in the slice, so one scan answer reaches
    // every batch at no extra call — which is exactly why naming the variant
    // there, and only there, is worth this much.
    const context = new FileContext('Modern Standard Arabic, neutral');
    const batch = makeBlocks(2);
    const msg = buildUserMessage(
      'English', 'Arabic', serializeLite(batch), context.renderForBatch(batch),
    );
    expect(msg).toContain(
      'Glossary for this scene:\nTarget register: Modern Standard Arabic, '
      + 'neutral (use consistently across every block)',
    );
  });
});

describe('buildAttributionUserMessage', () => {
  it('lists the roster then the scene', () => {
    const msg = buildAttributionUserMessage(
      '- Alice (F)\n- Bob (M)', ['[10] Hi', '[11] Bye'],
    );
    expect(msg.indexOf('Characters:')).toBeLessThan(msg.indexOf('Scene:'));
    expect(msg.endsWith('[10] Hi\n[11] Bye')).toBeTrue();
  });
});

describe('per-request directives', () => {
  it('carries the target\'s own norms in the line-limit rule', () => {
    const msg = buildUserMessage('English', 'Japanese', WIRE, '', [], 16, 2);
    expect(msg).toContain(
      '- Keep each line at or under 16 characters and never exceed 2 '
      + 'lines per block; prefer tighter phrasing over a longer line.',
    );
  });

  it('defaults the line-limit rule to the Latin norms', () => {
    const msg = buildUserMessage('English', 'Arabic', WIRE, '', []);
    expect(msg).toContain('at or under 42 characters and never exceed 2 lines');
  });

  it('no longer names a line length in the static system prompt', () => {
    // The limit is per-target, so it rides in the user message and the system
    // prompt stays byte-identical across requests (and cacheable).
    expect(SYSTEM_PROMPT).not.toContain('42');
    expect(SYSTEM_PROMPT).not.toContain('characters or fewer per line');
  });

  it('forbids moving words between blocks', () => {
    expect(SYSTEM_PROMPT).toContain(
      '- Never move words between blocks. If a sentence continues into '
      + 'the next block, translate only the part in THIS block, even if it '
      + 'reads incomplete.',
    );
    // Immediately after the independence rule it reinforces.
    expect(SYSTEM_PROMPT.indexOf('Translate each block independently'))
      .toBeLessThan(SYSTEM_PROMPT.indexOf('Never move words between blocks'));
  });

  it('names the tags that were dropped', () => {
    expect(SYSTEM_PROMPT).toContain(
      '- HTML tags, music symbols, formatting tags (\\N, {\\an8}, '
      + '{\\i1}, {\\i0}, <i>, </i>) - copy every tag through in the same '
      + 'position, opening and closing',
    );
  });

  for (const formality of ['formal', 'informal']) {
    it(`adds one register line for ${formality}`, () => {
      const msg = buildUserMessage('English', 'German', WIRE, '', [], 42, 2, formality);
      expect(msg).toContain(`Register: use ${formality} address throughout.`);
    });
  }

  it('says nothing for auto formality and leaves it to the source', () => {
    const msg = buildUserMessage('English', 'German', WIRE, '', [], 42, 2, 'auto');
    expect(msg).not.toContain('Register: use');
  });

  it('names the variant to use for a dialect', () => {
    const msg = buildUserMessage(
      'English', 'Arabic', WIRE, '', [], 42, 2, 'auto', 'Egyptian Arabic',
    );
    expect(msg).toContain('Target variant: Egyptian Arabic. Use it consistently.');
  });

  for (const dialect of ['', '   ']) {
    it(`adds nothing for a blank dialect ${JSON.stringify(dialect)}`, () => {
      const msg = buildUserMessage('English', 'Arabic', WIRE, '', [], 42, 2, 'auto', dialect);
      expect(msg).not.toContain('Target variant');
    });
  }

  it('strips a dialect before it reaches the model', () => {
    const msg = buildUserMessage(
      'English', 'Arabic', WIRE, '', [], 42, 2, 'auto', '  Brazilian Portuguese  ',
    );
    expect(msg).toContain('Target variant: Brazilian Portuguese. Use it consistently.');
  });

  it('sits the directives between the context and the blocks', () => {
    const msg = buildUserMessage(
      'English', 'Arabic', WIRE, 'Characters:\n- Alice', makeBlocks(1, 9),
      42, 2, 'formal', 'Egyptian Arabic',
    );
    const order = [
      'Previous context',
      '- Keep each line at or under',
      'Register: use formal',
      'Target variant: Egyptian Arabic',
      'Translate from English to Arabic:',
    ].map((needle) => msg.indexOf(needle));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(order.every((i) => i >= 0)).toBeTrue();
    // The wire stays last so nothing can be mistaken for an input block.
    expect(msg.endsWith(WIRE)).toBeTrue();
  });

  it('uses a given dialect in the scan instead of guessing the register', () => {
    const msg = buildScanUserMessage('English', 'Arabic', '[1] Hi', 'Egyptian Arabic');
    expect(msg).toContain(
      'Target variant: Egyptian Arabic. Use it as the <register> instead of inferring one.',
    );
    expect(msg.endsWith('[1] Hi')).toBeTrue();
  });

  it('infers the register when no dialect is given', () => {
    expect(buildScanUserMessage('English', 'Arabic', '[1] Hi')).not.toContain('Target variant');
  });
});
