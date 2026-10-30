import { Component, ElementRef, ViewChild, AfterViewChecked, OnDestroy, NgZone, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
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
  /** True while the reply is being typed out letter by letter. */
  streaming?: boolean;
  /** HTML revealed so far while streaming. */
  typed?: string;
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
const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
/** Typing animation: one tick every TYPE_TICK_MS, finishing long answers in roughly TYPE_MAX_MS. */
const TYPE_TICK_MS = 16;
const TYPE_MAX_MS = 4000;

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
  sourceDocumentUrl: string | null = null;
  sourceDocumentLoading = false;
  toastMsg = '';
  toastShow = false;

  selectedVoice: 'amy' | 'ryan' = (localStorage.getItem('wl-voice') as 'amy' | 'ryan') || 'amy';
  private currentAudio: HTMLAudioElement | null = null;
  private currentAudioUrl: string | null = null;

  private toastT: any;
  private pendingScroll = false;
  /** Keep the newest reply in view as it types, but never scroll past its first line. */
  private followReply = false;
  /** Last scroll position set while following; if the reader scrolls up from it, stop following. */
  private autoTop = -1;
  private typingMsg: Msg | null = null;
  private typingTimer: any;

  private http = inject(HttpClient);
  private sanitizer = inject(DomSanitizer);
  private backendUrl = environment.apiUrl || 'http://127.0.0.1:8000';
  private apiUrl = this.backendUrl + '/query';
  private ttsUrl = (environment.apiUrl || 'http://127.0.0.1:8000') + '/audio/tts';
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
  }

  ngAfterViewChecked(): void {
    if (this.pendingScroll && this.stream) {
      const el = this.stream.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.pendingScroll = false;
    }
    if (this.followReply && this.stream) {
      this.followReply = false;
      const el = this.stream.nativeElement;
      const replies = el.querySelectorAll<HTMLElement>('.msg.bot');
      const last = replies[replies.length - 1];
      const readerScrolledUp = this.autoTop >= 0 && el.scrollTop < this.autoTop - 4;
      if (last && !readerScrolledUp) {
        const replyTop = el.scrollTop + last.getBoundingClientRect().top - el.getBoundingClientRect().top - 16;
        const target = Math.min(replyTop, el.scrollHeight - el.clientHeight);
        if (target > el.scrollTop) el.scrollTop = target;
        this.autoTop = el.scrollTop;
      }
    }
  }

  ngOnDestroy(): void {
    this.stopSpeaking();
    this.finishTyping();
    this.clearSourceDocumentUrl();
  }

  private clearSourceDocumentUrl(): void {
    if (this.sourceDocumentUrl) {
      URL.revokeObjectURL(this.sourceDocumentUrl.split('#')[0]);
      this.sourceDocumentUrl = null;
    }
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
    this.finishTyping();
    this.railOpen = false;
    this.user = null;
    try {
      localStorage.removeItem(USER_KEY);
    } catch {}
  }

  private loadUser(): User | null {
    try {
      const u = JSON.parse(localStorage.getItem(USER_KEY) || 'null');
      return u && u.token ? u : null;
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

      const headers = new HttpHeaders({
        Authorization: `Bearer ${this.user?.token || ''}`
      });

      this.http.post<{
        answer: string;
        sources: string[];
        source_refs?: Array<{
          source?: string;
          chunk_index?: number | null;
          page_number?: number | null;
          line_start?: number | null;
          line_end?: number | null;
          text: string;
        }>;
        suggested_questions?: string[];
        is_greeting?: boolean;
      }>(this.apiUrl, body, { headers }).subscribe({
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
                section: this.sourceLocation(source.page_number, source.line_start, source.line_end)
                  || (source.chunk_index == null ? 'Knowledge Base' : `Chunk ${source.chunk_index + 1}`),
                updated: '2026',
                fresh: true,
                pageNumber: source.page_number ?? undefined,
                documentName: source.source || undefined,
                lineStart: source.line_start ?? undefined,
                lineEnd: source.line_end ?? undefined,
                passage: `<mark>${escapeHtml(source.text).replace(/\n/g, '<br/>')}</mark>`
              }))
            : (res.sources || []).map((doc, idx) => ({
                title: `Document Chunk ${idx + 1}`,
                section: 'Knowledge Base',
                updated: '2026',
                fresh: true,
                passage: `<mark>${escapeHtml(doc).replace(/\n/g, '<br/>')}</mark>`
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
          if (err.status === 401) {
            this.logout();
            this.toast('Session expired. Please sign in again.');
            return;
          }
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

  /** Replace the typing indicator with the reply, then type the reply out. */
  private push(typingMsg: Msg, reply: Msg): void {
    const i = this.messages.indexOf(typingMsg);
    if (i > -1) this.messages.splice(i, 1);
    this.messages.push(reply);
    this.autoTop = -1;
    this.followReply = true;
    this.startTyping(reply);
  }

  // --- Voice & Read aloud --------------------------------------------------

  toggleVoice(): void {
    this.stopSpeaking();
    this.selectedVoice = this.selectedVoice === 'amy' ? 'ryan' : 'amy';
    localStorage.setItem('wl-voice', this.selectedVoice);
    this.toast(`Voice switched to ${this.selectedVoice === 'amy' ? 'Amy' : 'Ryan'}`);
  }

  speak(m: Msg): void {
    if (this.speaking === m) {
      this.stopSpeaking();
      return;
    }

    this.stopSpeaking();
    this.speaking = m;

    const textToRead = this.plainText(m);
    const headers = new HttpHeaders({
      Authorization: `Bearer ${this.user?.token || ''}`
    });

    this.http.post(this.ttsUrl, { text: textToRead, voice: this.selectedVoice }, {
      headers,
      responseType: 'blob'
    }).subscribe({
      next: (blob) => {
        // Cancelled or switched messages while waiting for network
        if (this.speaking !== m) return;

        this.currentAudioUrl = URL.createObjectURL(blob);
        const audio = new Audio(this.currentAudioUrl);
        this.currentAudio = audio;

        audio.onended = () => {
          this.zone.run(() => this.stopSpeaking());
        };
        audio.onerror = () => {
          this.zone.run(() => {
            // Audio decode error -> fall back to browser speech
            this.speakWithBrowser(textToRead, m);
          });
        };

        audio.play().catch(() => {
          // Autoplay blocked -> fall back to browser speech
          this.speakWithBrowser(textToRead, m);
        });
      },
      error: () => {
        // Backend failure (e.g. 503, timeout, network error) -> Fall back to browser speech
        if (this.speaking !== m) return;
        this.speakWithBrowser(textToRead, m);
      }
    });
  }

  /** Browser-native SpeechSynthesis fallback */
  private speakWithBrowser(text: string, m: Msg): void {
    console.log("speakWithBrowser");
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      this.toast('Speech synthesis is not supported on this device.');
      this.stopSpeaking();
      return;
    }

    // Stop any existing audio or speech
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US';

    // Pick an appropriate client-side voice matching the selection
    const voices = window.speechSynthesis.getVoices();
    if (voices.length) {
      const isAmy = this.selectedVoice === 'amy';
      const targetGenderKeywords = isAmy
        ? ['female', 'samantha', 'zira', 'karen', 'victoria', 'amy']
        : ['male', 'david', 'george', 'ryan', 'alex', 'guy'];

      const matchedVoice = voices.find((v) => {
        const name = v.name.toLowerCase();
        return v.lang.startsWith('en') && targetGenderKeywords.some((kw) => name.includes(kw));
      }) || voices.find((v) => v.lang.startsWith('en'));

      if (matchedVoice) {
        utterance.voice = matchedVoice;
      }
    }

    utterance.onstart = () => {
      console.log('SpeechSynthesis: started');
    };

    utterance.onend = (e) => {
      console.log('SpeechSynthesis: ended', e);
      this.zone.run(() => this.stopSpeaking());
    };

    utterance.onerror = (e) => {
      console.error('SpeechSynthesis error:', e.error, e);
      this.toast(`Browser speech failed: ${e.error}`);
      this.zone.run(() => this.stopSpeaking());
    };

    this.speaking = m;
    window.speechSynthesis.speak(utterance);
  }

  private stopSpeaking(): void {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    if (this.currentAudioUrl) {
      URL.revokeObjectURL(this.currentAudioUrl);
      this.currentAudioUrl = null;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    this.speaking = null;
  }

  // --- Typing animation ----------------------------------------------------

  private replyHtml(m: Msg): string {
    if (m.entry) return m.entry.answer;
    if (m.fallback) return FALLBACK_PARAS.map((p) => `<p>${escapeHtml(p)}</p>`).join('');
    if (m.note) return `<p>${escapeHtml(m.note)}</p>`;
    return '';
  }

  /** Reveals the reply a few letters at a time; sources and actions appear once it finishes. */
  private startTyping(m: Msg): void {
    this.finishTyping();
    const html = this.replyHtml(m);
    const total = visibleLength(html);
    const reduceMotion = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!total || reduceMotion) return;

    const perTick = Math.max(1, Math.ceil(total / (TYPE_MAX_MS / TYPE_TICK_MS)));
    let shown = 0;
    m.streaming = true;
    m.typed = '';
    this.typingMsg = m;
    this.typingTimer = setInterval(() => {
      shown += perTick;
      if (shown >= total) {
        this.finishTyping();
      } else {
        m.typed = sliceHtml(html, shown) + '<span class="caret"></span>';
      }
      this.followReply = true;
    }, TYPE_TICK_MS);
  }

  private finishTyping(): void {
    clearInterval(this.typingTimer);
    if (this.typingMsg) {
      this.typingMsg.streaming = false;
      this.typingMsg.typed = undefined;
      this.typingMsg = null;
    }
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
    if (this.pending.length && !this.input.trim()) {
      this.toast('Enter a question to send with your image.');
      return;
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
      if (!IMAGE_TYPES.includes(f.type)) {
        this.toast('Only images can be attached (JPG, PNG or WebP).');
        continue;
      }
      if (this.pending.length >= MAX_IMAGES) {
        this.toast('You can attach up to 3 images per question.');
        break;
      }
      if (f.size > MAX_FILE_BYTES) {
        this.toast(`${f.name} is over 10 MB`);
        continue;
      }
      this.pending.push({ name: f.name, url: URL.createObjectURL(f), file: f });
    }
  }

  removePending(i: number): void {
    const [f] = this.pending.splice(i, 1);
    if (f?.url) URL.revokeObjectURL(f.url);
  }

  // --- Helpers -------------------------------------------------------------

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

  private sourceLocation(
    pageNumber?: number | null,
    lineStart?: number | null,
    lineEnd?: number | null
  ): string {
    const location: string[] = [];
    if (pageNumber != null) location.push(`Page ${pageNumber}`);
    if (lineStart != null) {
      location.push(
        lineEnd != null && lineEnd !== lineStart
          ? `Lines ${lineStart}-${lineEnd}`
          : `Line ${lineStart}`
      );
    }
    return location.join(' · ');
  }

  openDoc(s: Source): void {
    this.selectedSource = s;
    this.clearSourceDocumentUrl();
    this.sourceDocumentLoading = false;

    if (!s.documentName) return;

    const documentName = s.documentName.trim();
    const extension = documentName.split('.').pop()?.toLowerCase();
    if (!documentName || extension !== 'pdf') return;

    this.sourceDocumentLoading = true;
    const headers = new HttpHeaders({
      Authorization: `Bearer ${this.user?.token || ''}`
    });
    const url = `${this.backendUrl}/documents/${encodeURIComponent(documentName)}`;
    this.http.get(url, { headers, responseType: 'blob' }).subscribe({
      next: (blob) => {
        if (this.selectedSource !== s) return;
        const objectUrl = URL.createObjectURL(blob);
        this.sourceDocumentUrl = s.pageNumber
          ? `${objectUrl}#page=${s.pageNumber}`
          : objectUrl;
        this.sourceDocumentLoading = false;
      },
      error: () => {
        if (this.selectedSource !== s) return;
        this.sourceDocumentLoading = false;
        this.toastMsg = 'Unable to open the source document.';
        this.toastShow = true;
      }
    });
  }

  closeDoc(): void {
    this.selectedSource = null;
    this.sourceDocumentLoading = false;
    this.clearSourceDocumentUrl();
  }

  safeSourceDocumentUrl(): SafeResourceUrl | null {
    return this.sourceDocumentUrl ? this.sanitizer.bypassSecurityTrustResourceUrl(this.sourceDocumentUrl) : null;
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

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Walks HTML counting visible characters; tags count as 0 and entities as 1. Returns the index after `limit` characters. */
function walkHtml(html: string, limit: number): { index: number; seen: number } {
  let i = 0;
  let seen = 0;
  while (i < html.length && seen < limit) {
    if (html[i] === '<') {
      const end = html.indexOf('>', i);
      i = end < 0 ? html.length : end + 1;
    } else if (html[i] === '&') {
      const end = html.indexOf(';', i);
      i = end > -1 && end - i < 10 ? end + 1 : i + 1;
      seen++;
    } else {
      i++;
      seen++;
    }
  }
  return { index: i, seen };
}

function visibleLength(html: string): number {
  return walkHtml(html, Infinity).seen;
}

/** The first `n` visible characters of `html`, never cutting a tag or entity in half. */
function sliceHtml(html: string, n: number): string {
  return html.slice(0, walkHtml(html, n).index);
}