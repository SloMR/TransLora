// A synthetic English -> Arabic run, cue by cue: 48 invented source cues
// beside an invented "model output" for each. Every defect the deterministic
// repairs exist for is planted here on purpose, so a whole-file pass over the
// fixture measures what unit tests cannot — how often a detector fires on the
// cues it should leave alone.
//
// Written by hand for this repository. The shapes, not the words, are the
// contract; the landmark exports below name the cue each shape lives in, so a
// test never has to hard-code a number it cannot explain.

export interface AlignedCue {
  n: number;
  en: string;
  ar: string;
}

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

export const ALIGNED_CUES: AlignedCue[] = [
  { n: 1, en: 'NIGHT FERRY\nEpisode 2: The Late Crossing', ar: 'مركب الليل\nالحلقة الثانية: العبور المتأخر' },
  { n: 2, en: '- Are you on the night shift again?\n- Every night this week.', ar: '- هل أنت في نوبة الليل مجددا؟\n- كل ليلة هذا الأسبوع.' },
  { n: 3, en: 'Nadia signs the log at eleven.', ar: 'نادية توقع السجل في الحادية عشرة.' },
  { n: 4, en: 'Night shift never gets the good coffee.', ar: 'نوبة الليل لا تحصل على القهوة الجيدة أبدا.' },
  { n: 5, en: 'Omar says the engine is fine.', ar: 'عمر يقول إن المحرك بخير.' },
  { n: 6, en: 'He always says that.', ar: 'هو يقول ذلك دائما.' },
  { n: 7, en: '{\\i1}Somewhere a gull is laughing.{\\i0}', ar: '{\\i1}في مكان ما نورس يضحك.{\\i0}' },
  { n: 8, en: 'Get the ropes off the bollard!', ar: 'أنزل الحبال عن العمود.' },
  { n: 9, en: 'Rafiq checks the mooring lines twice.', ar: 'رفيق يفحص حبال الرسو مرتين.' },
  { n: 10, en: 'Rafiq will be here in ten minutes.', ar: 'رفيق سيصل خلال عشر دقائق.' },
  { n: 11, en: 'We run a safety drill before every crossing.', ar: 'نجري تدريبا على السلامة قبل كل عبور.' },
  { n: 12, en: 'The safety drill takes four minutes.', ar: 'تدريب السلامة يستغرق أربع دقائق.' },
  { n: 13, en: 'Safety drill sheets go in the blue folder.', ar: 'أوراق التدريب توضع في الملف الأزرق.' },
  { n: 14, en: 'This ferry terminal closes at two.', ar: 'هذه المحطة تغلق في الثانية.' },
  { n: 15, en: 'The ferry terminal has one working light.', ar: 'المحطة فيها مصباح واحد يعمل.' },
  { n: 16, en: 'Ferry terminal rules are on the wall.', ar: 'قواعد المحطة على الحائط.' },
  { n: 17, en: 'Take the lantern and go down.', ar: 'خُذِ الفَانُوسَ وَانْزِلْ.' },
  { n: 18, en: '<i>The lamp room is cold tonight.</i>', ar: 'غرفة المصباح باردة الليلة.' },
  { n: 19, en: '{\\i1}The crossing is over.{\\i0}', ar: '{\\i1}{\\i0}{\\i1}انتهى العبور.{\\i0}{\\i1}{\\i0}' },
  { n: 20, en: 'Nadia counts the tickets\nand puts them in the tin.', ar: 'نادية تعد التذاكر وتضعها في العلبة.' },
  { n: 21, en: 'The last boat is already tied up,', ar: 'المركب الأخير مربوط بالفعل فيمكن إنزال المنحدر' },
  { n: 22, en: 'so that ramp can come down now.', ar: 'المنحدر.' },
  { n: 23, en: 'Omar, the winch is stuck again.', ar: 'عمر، الرافعة عالقة مجددا.' },
  { n: 24, en: 'Then hit it with something heavy!', ar: 'إذن اضربها بشيء ثقيل.' },
  { n: 25, en: 'The inspector wants the fuel log,', ar: 'المفتش يريد سجل الوقود وأيضا أوراق التأمين' },
  { n: 26, en: 'and the insurance papers as well.', ar: 'أوراق التأمين.' },
  { n: 27, en: 'Nadia, we are a full nine minutes behind schedule.', ar: 'نادية، نحن متأخرون تسع دقائق كاملة عن الموعد.' },
  { n: 28, en: 'Nine minutes is a whole crossing.', ar: 'تسع دقائق تعادل رحلة عبور كاملة.' },
  { n: 29, en: '- Where is the manifest?\n- Under the radio.', ar: 'أين البيان؟\nتحت الراديو.' },
  { n: 30, en: '- Did you sign the log?\n- Nadia signed it.', ar: 'هل وقعت السجل؟ نادية وقعته.' },
  { n: 31, en: 'The blue folder is under the desk.', ar: 'الملف الأزرق تحت المكتب.' },
  { n: 32, en: 'I put the manifest in it myself.', ar: 'وضعت البيان فيه بنفسي.' },
  { n: 33, en: 'Then find it before the inspector does!', ar: 'إذن اعثر عليه قبل المفتش.' },
  { n: 34, en: 'It is not always easy to tell when a crossing is worth the fuel.', ar: 'من الصعب أحيانا أن تعرف متى يستحق العبور ثمن الوقود المحروق فيه.' },
  { n: 35, en: 'Nine hundred crates, all counted.', ar: 'تسعمئة صندوق، جميعها معدودة.' },
  { n: 36, en: 'The tide turns in an hour.', ar: 'المد ينقلب خلال ساعة.' },
  { n: 37, en: 'Then we leave before it does.', ar: 'إذن نغادر قبل ذلك.' },
  { n: 38, en: 'Nadia writes the time on her hand,', ar: 'نادية تكتب الوقت على يدها' },
  { n: 39, en: 'and your night shift finally begins.', ar: 'وَتَبْدَأُ نَوْبَتُكَ أَخِيرًا.' },
  { n: 40, en: 'Omar hums something old.', ar: 'يُدَنْدِنُ عُمَرُ بِشَيْءٍ قَدِيمٍ.' },
  { n: 41, en: 'The water is very black tonight.', ar: 'المَاءُ شَدِيدُ السَّوَادِ اللَّيْلَةَ.' },
  { n: 42, en: 'Rafiq counts the lights on the far shore.', ar: 'يَعُدُّ رَفِيقٌ الأَضْوَاءَ عَلَى الشَّاطِئِ البَعِيدِ.' },
  { n: 43, en: 'Night shift crews sign the sheet here.', ar: 'تُوَقِّعُ أَطْقُمُ النَّوْبَةِ الوَرَقَةَ هُنَا.' },
  { n: 44, en: 'Nadia turns off the office lamp.', ar: 'تُطْفِئُ نَادِيَةُ مِصْبَاحَ المَكْتَبِ.' },
  { n: 45, en: 'The engine settles into its own rhythm.', ar: 'يَسْتَقِرُّ المُحَرِّكُ عَلَى إِيقَاعِهِ.' },
  { n: 46, en: 'Nobody says anything for a while.', ar: 'لَا يَقُولُ أَحَدٌ شَيْئًا لِفَتْرَةٍ.' },
  { n: 47, en: 'The crossing takes twenty minutes.', ar: 'يَسْتَغْرِقُ العُبُورُ عِشْرِينَ دَقِيقَةً.' },
  { n: 48, en: 'Then they do it all again.', ar: 'ثُمَّ يُعِيدُونَ كُلَّ شَيْءٍ.' },
];

/** Cues 7 and 20 as a Windows-newline SRT file, the shape a real subtitle
 * download arrives in. Parsing renumbers to 1..n, so the wrapped cue is block
 * 1 and the two-line cue is block 2. */
export const CRLF_SAMPLE_SRT =
  '7\r\n00:00:18,120 --> 00:00:20,400\r\n{\\i1}Somewhere a gull is laughing.{\\i0}\r\n'
  + '\r\n'
  + '20\r\n00:01:02,000 --> 00:01:05,100\r\nNadia counts the tickets\r\n'
  + 'and puts them in the tin.\r\n';

/** The cue with the given fixture number. */
export function cue(n: number): AlignedCue {
  const found = ALIGNED_CUES.find((c) => c.n === n);
  if (!found) throw new Error(`no cue ${n} in the fixture`);
  return found;
}
