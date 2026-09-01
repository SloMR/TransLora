// Parsing what the prepass calls send back: the tagged scan sections and the
// `N=Speaker` attribution lines.

import {
  CharacterHint,
  FileContext,
  Gender,
  MAX_IDIOMS,
  MAX_TERMS,
  SceneHint,
  TermHint,
  detectParticipants,
  usableIdioms,
} from './context-pass';

// Closing tag optional so a truncated reply still parses.
const SECTION_RE =
  /<(register|characters|terms|idioms|scenes|notes)>\s*([\s\S]*?)\s*(?=<\/\1>|<(?:register|characters|terms|idioms|scenes|notes)>|$)/gi;
const SCENE_RANGE_RE = /^(\d+)\s*(?:-\s*(\d+))?$/;
const ATTRIB_LINE_RE = /^\s*(\d+)\s*=\s*(.+?)\s*$/;

function stripBullet(line: string): string {
  return line.trim().replace(/^[-*•]\s*/, '').trim();
}

function splitOnce(s: string, sep: string): [string, string] {
  const i = s.indexOf(sep);
  return i < 0 ? [s, ''] : [s.slice(0, i), s.slice(i + sep.length)];
}

// Parse the tagged response. Tolerates whitespace and bullet markers.
export function parseContextResponse(text: string): FileContext {
  const sections: Record<string, string> = {};
  SECTION_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SECTION_RE.exec(text || '')) !== null) {
    sections[m[1].toLowerCase()] = m[2];
  }

  const register = stripBullet((sections['register'] ?? '').split(/\s+/).join(' '));

  const characters: CharacterHint[] = [];
  for (const raw of (sections['characters'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (!line || !line.includes('=>')) continue;
    const [srcPart, restPart] = splitOnce(line, '=>');
    let tgt: string, gender: string;
    if (restPart.includes('|')) {
      const idx = restPart.lastIndexOf('|');
      tgt = restPart.slice(0, idx).trim();
      gender = restPart.slice(idx + 1).trim().toLowerCase();
    } else {
      tgt = restPart.trim();
      gender = 'unknown';
    }
    const g: Gender = gender === 'male' || gender === 'female' ? gender : 'unknown';
    const src = srcPart.trim();
    if (src && tgt) characters.push({ source: src, target: tgt, gender: g });
  }

  const pairs = (section: string): TermHint[] => {
    const out: TermHint[] = [];
    for (const raw of (sections[section] ?? '').split('\n')) {
      const line = stripBullet(raw);
      if (!line || !line.includes('=>')) continue;
      const [srcPart, tgtPart] = splitOnce(line, '=>');
      const src = srcPart.trim();
      const tgt = tgtPart.trim();
      if (src && tgt) out.push({ source: src, target: tgt });
    }
    return out;
  };
  const terms = pairs('terms').slice(0, MAX_TERMS);
  // Pruned before the cap, so dropping a poisoned entry lets a usable one in.
  const idioms = usableIdioms(terms, pairs('idioms')).slice(0, MAX_IDIOMS);

  const scenes: SceneHint[] = [];
  for (const raw of (sections['scenes'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (!line || !line.includes('=>')) continue;
    const [rangePart, descPart] = splitOnce(line, '=>');
    const desc = descPart.trim();
    const rm = SCENE_RANGE_RE.exec(rangePart.trim());
    if (!desc || !rm) continue;
    let start = parseInt(rm[1], 10);
    let end = rm[2] ? parseInt(rm[2], 10) : start;
    if (end < start) [start, end] = [end, start];
    scenes.push({
      start, end, description: desc,
      participants: detectParticipants(desc, characters),
      attribution: {},
    });
  }

  const notes: string[] = [];
  for (const raw of (sections['notes'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (line) notes.push(line);
  }

  return new FileContext(
    register,
    characters.slice(0, 20),
    terms,
    idioms,
    scenes.slice(0, 40),
    notes.slice(0, 4),
  );
}

export function parseAttributionResponse(
  raw: string, scene: SceneHint, characters: CharacterHint[],
): Record<number, string> {
  const valid = new Set<string>(characters.map((h) => h.source));
  valid.add('unknown');
  const out: Record<number, string> = {};
  for (const line of (raw || '').split('\n')) {
    const m = ATTRIB_LINE_RE.exec(line);
    if (!m) continue;
    const n = parseInt(m[1], 10);
    const name = m[2].trim().replace(/^["']|["']$/g, '');
    if (n >= scene.start && n <= scene.end && valid.has(name)) out[n] = name;
  }
  return out;
}
