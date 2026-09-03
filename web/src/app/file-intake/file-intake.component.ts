import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import {
  FileStatus,
  SUBTITLE_ACCEPT,
  SUBTITLE_EXTS,
  UploadedFile,
} from '../core/file-types';
import { parseSubtitle } from '../core/subtitle-formats';
import { errMessage } from '../error-message';

// The files rail: the dropzone, the queued files with where each one is in
// the run, and the read-and-parse
// path every incoming file goes through.
@Component({
  selector: 'app-file-intake',
  templateUrl: './file-intake.component.html',
  styleUrl: './file-intake.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FileIntakeComponent {
  files = input.required<UploadedFile[]>();
  fileCountLabel = input.required<string>();
  supportedFormats = input.required<string[]>();
  isTranslating = input.required<boolean>();
  isDone = input.required<boolean>();
  /** The run's view of each file, by the same index; empty before a run. */
  statuses = input<FileStatus[]>([]);

  filesAdded = output<UploadedFile[]>();
  fileRemoved = output<number>();
  errorMessage = output<string>();

  subtitleAccept = SUBTITLE_ACCEPT;
  dragOver = signal(false);

  onDragOver(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(true);
  }

  onDragLeave(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(false);
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(false);
    const fileList = event.dataTransfer?.files;
    if (fileList) this.handleFiles(fileList);
  }

  onFileSelect(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      this.handleFiles(input.files);
      input.value = '';
    }
  }

  async handleFiles(fileList: FileList) {
    this.errorMessage.emit('');
    const incoming: UploadedFile[] = [];
    const existingNames = new Set(this.files().map((f) => f.name));
    const rejected: { name: string; reason: string }[] = [];

    for (const file of Array.from(fileList)) {
      const lower = file.name.toLowerCase();
      if (!SUBTITLE_EXTS.some((ext) => lower.endsWith(ext))) {
        rejected.push({ name: file.name, reason: 'unsupported extension' });
        continue;
      }
      if (existingNames.has(file.name)) continue;

      let content: string;
      try {
        content = await this.readFile(file);
      } catch (err) {
        rejected.push({ name: file.name, reason: errMessage(err, 'could not be read') });
        continue;
      }

      try {
        const doc = parseSubtitle(file.name, content);
        if (doc.blocks.length === 0) {
          rejected.push({ name: file.name, reason: 'no subtitle blocks found' });
          continue;
        }
        incoming.push({
          name: file.name,
          blockCount: doc.blocks.length,
          doc,
        });
      } catch (err) {
        rejected.push({
          name: file.name,
          reason: errMessage(err, 'could not be parsed'),
        });
      }
    }

    if (incoming.length === 0 && this.files().length === 0) {
      this.errorMessage.emit(
        rejected.length > 0
          ? this.formatRejected(rejected)
          : `Please select subtitle files (${SUBTITLE_EXTS.join(', ')}).`,
      );
      return;
    }

    if (rejected.length > 0) {
      this.errorMessage.emit(this.formatRejected(rejected));
    }

    this.filesAdded.emit(incoming);
  }

  private formatRejected(rejected: { name: string; reason: string }[]): string {
    const details = rejected.map((r) => `${r.name} (${r.reason})`).join('; ');
    return `Skipped: ${details}.`;
  }

  removeFile(index: number) {
    this.fileRemoved.emit(index);
  }

  statusOf(index: number): FileStatus['status'] | 'queued' {
    return this.statuses()[index]?.status ?? 'queued';
  }

  flaggedIn(index: number): number {
    return this.statuses()[index]?.review?.cues.filter((c) => c.flags.length > 0).length ?? 0;
  }

  /** The one line under a file's name: what it is, or what became of it. */
  meta(index: number): string {
    const file = this.files()[index];
    const status = this.statuses()[index];
    const blocks = `${file?.blockCount ?? 0} lines`;
    if (!status) return blocks;
    switch (status.status) {
      case 'translating': {
        const p = status.progress;
        switch (p?.stage) {
          case 'batches':
            return `${blocks}, batch ${p.done} of ${p.total}`;
          case 'checking':
            return `${blocks}, checking the meaning survived, ${p.done} of ${p.total}`;
          case 'repairing':
            return `${blocks}, repairing the flagged lines, ${p.done} of ${p.total}`;
          default:
            return `${blocks}, reading names, terms and speakers first`;
        }
      }
      case 'done': {
        const flagged = this.flaggedIn(index);
        return flagged ? `${blocks}, ${flagged} flagged` : `${blocks}, none flagged`;
      }
      case 'failed':
        return status.error ?? 'Failed';
      default:
        return blocks;
    }
  }

  // Strict UTF-8 like the CLI (BOM stripped): bad bytes fail instead of becoming U+FFFD.
  private async readFile(file: File): Promise<string> {
    let buffer: ArrayBuffer;
    try {
      buffer = await file.arrayBuffer();
    } catch {
      throw new Error('could not be read');
    }

    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
    } catch {
      throw new Error(
        'not valid UTF-8 — re-save it as UTF-8 (legacy encodings such as Windows-1256 are not supported)',
      );
    }
  }
}
