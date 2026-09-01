// A synthetic run of one invented English episode, cue by cue, beside an
// invented "model output" for each — five target files, one per script whose
// repairs behave differently. Every defect the deterministic repairs exist for
// is planted here on purpose, so a whole-file pass over the fixture measures
// what unit tests cannot: how often a detector fires on the cues it should
// leave alone.
//
// Every file carries the same eleven defect shapes, in the same roles, because
// the repairs branch on script and a defect planted only in Arabic only ever
// proves Arabic works. Reflow breaks between characters for Japanese and
// Chinese against a 16-column budget and between words for the rest against
// 42; a dropped dash, a dropped tag and a collapsed line are restored by the
// same rules everywhere, and the point of planting them five times is that the
// rules are measured five times.
//
// Arabic is the longest file because three passes are its alone — the RTL
// punctuation map, the diacritic strip and the variant check — and each is
// file-level, so it needs a file to be level against.
//
// Written by hand for this repository. The shapes, not the words, are the
// contract; the landmark records below name the cue each shape lives in, so a
// test never has to hard-code a number it cannot explain.

/** The target languages the fixture renders into — one per script whose
 * repairs differ, so a test can select the rows it needs by writing system. */
export type TargetLanguage =
  'Arabic' | 'Chinese' | 'Japanese' | 'Russian' | 'Spanish';

export const TARGET_LANGUAGES: TargetLanguage[] =
  ['Arabic', 'Japanese', 'Chinese', 'Russian', 'Spanish'];

export interface AlignedCue {
  n: number;
  en: string;
  target: string;
  lang: TargetLanguage;
}

/** A row before the file it belongs to stamps `lang` on it. */
type Rendering = Omit<AlignedCue, 'lang'>;

/** The eleven shapes every target file plants, named by role. A test loops
 * over `TARGET_LANGUAGES` and reads the same role out of each, so a rule is
 * measured against every script rather than against Arabic five times. */
export interface Landmarks {
  /** One line past that script's own budget, so reflow has work to do. */
  overBudget: number;
  /** Two source lines, two output lines: reflow must leave it alone. */
  twoLines: number;
  /** Two speaker turns, dashes intact. */
  dashes: number;
  /** A wrapping italic pair the model kept. */
  tagged: number;
  /** Adjacent cues where the first swallowed a clause of the second, which
   * kept only the fragment. */
  bled: [number, number];
  /** Both dashes dropped, but the two lines survived: restorable. */
  dashDropped: number;
  /** Both dashes dropped AND the turns merged onto one line: not restorable. */
  dashMerged: number;
  /** A wrapping italic pair the model dropped entirely. */
  droppedWrap: number;
  /** A two-line source cue the model returned as one. */
  collapsed: number;
  /** A terminal "!" that came back as a full stop. */
  flattenedMark: number;
}

export const LANDMARKS: Record<TargetLanguage, Landmarks> = {
  Arabic: {
    overBudget: 1, twoLines: 2, dashes: 3, tagged: 4, bled: [5, 6],
    dashDropped: 7, dashMerged: 8, droppedWrap: 9, collapsed: 10,
    flattenedMark: 11,
  },
  Japanese: {
    overBudget: 27, twoLines: 28, dashes: 29, tagged: 30, bled: [31, 32],
    dashDropped: 33, dashMerged: 34, droppedWrap: 35, collapsed: 36,
    flattenedMark: 37,
  },
  Chinese: {
    overBudget: 39, twoLines: 40, dashes: 41, tagged: 42, bled: [43, 44],
    dashDropped: 45, dashMerged: 46, droppedWrap: 47, collapsed: 48,
    flattenedMark: 49,
  },
  Russian: {
    overBudget: 51, twoLines: 52, dashes: 53, tagged: 54, bled: [55, 56],
    dashDropped: 57, dashMerged: 58, droppedWrap: 59, collapsed: 60,
    flattenedMark: 61,
  },
  Spanish: {
    overBudget: 63, twoLines: 64, dashes: 65, tagged: 66, bled: [67, 68],
    dashDropped: 69, dashMerged: 70, droppedWrap: 71, collapsed: 72,
    flattenedMark: 73,
  },
};

// Two passes cannot see a defect this fixture plants in all five files. Both
// are pinned by a test rather than left as a silent gap, and the bled and
// flattened-mark cues are planted in the blind files precisely so the day
// someone teaches a pass to see them, a test says so.

/** The languages whose bleeding is detectable. `contentWords` splits on
 * spaces, so a script that does not use them yields one token per cue and the
 * shared-run test can never fire. */
export const BLEED_VISIBLE_LANGUAGES: TargetLanguage[] =
  ['Arabic', 'Russian', 'Spanish'];
export const BLEED_BLIND_LANGUAGES: TargetLanguage[] = ['Japanese', 'Chinese'];

/** The languages whose flattened terminal mark is restored. `TERMINAL_MARKS`
 * holds the ASCII three and `targetMark` re-points them for Arabic only, so a
 * cue ending on the ideographic 。 reads as ending on no mark at all and is
 * left exactly as the model wrote it. */
export const MARK_RESTORED_LANGUAGES: TargetLanguage[] =
  ['Arabic', 'Russian', 'Spanish'];
export const MARK_BLIND_LANGUAGES: TargetLanguage[] = ['Japanese', 'Chinese'];

// --- Shapes only one file can carry -----------------------------------------

/** Adjacent cues that really do share a name in both languages: repetition,
 * not a clause that slid across the boundary. */
export const SHARED_WORD_PAIR: [number, number] = [13, 14];

/** A wrap the model duplicated, leaving renderless empty pairs either side. */
export const EMPTY_PAIR_CUE = 12;

/** Cues the model vocalized against a file whose median cue carries no marks. */
export const VOCALIZED_CUES = [22, 23, 24, 25, 26];

/** Source phrases the file repeats often enough to seed the glossary scan,
 * most-spent-on first. */
export const REPEATED_PHRASES = ['night shift', 'ferry terminal'];

/** The repeated phrase whose four cues come back with no wording in common —
 * cues 3 and 15 say نوبة الليل, 17 and 18 inflect it away. Every cue reads
 * correctly on its own, so only a whole-file pass can see the split. */
export const SPLIT_PHRASE = 'night shift';
/** The repeated phrase every one of its cues renders around المحطة. */
export const CONSISTENT_PHRASE = 'ferry terminal';

/** A Japanese cue with a Latin word welded to the kana either side of it. */
export const WELDED_LATIN_CUE = 38;
/** A Chinese cue at exactly 40 characters: inside the Latin budget and well
 * past the Han one, so one input answers the question twice. */
export const PER_SCRIPT_BUDGET_CUE = 50;
/** A Russian cue the model left Han characters in. */
export const LEAKED_HAN_CUE = 62;

// --- The files ---------------------------------------------------------------

const ARABIC_ROWS: Rendering[] = [
  { n: 1, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'من الصعب أحيانا أن تعرف متى يستحق العبور ثمن الوقود المحروق فيه.' },
  { n: 2, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'نادية تعد التذاكر\nوتضعها في العلبة.' },
  { n: 3, en: '- Are you on the night shift again?\n- Every night this week.', target: '- هل أنت في نوبة الليل مجددا؟\n- كل ليلة هذا الأسبوع.' },
  { n: 4, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}في مكان ما نورس يضحك.{\\i0}' },
  { n: 5, en: 'The last boat is already tied up,', target: 'المركب الأخير مربوط بالفعل فيمكن إنزال المنحدر' },
  { n: 6, en: 'so that ramp can come down now.', target: 'المنحدر.' },
  { n: 7, en: '- Where is the manifest?\n- Under the radio.', target: 'أين البيان؟\nتحت الراديو.' },
  { n: 8, en: '- Did you sign the log?\n- Nadia signed it.', target: 'هل وقعت السجل؟ نادية وقعته.' },
  { n: 9, en: '<i>The lamp room is cold tonight.</i>', target: 'غرفة المصباح باردة الليلة.' },
  { n: 10, en: 'The inspector wants the fuel log\nand the insurance papers.', target: 'المفتش يريد سجل الوقود وأوراق التأمين.' },
  { n: 11, en: 'Get the ropes off the bollard!', target: 'أنزل الحبال عن العمود.' },
  { n: 12, en: '{\\i1}The crossing is over.{\\i0}', target: '{\\i1}{\\i0}{\\i1}انتهى العبور.{\\i0}{\\i1}{\\i0}' },
  { n: 13, en: 'Rafiq checks the mooring lines twice.', target: 'رفيق يفحص حبال الرسو مرتين.' },
  { n: 14, en: 'Rafiq will be here in ten minutes.', target: 'رفيق سيصل خلال عشر دقائق.' },
  { n: 15, en: 'Night shift never gets the good coffee.', target: 'نوبة الليل لا تحصل على القهوة الجيدة أبدا.' },
  { n: 16, en: 'The crew log is in the drawer.', target: 'سجل الطاقم في الدرج.' },
  { n: 17, en: 'Night shift crews sign the sheet here.', target: 'أطقم المناوبة الليلية توقع الورقة هنا.' },
  { n: 18, en: 'and your night shift finally begins.', target: 'ويبدأ عملك الليلي أخيرا.' },
  { n: 19, en: 'This ferry terminal closes at two.', target: 'هذه المحطة تغلق في الثانية.' },
  { n: 20, en: 'The ferry terminal has one working light.', target: 'المحطة فيها مصباح واحد يعمل.' },
  { n: 21, en: 'Ferry terminal rules are on the wall.', target: 'قواعد المحطة على الحائط.' },
  { n: 22, en: 'Take the lantern and go down.', target: 'خُذِ الفَانُوسَ وَانْزِلْ.' },
  { n: 23, en: 'Omar hums something old.', target: 'يُدَنْدِنُ عُمَرُ بِشَيْءٍ قَدِيمٍ.' },
  { n: 24, en: 'The water is very black tonight.', target: 'المَاءُ شَدِيدُ السَّوَادِ اللَّيْلَةَ.' },
  { n: 25, en: 'Nobody says anything for a while.', target: 'لَا يَقُولُ أَحَدٌ شَيْئًا لِفَتْرَةٍ.' },
  { n: 26, en: 'Then they do it all again.', target: 'ثُمَّ يُعِيدُونَ كُلَّ شَيْءٍ.' },
];

// A no-space script: a line break lands between characters and the budget is
// 16 columns rather than 42. The bled pair here is planted to be invisible —
// see BLEED_BLIND_LANGUAGES.
const JAPANESE_ROWS: Rendering[] = [
  { n: 27, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'その渡りが燃料に見合うかは、いつも分かるわけではない。' },
  { n: 28, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'ナディアが切符を数え\n缶にしまう。' },
  { n: 29, en: '- Are you on the night shift again?\n- Every night this week.', target: '- また夜勤なのか。\n- 今週は毎晩だ。' },
  { n: 30, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}どこかでカモメが笑っている。{\\i0}' },
  { n: 31, en: 'The last boat is already tied up,', target: '最後の船はもう繋がれているのでその斜路を下ろせる' },
  { n: 32, en: 'so that ramp can come down now.', target: 'その斜路。' },
  { n: 33, en: '- Where is the manifest?\n- Under the radio.', target: '積荷目録はどこだ。\n無線機の下だ。' },
  { n: 34, en: '- Did you sign the log?\n- Nadia signed it.', target: '日誌に署名したか。ナディアが署名した。' },
  { n: 35, en: '<i>The lamp room is cold tonight.</i>', target: '灯室は今夜冷える。' },
  { n: 36, en: 'The inspector wants the fuel log\nand the insurance papers.', target: '検査官は燃料記録と保険書類を求めている。' },
  { n: 37, en: 'Get the ropes off the bollard!', target: '係柱からロープを外せ。' },
  { n: 38, en: 'Rafiq called from the pier.', target: 'ラフィクがpierから電話した。' },
];

// Han only, and the one file carrying a cue whose length answers differently
// depending on which script's budget is asked.
const CHINESE_ROWS: Rendering[] = [
  { n: 39, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: '有时很难说清一趟渡船是否值回烧掉的燃料。' },
  { n: 40, en: 'Nadia counts the tickets\nand puts them in the tin.', target: '娜迪亚数完船票\n把它们放进铁盒。' },
  { n: 41, en: '- Are you on the night shift again?\n- Every night this week.', target: '- 又是夜班吗？\n- 这周每晚都是。' },
  { n: 42, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}某处有海鸥在笑。{\\i0}' },
  { n: 43, en: 'The last boat is already tied up,', target: '最后一条船已经系好了所以可以放下坡道' },
  { n: 44, en: 'so that ramp can come down now.', target: '坡道。' },
  { n: 45, en: '- Where is the manifest?\n- Under the radio.', target: '舱单在哪里？\n在无线电下面。' },
  { n: 46, en: '- Did you sign the log?\n- Nadia signed it.', target: '你在日志上签字了吗？娜迪亚签了。' },
  { n: 47, en: '<i>The lamp room is cold tonight.</i>', target: '灯室今夜很冷。' },
  { n: 48, en: 'The inspector wants the fuel log\nand the insurance papers.', target: '检查员要燃料记录和保险单。' },
  { n: 49, en: 'Get the ropes off the bollard!', target: '把缆绳从系缆桩上解开。' },
  { n: 50, en: 'The inspector wants the fuel log and the insurance papers, and the blue folder is back under the desk.', target: '检查员今晚要燃料记录和保险单，所以娜迪亚在开船之前把蓝色文件夹放在了办公桌下面。' },
];

// Word breaks at the same 42 columns Arabic uses, with none of the Arabic
// punctuation, diacritic or variant machinery: the control for "leaves the
// other scripts alone".
const RUSSIAN_ROWS: Rendering[] = [
  { n: 51, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'Иногда трудно понять, когда переправа стоит потраченного топлива.' },
  { n: 52, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'Надя считает билеты\nи убирает их в жестянку.' },
  { n: 53, en: '- Are you on the night shift again?\n- Every night this week.', target: '- Ты снова в ночной смене?\n- Каждую ночь на этой неделе.' },
  { n: 54, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}Где-то смеётся чайка.{\\i0}' },
  { n: 55, en: 'The last boat is already tied up,', target: 'Последняя лодка уже пришвартована поэтому можно опустить сходни' },
  { n: 56, en: 'so that ramp can come down now.', target: 'сходни.' },
  { n: 57, en: '- Where is the manifest?\n- Under the radio.', target: 'Где судовая роль?\nПод радиостанцией.' },
  { n: 58, en: '- Did you sign the log?\n- Nadia signed it.', target: 'Ты расписался в журнале? Надя расписалась.' },
  { n: 59, en: '<i>The lamp room is cold tonight.</i>', target: 'В ламповой сегодня холодно.' },
  { n: 60, en: 'The inspector wants the fuel log\nand the insurance papers.', target: 'Инспектор требует журнал топлива и страховые бумаги.' },
  { n: 61, en: 'Get the ropes off the bollard!', target: 'Снимите канаты с кнехта.' },
  { n: 62, en: 'The label on the crate is unreadable.', target: 'Надпись на ящике 你好 неразборчива.' },
];

const SPANISH_ROWS: Rendering[] = [
  { n: 63, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'No siempre es fácil saber cuándo una travesía compensa el combustible.' },
  { n: 64, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'Nadia cuenta los billetes\ny los guarda en la lata.' },
  { n: 65, en: '- Are you on the night shift again?\n- Every night this week.', target: '- ¿Otra vez en el turno de noche?\n- Todas las noches esta semana.' },
  { n: 66, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}En algún lugar ríe una gaviota.{\\i0}' },
  { n: 67, en: 'The last boat is already tied up,', target: 'El último barco ya está amarrado así que se puede bajar la rampa' },
  { n: 68, en: 'so that ramp can come down now.', target: 'la rampa.' },
  { n: 69, en: '- Where is the manifest?\n- Under the radio.', target: '¿Dónde está el manifiesto?\nDebajo de la radio.' },
  { n: 70, en: '- Did you sign the log?\n- Nadia signed it.', target: '¿Firmaste el registro? Nadia lo firmó.' },
  { n: 71, en: '<i>The lamp room is cold tonight.</i>', target: 'La sala de lámparas está fría esta noche.' },
  { n: 72, en: 'The inspector wants the fuel log\nand the insurance papers.', target: 'El inspector quiere el registro de combustible y los seguros.' },
  { n: 73, en: 'Get the ropes off the bollard!', target: 'Quita los cabos del noray.' },
];

/** Every row of every file. `n` is unique across the whole table, so a cue can
 * be found without being told which file it came from. */
export const ALIGNED_CUES: AlignedCue[] = [
  ...file('Arabic', ARABIC_ROWS),
  ...file('Japanese', JAPANESE_ROWS),
  ...file('Chinese', CHINESE_ROWS),
  ...file('Russian', RUSSIAN_ROWS),
  ...file('Spanish', SPANISH_ROWS),
];

function file(lang: TargetLanguage, rows: Rendering[]): AlignedCue[] {
  return rows.map((row) => ({ ...row, lang }));
}

/** Cues 4 and 2 as a Windows-newline SRT file, the shape a real subtitle
 * download arrives in. Parsing renumbers to 1..n, so the wrapped cue is block
 * 1 and the two-line cue is block 2. */
export const CRLF_SAMPLE_SRT =
  '4\r\n00:00:18,120 --> 00:00:20,400\r\n{\\i1}Somewhere a gull is laughing.{\\i0}\r\n'
  + '\r\n'
  + '2\r\n00:01:02,000 --> 00:01:05,100\r\nNadia counts the tickets\r\n'
  + 'and puts them in the tin.\r\n';

/** One target file: one language, one script. The file-level passes — the
 * diacritic strip, variant drift, phrase consistency — only mean anything
 * inside one of these, never across the table. */
export function cuesFor(lang: TargetLanguage): AlignedCue[] {
  return ALIGNED_CUES.filter((c) => c.lang === lang);
}

/** The cue with the given fixture number. */
export function cue(n: number): AlignedCue {
  const found = ALIGNED_CUES.find((c) => c.n === n);
  if (!found) throw new Error(`no cue ${n} in the fixture`);
  return found;
}
