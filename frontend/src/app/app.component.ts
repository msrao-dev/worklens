import { Component, ElementRef, ViewChild, AfterViewChecked, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { SafeHtmlPipe } from './safe-html.pipe';
import { KB, SUGGEST, SHORTCUTS, KBEntry, Source, Shortcut } from './worklens.data';
import { environment } from '../environments/environment';

interface Msg {
  role: 'user' | 'bot';
  text?: string;
  entry?: KBEntry;
  fallback?: boolean;
  typing?: boolean;
  up?: boolean;
  down?: boolean;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, SafeHtmlPipe],
  templateUrl: './app.component.html'
})
export class AppComponent implements AfterViewChecked {
  suggest: string[] = SUGGEST;
  shortcuts: Shortcut[] = SHORTCUTS;
  messages: Msg[] = [];
  input = '';
  dark = false;
  railOpen = false;
  selectedSource: Source | null = null;
  toastMsg = '';
  toastShow = false;

  private toastT: any;
  private pendingScroll = false;

  private http = inject(HttpClient);
  private apiUrl = (environment.apiUrl || 'http://127.0.0.1:8000') + '/query';
  private useMock = (environment as any).useMock ?? false;

  @ViewChild('stream') stream?: ElementRef<HTMLDivElement>;

  constructor() {
    try {
      const saved = localStorage.getItem('wl-theme');
      if (saved) this.dark = saved === 'dark';
      else this.dark = !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    } catch {
      this.dark = false;
    }
    this.applyTheme();

    // Seed the opening state with one worked example
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

  isStale(updated: string): boolean {
    return /202[0-4]/.test(updated);
  }

  /** Keyword match against the sample KB. */
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

  ask(text: string): void {
    text = (text || '').trim();
    if (!text) return;

    this.messages.push({ role: 'user', text });
    const typingMsg: Msg = { role: 'bot', typing: true };
    this.messages.push(typingMsg);
    this.pendingScroll = true;
    this.railOpen = false;

    // 1. Local mock bypass for instant testing
    if (this.useMock) {
      const e = this.match(text);
      setTimeout(() => {
        const i = this.messages.indexOf(typingMsg);
        if (i > -1) this.messages.splice(i, 1);
        this.messages.push(e ? { role: 'bot', entry: e } : { role: 'bot', fallback: true });
        this.pendingScroll = true;
      }, 500);
      return;
    }

    // 2. Real API call to FastAPI backend
    this.http.post<{ answer: string; sources: string[] }>(this.apiUrl, { question: text })
      .subscribe({
        next: (res) => {
          const i = this.messages.indexOf(typingMsg);
          if (i > -1) this.messages.splice(i, 1);

          const isNotFound = res.answer.includes("Information not found");

          if (isNotFound) {
            this.messages.push({ role: 'bot', fallback: true });
          } else {
            const sources: Source[] = (res.sources || []).map((doc, idx) => ({
              title: `Document Chunk ${idx + 1}`,
              section: 'Knowledge Base',
              updated: '2026',
              fresh: true,
              passage: doc.replace(/\n/g, '<br/>')
            }));

            const entry: KBEntry = {
              id: String(Date.now()),
              cat: 'Policy',
              kw: [],
              conf: 'high',
              answer: `<p>${res.answer.replace(/\n/g, '<br/>')}</p>`,
              sources
            };

            this.messages.push({ role: 'bot', entry });
          }
          this.pendingScroll = true;
        },
        error: (err) => {
          console.error('API Error:', err);
          const i = this.messages.indexOf(typingMsg);
          if (i > -1) this.messages.splice(i, 1);
          this.messages.push({ role: 'bot', fallback: true });
          this.pendingScroll = true;
        }
      });
  }

  send(): void {
    if (this.input.trim()) {
      this.ask(this.input);
      this.input = '';
    }
  }

  onKey(ev: KeyboardEvent): void {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      this.send();
    }
  }

  openDoc(s: Source): void {
    this.selectedSource = s;
  }

  closeDoc(): void {
    this.selectedSource = null;
  }

  vote(m: Msg, up: boolean): void {
    m.up = up;
    m.down = !up;
    this.toast(up ? 'Thanks — feedback noted' : 'Thanks — we’ll use this to improve');
  }

  copy(m: Msg): void {
    const tmp = document.createElement('div');
    tmp.innerHTML = m.entry ? m.entry.answer : '';
    const txt = tmp.innerText;
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
    } catch { }
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
