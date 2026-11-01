import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.use({ gfm: true, breaks: true });

const PURIFY_OPTIONS = {
  ADD_TAGS: ['mark'],
  ADD_ATTR: ['target', 'rel']
};

// Links in answers open in a new tab so the chat isn't lost.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && node.getAttribute('href')) {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

/** Markdown -> sanitized HTML. Safe to insert into the page. */
export function renderMarkdown(value: string): string {
  if (!value) return '';
  const rawHtml = marked.parse(value, { async: false }) as string;
  return DOMPurify.sanitize(rawHtml, PURIFY_OPTIONS) as string;
}

/**
 * Parses markdown into HTML and sanitizes the output with DOMPurify
 * so that formatting tags like <strong>, <em>, <ul>, <ol>, <code>, and <mark>
 * are rendered safely without exposing the app to XSS vulnerabilities.
 */
@Pipe({ name: 'safeHtml', standalone: true })
export class SafeHtmlPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string): SafeHtml {
    return value ? this.sanitizer.bypassSecurityTrustHtml(renderMarkdown(value)) : '';
  }
}

/** Sanitizes HTML that is already rendered (e.g. a reply mid-typing) without parsing it as markdown again. */
@Pipe({ name: 'cleanHtml', standalone: true })
export class CleanHtmlPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string | undefined): SafeHtml {
    return value ? this.sanitizer.bypassSecurityTrustHtml(DOMPurify.sanitize(value, PURIFY_OPTIONS) as string) : '';
  }
}
