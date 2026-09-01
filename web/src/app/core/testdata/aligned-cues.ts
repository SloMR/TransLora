// A synthetic run of one invented English episode, cue by cue, beside an
// invented "model output" for each — five target files, one per script whose
// repairs behave differently. Every defect the deterministic repairs exist for
// is planted here on purpose, so a whole-file pass over the fixture measures
// what unit tests cannot — how often a detector fires on the cues it should
// leave alone.
//
// Arabic is the long file and carries every planted defect; it is also the only
// script that takes the RTL punctuation map, the diacritic strip and the
// variant check at all. The four beside it exist because the rest of the
// repairs are script-dependent: Japanese and Chinese break a line between
// characters against a 16-column budget, Russian and Spanish between words
// against 42, and nothing else in the fixture could tell those two apart.
//
// Written by hand for this repository. The shapes, not the words, are the
// contract; the landmark exports below name the cue each shape lives in, so a
// test never has to hard-code a number it cannot explain.

/** The target languages the fixture renders into — one per script whose
 * repairs differ, so a test can select the rows it needs by writing system. */
export type TargetLanguage =
  'Arabic' | 'Chinese' | 'Japanese' | 'Russian' | 'Spanish';

export interface AlignedCue {
  n: number;
  en: string;
  target: string;
  lang: TargetLanguage;
}

/** A row before the file it belongs to stamps `lang` on it. */
type Rendering = Omit<AlignedCue, 'lang'>;

/** Adjacent pairs where the first cue's output swallowed a clause of the
 * second, which kept only the fragment. */
export const BLED_PAIRS: [number, number][] = [[21, 22], [25, 26]];

/** Adjacent cues that really do share a name in both languages: repetition,
 * not a clause that slid across the boundary. */
export const SHARED_WORD_PAIR: [number, number] = [9, 10];

/** Cues the model vocalized against a file whose median cue carries no marks. */
export const VOCALIZED_CUES = [17, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48];

/** Cues whose terminal "!" came back as a full stop. */
export const FLATTENED_MARK_CUES = [8, 24, 33];

/** Source phrases the file repeats often enough to seed the glossary scan,
 * most-spent-on first. */
export const REPEATED_PHRASES = ['night shift', 'ferry terminal', 'safety drill'];

/** The repeated phrase whose four cues come back with no wording in common —
 * two say نوبة الليل, the others inflect it away. Every cue reads correctly on
 * its own, so only a whole-file pass can see the split. */
export const SPLIT_PHRASE = 'night shift';
/** The repeated phrase every one of its cues renders around المحطة. */
export const CONSISTENT_PHRASE = 'ferry terminal';

/** A wrapping italic pair the model dropped entirely. */
export const DROPPED_WRAP_CUE = 18;
/** A wrap the model duplicated, leaving renderless empty pairs either side. */
export const EMPTY_PAIR_CUE = 19;

/** Two speaker turns, dashes intact. */
export const DASH_INTACT_CUE = 2;
/** Both dashes dropped, but the two lines survived: restorable. */
export const DASH_DROPPED_CUE = 29;
/** Both dashes dropped AND the turns merged onto one line: not restorable. */
export const DASH_MERGED_CUE = 30;

/** A two-line source cue the model returned as one. */
export const COLLAPSED_LINES_CUE = 20;
/** One line past any script's per-line limit. */
export const LONG_LINE_CUE = 34;
/** One line comfortably inside the Latin limit and past the CJK one. */
export const OVER_CJK_LIMIT_CUE = 36;

/** The four shapes every target file carries, so a script-dependent pass can
 * be measured against each script rather than against Arabic five times. */
export interface ScriptLandmarks {
  /** One line past that script's own budget, so reflow has work to do. */
  overBudget: number;
  twoLines: number;
  dashes: number;
  tagged: number;
}

export const SCRIPT_LANDMARKS: Record<TargetLanguage, ScriptLandmarks> = {
  Arabic: { overBudget: LONG_LINE_CUE, twoLines: 1, dashes: DASH_INTACT_CUE, tagged: 7 },
  Japanese: { overBudget: 52, twoLines: 50, dashes: 51, tagged: 49 },
  Chinese: { overBudget: 57, twoLines: 55, dashes: 56, tagged: 54 },
  Russian: { overBudget: 62, twoLines: 60, dashes: 61, tagged: 59 },
  Spanish: { overBudget: 67, twoLines: 65, dashes: 66, tagged: 64 },
};

/** A Chinese cue at exactly 40 characters: inside the Latin budget and well
 * past the Han one, so one input answers the question twice. */
export const PER_SCRIPT_BUDGET_CUE = 58;

/** A Japanese cue with a Latin word welded to the kana either side of it. */
export const WELDED_LATIN_CUE = 53;
/** A Russian cue the model left Han characters in. */
export const LEAKED_HAN_CUE = 63;

const ARABIC_ROWS: Rendering[] = [
  { n: 1, en: 'NIGHT FERRY\nEpisode 2: The Late Crossing', target: 'مركب الليل\nالحلقة الثانية: العبور المتأخر' },
  { n: 2, en: '- Are you on the night shift again?\n- Every night this week.', target: '- هل أنت في نوبة الليل مجددا؟\n- كل ليلة هذا الأسبوع.' },
  { n: 3, en: 'Nadia signs the log at eleven.', target: 'نادية توقع السجل في الحادية عشرة.' },
  { n: 4, en: 'Night shift never gets the good coffee.', target: 'نوبة الليل لا تحصل على القهوة الجيدة أبدا.' },
  { n: 5, en: 'Omar says the engine is fine.', target: 'عمر يقول إن المحرك بخير.' },
  { n: 6, en: 'He always says that.', target: 'هو يقول ذلك دائما.' },
  { n: 7, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}في مكان ما نورس يضحك.{\\i0}' },
  { n: 8, en: 'Get the ropes off the bollard!', target: 'أنزل الحبال عن العمود.' },
  { n: 9, en: 'Rafiq checks the mooring lines twice.', target: 'رفيق يفحص حبال الرسو مرتين.' },
  { n: 10, en: 'Rafiq will be here in ten minutes.', target: 'رفيق سيصل خلال عشر دقائق.' },
  { n: 11, en: 'We run a safety drill before every crossing.', target: 'نجري تدريبا على السلامة قبل كل عبور.' },
  { n: 12, en: 'The safety drill takes four minutes.', target: 'تدريب السلامة يستغرق أربع دقائق.' },
  { n: 13, en: 'Safety drill sheets go in the blue folder.', target: 'أوراق التدريب توضع في الملف الأزرق.' },
  { n: 14, en: 'This ferry terminal closes at two.', target: 'هذه المحطة تغلق في الثانية.' },
  { n: 15, en: 'The ferry terminal has one working light.', target: 'المحطة فيها مصباح واحد يعمل.' },
  { n: 16, en: 'Ferry terminal rules are on the wall.', target: 'قواعد المحطة على الحائط.' },
  { n: 17, en: 'Take the lantern and go down.', target: 'خُذِ الفَانُوسَ وَانْزِلْ.' },
  { n: 18, en: '<i>The lamp room is cold tonight.</i>', target: 'غرفة المصباح باردة الليلة.' },
  { n: 19, en: '{\\i1}The crossing is over.{\\i0}', target: '{\\i1}{\\i0}{\\i1}انتهى العبور.{\\i0}{\\i1}{\\i0}' },
  { n: 20, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'نادية تعد التذاكر وتضعها في العلبة.' },
  { n: 21, en: 'The last boat is already tied up,', target: 'المركب الأخير مربوط بالفعل فيمكن إنزال المنحدر' },
  { n: 22, en: 'so that ramp can come down now.', target: 'المنحدر.' },
  { n: 23, en: 'Omar, the winch is stuck again.', target: 'عمر، الرافعة عالقة مجددا.' },
  { n: 24, en: 'Then hit it with something heavy!', target: 'إذن اضربها بشيء ثقيل.' },
  { n: 25, en: 'The inspector wants the fuel log,', target: 'المفتش يريد سجل الوقود وأيضا أوراق التأمين' },
  { n: 26, en: 'and the insurance papers as well.', target: 'أوراق التأمين.' },
  { n: 27, en: 'Nadia, we are a full nine minutes behind schedule.', target: 'نادية، نحن متأخرون تسع دقائق كاملة عن الموعد.' },
  { n: 28, en: 'Nine minutes is a whole crossing.', target: 'تسع دقائق تعادل رحلة عبور كاملة.' },
  { n: 29, en: '- Where is the manifest?\n- Under the radio.', target: 'أين البيان؟\nتحت الراديو.' },
  { n: 30, en: '- Did you sign the log?\n- Nadia signed it.', target: 'هل وقعت السجل؟ نادية وقعته.' },
  { n: 31, en: 'The blue folder is under the desk.', target: 'الملف الأزرق تحت المكتب.' },
  { n: 32, en: 'I put the manifest in it myself.', target: 'وضعت البيان فيه بنفسي.' },
  { n: 33, en: 'Then find it before the inspector does!', target: 'إذن اعثر عليه قبل المفتش.' },
  { n: 34, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'من الصعب أحيانا أن تعرف متى يستحق العبور ثمن الوقود المحروق فيه.' },
  { n: 35, en: 'Nine hundred crates, all counted.', target: 'تسعمئة صندوق، جميعها معدودة.' },
  { n: 36, en: 'The tide turns in an hour.', target: 'المد ينقلب خلال ساعة.' },
  { n: 37, en: 'Then we leave before it does.', target: 'إذن نغادر قبل ذلك.' },
  { n: 38, en: 'Nadia writes the time on her hand,', target: 'نادية تكتب الوقت على يدها' },
  { n: 39, en: 'and your night shift finally begins.', target: 'وَتَبْدَأُ نَوْبَتُكَ أَخِيرًا.' },
  { n: 40, en: 'Omar hums something old.', target: 'يُدَنْدِنُ عُمَرُ بِشَيْءٍ قَدِيمٍ.' },
  { n: 41, en: 'The water is very black tonight.', target: 'المَاءُ شَدِيدُ السَّوَادِ اللَّيْلَةَ.' },
  { n: 42, en: 'Rafiq counts the lights on the far shore.', target: 'يَعُدُّ رَفِيقٌ الأَضْوَاءَ عَلَى الشَّاطِئِ البَعِيدِ.' },
  { n: 43, en: 'Night shift crews sign the sheet here.', target: 'تُوَقِّعُ أَطْقُمُ النَّوْبَةِ الوَرَقَةَ هُنَا.' },
  { n: 44, en: 'Nadia turns off the office lamp.', target: 'تُطْفِئُ نَادِيَةُ مِصْبَاحَ المَكْتَبِ.' },
  { n: 45, en: 'The engine settles into its own rhythm.', target: 'يَسْتَقِرُّ المُحَرِّكُ عَلَى إِيقَاعِهِ.' },
  { n: 46, en: 'Nobody says anything for a while.', target: 'لَا يَقُولُ أَحَدٌ شَيْئًا لِفَتْرَةٍ.' },
  { n: 47, en: 'The crossing takes twenty minutes.', target: 'يَسْتَغْرِقُ العُبُورُ عِشْرِينَ دَقِيقَةً.' },
  { n: 48, en: 'Then they do it all again.', target: 'ثُمَّ يُعِيدُونَ كُلَّ شَيْءٍ.' },
];

// The same source cues rendered into a no-space script: a line break lands
// between characters, and the budget is 16 columns rather than 42.
const JAPANESE_ROWS: Rendering[] = [
  { n: 49, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}どこかでカモメが笑っている。{\\i0}' },
  { n: 50, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'ナディアが切符を数え\n缶にしまう。' },
  { n: 51, en: '- Are you on the night shift again?\n- Every night this week.', target: '- また夜勤なのか。\n- 今週は毎晩だ。' },
  { n: 52, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'その渡りが燃料に見合うかは、いつも分かるわけではない。' },
  { n: 53, en: 'Rafiq called from the pier.', target: 'ラフィクがpierから電話した。' },
];

// Han only, and the one file carrying a cue whose length answers differently
// depending on which script's budget is asked.
const CHINESE_ROWS: Rendering[] = [
  { n: 54, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}某处有海鸥在笑。{\\i0}' },
  { n: 55, en: 'Nadia counts the tickets\nand puts them in the tin.', target: '娜迪亚数完船票\n把它们放进铁盒。' },
  { n: 56, en: '- Are you on the night shift again?\n- Every night this week.', target: '- 又是夜班吗？\n- 这周每晚都是。' },
  { n: 57, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: '有时很难说清一趟渡船是否值回烧掉的燃料。' },
  { n: 58, en: 'The inspector wants the fuel log and the insurance papers, and the blue folder is back under the desk.', target: '检查员今晚要燃料记录和保险单，所以娜迪亚在开船之前把蓝色文件夹放在了办公桌下面。' },
];

// Word breaks at the same 42 columns Arabic uses, with none of the Arabic
// punctuation, diacritic or variant machinery: the control for "leaves the
// other scripts alone".
const RUSSIAN_ROWS: Rendering[] = [
  { n: 59, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}Где-то смеётся чайка.{\\i0}' },
  { n: 60, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'Надя считает билеты\nи убирает их в жестянку.' },
  { n: 61, en: '- Are you on the night shift again?\n- Every night this week.', target: '- Ты снова в ночной смене?\n- Каждую ночь на этой неделе.' },
  { n: 62, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'Иногда трудно понять, когда переправа стоит потраченного топлива.' },
  { n: 63, en: 'The label on the crate is unreadable.', target: 'Надпись на ящике 你好 неразборчива.' },
];

const SPANISH_ROWS: Rendering[] = [
  { n: 64, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', target: '{\\i1}En algún lugar ríe una gaviota.{\\i0}' },
  { n: 65, en: 'Nadia counts the tickets\nand puts them in the tin.', target: 'Nadia cuenta los billetes\ny los guarda en la lata.' },
  { n: 66, en: '- Are you on the night shift again?\n- Every night this week.', target: '- ¿Otra vez en el turno de noche?\n- Todas las noches esta semana.' },
  { n: 67, en: 'It is not always easy to tell when a crossing is worth the fuel.', target: 'No siempre es fácil saber cuándo una travesía compensa el combustible.' },
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

/** Cues 7 and 20 as a Windows-newline SRT file, the shape a real subtitle
 * download arrives in. Parsing renumbers to 1..n, so the wrapped cue is block
 * 1 and the two-line cue is block 2. */
export const CRLF_SAMPLE_SRT =
  '7\r\n00:00:18,120 --> 00:00:20,400\r\n{\\i1}Somewhere a gull is laughing.{\\i0}\r\n'
  + '\r\n'
  + '20\r\n00:01:02,000 --> 00:01:05,100\r\nNadia counts the tickets\r\n'
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
