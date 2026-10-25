import { Component, ElementRef, ViewChild, AfterViewChecked, OnDestroy, NgZone, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { SafeHtmlPipe } from './safe-html.pipe';
import { LoginComponent, User } from './login.component';
import { KB, QUESTIONS, RELATED, POPULAR, KBEntry, Source } from './worklens.data';
import { environment } from '../environments/environment';

interface Attachment {
  name: string;
  /** Object URL for image previews; undefined for other files. */
  url?: string;
  file?: File;
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
  /** Shown beside the typing dots, e.g. while reading an image. */
  status?: string;
  /** The question this reply answers (used in the PDF). */
  q?: string;
  /** Text read from attached images, if any. */
  imageText?: string;
  followups?: string[];
}

const FALLBACK_PARAS = [
  'I couldn’t find this in the company documents available to me.',
  'None of the indexed HR, IT, Facilities or Finance documents cover it closely enough to give a reliable answer, so I won’t guess. Try rephrasing, or I can route it to the right team.'
];

/** Minimum keyword score for an answer. In production this is the retrieval similarity threshold. */
const MIN_MATCH_SCORE = 1;

const USER_KEY = 'wl-user';
const HISTORY_KEY = 'wl-history';
const HISTORY_MAX = 20;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_IMAGES = 3;

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, SafeHtmlPipe, LoginComponent],
  templateUrl: './app.component.html'
})
export class AppComponent implements AfterViewChecked, OnDestroy {
  user: User | null = this.loadUser();
  history: string[] = this.loadHistory();
  messages: Msg[] = [];
  input = '';
  pending: Attachment[] = [];
  dragging = false;
  speaking: Msg | null = null;
  fallbackParas = FALLBACK_PARAS;
  dark = false;
  railOpen = false;
  selectedSource: Source | null = null;
  toastMsg = '';
  toastShow = false;

  private toastT: any;
  private pendingScroll = false;
  private utterance: SpeechSynthesisUtterance | null = null;

  private http = inject(HttpClient);
  private apiUrl = (environment.apiUrl || 'http://127.0.0.1:8000') + '/query';
  private useMock = (environment as any).useMock ?? false;

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
    this.messages.push({ role: 'bot', entry: KB[0], q: QUESTIONS[KB[0].id], followups: this.followups(KB[0].id) });
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

  // --- Session -------------------------------------------------------------

  get initials(): string {
    const parts = (this.user?.name || '').split(' ').filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
  }

  onLogin(user: User): void {
    this.user = user;
    try {
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    } catch {}
  }

  logout(): void {
    this.stopSpeaking();
    this.railOpen = false;
    this.user = null;
    try {
      localStorage.removeItem(USER_KEY);
    } catch {}
  }

  private loadUser(): User | null {
    try {
      const u = JSON.parse(localStorage.getItem(USER_KEY) || 'null');
      return u?.email ? u : null;
    } catch {
      return null;
    }
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
    return bestScore >= MIN_MATCH_SCORE ? best : null;
  }

  private followups(id: string): string[] {
    return (RELATED[id] || []).map((r) => QUESTIONS[r]);
  }

  /** Gathers previous conversation turns to provide conversational context for follow-up questions. */
  private getChatHistory(): Array<{ role: 'user' | 'assistant'; text: string }> {
    return this.messages
      .filter((m) => !m.typing)
      .map((m) => {
        if (m.role === 'user') {
          return { role: 'user' as const, text: m.text || '' };
        }
        return {
          role: 'assistant' as const,
          text: this.plainText(m)
        };
      })
      .filter((m) => m.text.trim().length > 0)
      .slice(-6);
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

    const images = files.filter((f) => f.url && f.file);
    const started = Date.now();
    if (images.length && !this.useMock) typingMsg.status = 'Analyzing your image…';
    const reading = images.length && this.useMock ? this.readImages(images, typingMsg) : Promise.resolve('');
    reading.then((fromImage) => {
      // Keep a short "typing" pause even when there was nothing to read.
      const wait = Math.max(0, 650 - (Date.now() - started));
      setTimeout(() => this.reply(typingMsg, text, files, images.length > 0, fromImage), wait);
    });
  }

  private reply(typingMsg: Msg, text: string, files: Attachment[], hadImages: boolean, fromImage: string): void {
    const query = [text, fromImage].filter(Boolean).join(' ');
    const imageText = fromImage || undefined;
    const images = files.filter((f) => f.url && f.file);

    // Real API call to the FastAPI backend, unless mock data is switched on.
    if ((text || images.length) && !this.useMock) {
      // Exclude the current user message to retain prior dialogue context only
      const historyPayload = this.getChatHistory().slice(0, -1);

      let body: object | FormData;
      if (images.length) {
        const form = new FormData();
        form.append('question', text);
        form.append('history', JSON.stringify(historyPayload));
        images.forEach((image) => form.append('image', image.file!, image.name));
        body = form;
      } else {
        body = { question: text, history: historyPayload };
      }

      this.http.post<{
        answer: string;
        sources: string[];
        source_refs?: Array<{ source?: string; chunk_index?: number | null; text: string }>;
        suggested_questions?: string[];
        is_greeting?: boolean;
      }>(this.apiUrl, body).subscribe({
        next: (res) => {
          const suggestedQuestions = Array.isArray(res.suggested_questions)
            ? res.suggested_questions.filter((question) => typeof question === 'string' && !!question.trim())
            : [];

          if (!res.is_greeting && /^Information not found\.?$/i.test(res.answer.trim())) {
            this.push(typingMsg, this.fallbackReply(text, imageText, suggestedQuestions));
            return;
          }

          // Greeting or direct model conversational guidance
          if (res.is_greeting) {
            this.push(typingMsg, {
              role: 'bot',
              note: res.answer,
              followups: suggestedQuestions.length ? suggestedQuestions : POPULAR.map((id) => QUESTIONS[id])
            });
            return;
          }

          const sourceRefs = res.source_refs || [];
          const sources: Source[] = sourceRefs.length
            ? sourceRefs.map((source, idx) => ({
                title: source.source || `Document Chunk ${idx + 1}`,
                section: source.chunk_index == null ? 'Knowledge Base' : `Chunk ${source.chunk_index + 1}`,
                updated: '2026',
                fresh: true,
                passage: source.text.replace(/\n/g, '<br/>')
              }))
            : (res.sources || []).map((doc, idx) => ({
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
          this.push(typingMsg, {
            role: 'bot',
            entry,
            q: text || query,
            imageText,
            followups: suggestedQuestions
          });
        },
        error: (err) => {
          const detail = typeof err.error?.detail === 'string' ? err.error.detail : '';
          this.push(typingMsg, {
            role: 'bot',
            note: detail || 'WorkLens could not process this query. Please try again.'
          });
        }
      });
      return;
    }

    const isGreetingMock = /^(hi|hello|hey|good\s+(morning|afternoon|evening))\b/i.test(query.trim());
    if (isGreetingMock) {
      this.push(typingMsg, {
        role: 'bot',
        note: 'Hello! I am your WorkLens assistant. How can I assist you with the employee handbook, leave policy, or workplace benefits today?',
        followups: POPULAR.map((id) => QUESTIONS[id])
      });
      return;
    }

    const e = query ? this.match(query) : null;
    let reply: Msg;
    if (e) {
      reply = { role: 'bot', entry: e, q: text || QUESTIONS[e.id], imageText, followups: this.followups(e.id) };
    } else if (query) {
      reply = this.fallbackReply(text, imageText);
    } else if (hadImages) {
      reply = { role: 'bot', note: 'I couldn’t find any readable text in that image. Try a clearer screenshot, or type your question.' };
    } else {
      reply = {
        role: 'bot',
        note: `Got your ${files.length === 1 ? 'file' : files.length + ' files'}. I can read text from images, but other files aren’t supported yet.`
      };
    }
    this.push(typingMsg, reply);
  }

  private fallbackReply(text: string, imageText?: string, followups?: string[]): Msg {
    return {
      role: 'bot',
      fallback: true,
      q: text,
      imageText,
      followups: followups && followups.length ? followups : POPULAR.map((id) => QUESTIONS[id])
    };
  }

  /** Replace the typing indicator with the reply. */
  private push(typingMsg: Msg, reply: Msg): void {
    const i = this.messages.indexOf(typingMsg);
    if (i > -1) this.messages.splice(i, 1);
    this.messages.push(reply);
    this.pendingScroll = true;
  }

  /** OCR in the browser; tesseract.js fetches its engine and language data on first use. */
  private async readImages(images: Attachment[], typingMsg: Msg): Promise<string> {
    typingMsg.status = images.length > 1 ? 'Reading your images…' : 'Reading your image…';
    try {
      const { createWorker } = await import('tesseract.js');
      const worker = await createWorker('eng');
      try {
        const parts: string[] = [];
        for (const img of images) {
          const { data } = await worker.recognize(img.file!);
          parts.push(data.text);
        }
        return parts.join(' ').replace(/\s+/g, ' ').trim();
      } finally {
        await worker.terminate();
      }
    } catch {
      this.toast('Couldn’t read the image — check your internet connection');
      return '';
    }
  }

  send(): void {
    if (!this.input.trim() && !this.pending.length) return;
    if (this.pending.some((file) => file.url) && !this.input.trim()) {
      this.toast('Enter a question to send with your image.');
      return;
    }
    if (this.input.trim() && this.pending.some((file) => !file.url)) {
      this.toast('PDF and document attachments are not processed in chat queries; only images are sent.');
    }
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
    this.addFiles(input.files);
    input.value = '';
  }

  onDragOver(e: DragEvent): void {
    if (!e.dataTransfer?.types.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    this.dragging = true;
  }

  onDragLeave(e: DragEvent): void {
    // Ignore leave events fired when moving between child elements.
    const to = e.relatedTarget as Node | null;
    if (to && (e.currentTarget as HTMLElement).contains(to)) return;
    this.dragging = false;
  }

  onDrop(e: DragEvent): void {
    e.preventDefault();
    this.dragging = false;
    this.addFiles(e.dataTransfer?.files);
  }

  private addFiles(files: FileList | null | undefined): void {
    for (const f of Array.from(files || [])) {
      if (f.type.startsWith('image/') && this.pending.filter((item) => item.url).length >= MAX_IMAGES) {
        this.toast('You can attach up to 3 images per question.');
        continue;
      }
      if (f.size > MAX_FILE_BYTES) {
        this.toast(`${f.name} is over 10 MB`);
        continue;
      }
      this.pending.push({
        name: f.name,
        url: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined,
        file: f
      });
    }
  }

  removePending(i: number): void {
    const [f] = this.pending.splice(i, 1);
    if (f?.url) URL.revokeObjectURL(f.url);
  }

  // --- Read aloud ----------------------------------------------------------

  private stripHtml(html: string): string {
    const tmp = document.createElement('div');
    // Break after block elements and bullet list items so they aren't run together.
    tmp.innerHTML = html.replace(/<li>/g, '$&• ').replace(/<\/(p|li)>/g, '$&\n');
    return (tmp.textContent || '').trim();
  }

  private plainText(m: Msg): string {
    if (m.fallback) return FALLBACK_PARAS.join(' ');
    if (m.note) return m.note;
    return this.stripHtml(m.entry?.answer || '');
  }

  private sourceLine(s: Source, i: number): string {
    return `${i + 1}. ${s.title} — ${s.section} (updated ${s.updated})`;
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

  /** Copies the answer plus its sources, ready to paste into Teams or email. */
  copy(m: Msg): void {
    let txt = this.plainText(m);
    const sources = m.entry?.sources || [];
    if (sources.length) txt += '\n\nSources:\n' + sources.map((s, i) => this.sourceLine(s, i)).join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(
        () => this.toast('Answer copied with sources'),
        () => this.toast('Couldn’t copy — your browser blocked clipboard access')
      );
    } else {
      this.toast('Couldn’t copy — your browser blocked clipboard access');
    }
  }

  /** Saves the question, answer and sources as a PDF. */
  async downloadPdf(m: Msg): Promise<void> {
    if (!m.entry) return;
    const entry = m.entry;
    try {
      const { jsPDF } = await import('jspdf');
      const doc = new jsPDF({ unit: 'pt', format: 'a4' });
      const margin = 56;
      const width = doc.internal.pageSize.getWidth() - margin * 2;
      const pageH = doc.internal.pageSize.getHeight();
      const INK: [number, number, number] = [26, 38, 44];
      const SOFT: [number, number, number] = [87, 112, 123];
      const BRAND: [number, number, number] = [12, 108, 120];
      let y = margin;

      // jsPDF's built-in fonts only cover Latin-1, so swap typographic characters.
      const safe = (t: string) =>
        t.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, '-').replace(/→/g, '->')
          .replace(/•/g, '-').replace(/…/g, '...').replace(/[^\x00-\xFF]/g, '');

      const write = (text: string, size: number, style: 'normal' | 'bold', color: [number, number, number], gapAfter: number) => {
        doc.setFont('helvetica', style);
        doc.setFontSize(size);
        doc.setTextColor(color[0], color[1], color[2]);
        const lineH = size * 1.4;
        for (const line of doc.splitTextToSize(safe(text), width) as string[]) {
          if (y + lineH > pageH - margin) {
            doc.addPage();
            y = margin;
          }
          doc.text(line, margin, y + size);
          y += lineH;
        }
        y += gapAfter;
      };

      write('WorkLens answer', 18, 'bold', BRAND, 2);
      write(new Date().toLocaleString(), 9, 'normal', SOFT, 18);
      if (m.q) {
        write('QUESTION', 9, 'bold', SOFT, 2);
        write(m.q, 12, 'normal', INK, 16);
      }
      write('ANSWER', 9, 'bold', SOFT, 2);
      for (const para of this.plainText(m).split('\n').filter((p) => p.trim())) write(para, 11.5, 'normal', INK, 4);
      y += 12;
      write('SOURCES', 9, 'bold', SOFT, 4);
      entry.sources.forEach((s, i) => {
        write(this.sourceLine(s, i), 11, 'bold', INK, 2);
        write(this.stripHtml(s.passage), 10, 'normal', SOFT, 10);
      });

      const name = (m.q || entry.id).replace(/[^\w\s-]/g, '').trim().slice(0, 60) || 'answer';
      doc.save(`WorkLens - ${name}.pdf`);
      this.toast('PDF downloaded');
    } catch {
      this.toast('Couldn’t create the PDF');
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