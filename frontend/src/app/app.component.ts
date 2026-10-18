import { Component, ElementRef, ViewChild, AfterViewChecked, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SafeHtmlPipe } from './safe-html.pipe';
import { KB, SHORTCUTS, KBEntry, Source, Shortcut } from './worklens.data';

interface Attachment {
  name: string;
  /** Object URL for image previews; undefined for other files. */
  url?: string;
}

interface Msg {
  role: 'user' | 'bot';
  text?: string;
  files?: Attachment[];
  entry?: KBEntry;
  fallback?: boolean;
  /** Plain bot reply, e.g. acknowledging an upload. */
  note?: string;
  typing?: boolean;
}

const FALLBACK_TEXT =
  'I couldn’t find a confident answer to that in the approved company documents, so I’d rather not guess. ' +
  'You can try rephrasing, or I can point you to someone who’ll know.';

const HISTORY_KEY = 'wl-history';
const HISTORY_MAX = 20;
const MAX_FILE_BYTES = 10 * 1024 * 1024;

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, SafeHtmlPipe],
  templateUrl: './app.component.html'
})
export class AppComponent implements AfterViewChecked, OnDestroy {
  history: string[] = this.loadHistory();
  shortcuts: Shortcut[] = SHORTCUTS;
  messages: Msg[] = [];
  input = '';
  pending: Attachment[] = [];
  speaking: Msg | null = null;
  dark = false;
  railOpen = false;
  selectedSource: Source | null = null;
  toastMsg = '';
  toastShow = false;

  private toastT: any;
  private pendingScroll = false;
  private utterance: SpeechSynthesisUtterance | null = null;

  @ViewChild('stream') stream?: ElementRef<HTMLDivElement>;

  constructor(private zone: NgZone) {
    try {
      const saved = localStorage.getItem('wl-theme');
      if (saved) this.dark = saved === 'dark';
      else this.dark = !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    } catch {
      this.dark = false;
    }
    this.applyTheme();

    // Seed the opening state with one worked example, so the first view
    // shows the product in action rather than an empty shell.
    this.messages.push({ role: 'user', text: 'How many leave days do I have left?' });
    this.messages.push({ role: 'bot', entry: KB[0] });
  }

  ngAfterViewChecked(): void {
    if (this.pendingScroll && this.stream) {
      const el = this.stream.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.pendingScroll = false;
    }
  }

  ngOnDestroy(): void {
    this.stopSpeaking();
  }

  isStale(updated: string): boolean {
    return /202[0-4]/.test(updated);
  }

  /** Keyword match against the sample KB. In production this is the RAG call. */
  private match(text: string): KBEntry | null {
    const t = ' ' + text.toLowerCase().replace(/[^a-z0-9\s']/g, ' ').replace(/\s+/g, ' ') + ' ';
    let best: KBEntry | null = null;
    let bestScore = 0;
    for (const e of KB) {
      let score = 0;
      for (const k of e.kw) {
        if (t.indexOf(k) > -1) score += k.split(' ').length;
      }
      if (score > bestScore) {
        bestScore = score;
        best = e;
      }
    }
    return bestScore >= 1 ? best : null;
  }

  ask(text: string, files: Attachment[] = []): void {
    text = (text || '').trim();
    if (!text && !files.length) return;
    this.messages.push({ role: 'user', text, files });
    if (text) this.remember(text);
    const typingMsg: Msg = { role: 'bot', typing: true };
    this.messages.push(typingMsg);
    this.pendingScroll = true;
    this.railOpen = false;

    const e = text ? this.match(text) : null;
    setTimeout(() => {
      const i = this.messages.indexOf(typingMsg);
      if (i > -1) this.messages.splice(i, 1);
      let reply: Msg;
      if (e) reply = { role: 'bot', entry: e };
      else if (text) reply = { role: 'bot', fallback: true };
      else
        reply = {
          role: 'bot',
          note: `Got your ${files.length === 1 ? 'file' : files.length + ' files'}. This demo can’t read uploads yet — once the backend is connected, I’ll answer questions from them.`
        };
      this.messages.push(reply);
      this.pendingScroll = true;
    }, 650);
  }

  send(): void {
    if (!this.input.trim() && !this.pending.length) return;
    this.ask(this.input, this.pending);
    this.input = '';
    this.pending = [];
  }

  onKey(ev: KeyboardEvent): void {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      this.send();
    }
  }

  // --- History -------------------------------------------------------------

  private loadHistory(): string[] {
    try {
      const v = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
    } catch {
      return [];
    }
  }

  private saveHistory(): void {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(this.history));
    } catch {}
  }

  /** Most recent first; asking the same question again moves it to the top. */
  private remember(q: string): void {
    const key = q.toLowerCase();
    this.history = [q, ...this.history.filter((h) => h.toLowerCase() !== key)].slice(0, HISTORY_MAX);
    this.saveHistory();
  }

  clearHistory(): void {
    this.history = [];
    this.saveHistory();
  }

  // --- Attachments ---------------------------------------------------------

  onFiles(input: HTMLInputElement): void {
    for (const f of Array.from(input.files || [])) {
      if (f.size > MAX_FILE_BYTES) {
        this.toast(`${f.name} is over 10 MB`);
        continue;
      }
      this.pending.push({
        name: f.name,
        url: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined
      });
    }
    input.value = '';
  }

  removePending(i: number): void {
    const [f] = this.pending.splice(i, 1);
    if (f?.url) URL.revokeObjectURL(f.url);
  }

  // --- Read aloud ----------------------------------------------------------

  private plainText(m: Msg): string {
    if (m.fallback) return FALLBACK_TEXT;
    if (m.note) return m.note;
    const tmp = document.createElement('div');
    // Break after block elements so list items aren't run together.
    tmp.innerHTML = (m.entry?.answer || '').replace(/<\/(p|li)>/g, '$&\n');
    return (tmp.textContent || '').trim();
  }

  speak(m: Msg): void {
    if (this.speaking === m) {
      this.stopSpeaking();
      return;
    }
    if (!('speechSynthesis' in window)) {
      this.toast('Read aloud isn’t supported in this browser');
      return;
    }
    this.stopSpeaking();
    const u = new SpeechSynthesisUtterance(this.plainText(m));
    const done = () =>
      this.zone.run(() => {
        // cancel() fires end/error for the previous utterance; ignore stale ones.
        if (this.utterance === u) {
          this.utterance = null;
          this.speaking = null;
        }
      });
    u.onend = done;
    u.onerror = done;
    this.utterance = u;
    this.speaking = m;
    window.speechSynthesis.speak(u);
  }

  private stopSpeaking(): void {
    this.utterance = null;
    this.speaking = null;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }

  // -------------------------------------------------------------------------

  openDoc(s: Source): void {
    this.selectedSource = s;
  }
  closeDoc(): void {
    this.selectedSource = null;
  }

  copy(m: Msg): void {
    const txt = this.plainText(m);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(
        () => this.toast('Answer copied'),
        () => this.toast('Answer copied')
      );
    } else {
      this.toast('Answer copied');
    }
  }

  askHuman(): void {
    this.toast('Routed to the HR Helpdesk · you’ll get a reply by email (demo)');
  }

  toggleTheme(): void {
    this.dark = !this.dark;
    try {
      localStorage.setItem('wl-theme', this.dark ? 'dark' : 'light');
    } catch {}
    this.applyTheme();
  }

  private applyTheme(): void {
    document.documentElement.setAttribute('data-theme', this.dark ? 'dark' : 'light');
  }

  toast(msg: string): void {
    this.toastMsg = msg;
    this.toastShow = true;
    clearTimeout(this.toastT);
    this.toastT = setTimeout(() => (this.toastShow = false), 2600);
  }

  trackByIndex(i: number): number {
    return i;
  }
}
