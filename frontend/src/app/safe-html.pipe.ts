import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

/**
 * Parses markdown into HTML and sanitizes the output with DOMPurify
 * so that formatting tags like <strong>, <em>, <ul>, <ol>, <code>, and <mark>
 * are rendered safely without exposing the app to XSS vulnerabilities.
 */
@Pipe({ name: 'safeHtml', standalone: true })
export class SafeHtmlPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {
    marked.setOptions({
      gfm: true,
      breaks: true
    });
  }

  transform(value: string): SafeHtml {
    if (!value) return '';

    // 1. Convert Markdown to HTML
    const rawHtml = marked.parse(value, { async: false }) as string;

    // 2. Sanitize to strip scripts, event handlers, and malicious tags
    const cleanHtml = DOMPurify.sanitize(rawHtml, {
      ADD_TAGS: ['mark'],
      ADD_ATTR: ['target', 'rel']
    });

    // 3. Mark the sanitized string as trusted for Angular
    return this.sanitizer.bypassSecurityTrustHtml(cleanHtml);
  }
}
