import { Component, EventEmitter, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

export interface User {
  name: string;
  email: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Demo sign-in screen. There is no auth backend yet, so any valid email and password is accepted. */
@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="login">
      <form class="login-card" (ngSubmit)="submit()" novalidate>
        <div class="brand login-brand">
          <svg class="lens" viewBox="0 0 32 32" fill="none" aria-hidden="true">
            <circle cx="16" cy="16" r="13" stroke="var(--brand)" stroke-width="2.3" />
            <circle cx="16" cy="16" r="6.4" fill="var(--brand)" />
            <circle cx="13.4" cy="13.4" r="1.9" fill="#fff" opacity=".9" />
            <path d="M25.2 25.2 L30 30" stroke="var(--brand)" stroke-width="2.6" stroke-linecap="round" />
          </svg>
          <div class="brand-name">Work<span>Lens</span></div>
        </div>
        <h1>Sign in</h1>
        <p class="login-sub">Use your work account to access the knowledge assistant.</p>

        <label for="wl-email">Work email</label>
        <input id="wl-email" name="email" type="email" autocomplete="username" [(ngModel)]="email"
          placeholder="name@company.com" [class.invalid]="error && !emailValid" />

        <label for="wl-password">Password</label>
        <div class="pw">
          <input id="wl-password" name="password" [type]="showPw ? 'text' : 'password'" autocomplete="current-password"
            [(ngModel)]="password" [class.invalid]="error && !password" />
          <button type="button" class="pw-toggle" (click)="showPw = !showPw">{{ showPw ? 'Hide' : 'Show' }}</button>
        </div>

        <div class="login-error" *ngIf="error" role="alert">{{ error }}</div>

        <button class="login-btn" type="submit" [disabled]="busy">{{ busy ? 'Signing in…' : 'Sign in' }}</button>
        <div class="login-hint">Demo · any work email and password will sign you in</div>
      </form>
    </div>
  `
})
export class LoginComponent {
  @Output() loggedIn = new EventEmitter<User>();

  email = '';
  password = '';
  showPw = false;
  busy = false;
  error = '';

  get emailValid(): boolean {
    return EMAIL_RE.test(this.email.trim());
  }

  submit(): void {
    if (!this.emailValid) {
      this.error = 'Enter a valid work email.';
      return;
    }
    if (!this.password) {
      this.error = 'Enter your password.';
      return;
    }
    this.error = '';
    this.busy = true;
    const email = this.email.trim().toLowerCase();
    // Brief delay so the sign-in feels like a real request.
    setTimeout(() => this.loggedIn.emit({ email, name: nameFromEmail(email) }), 400);
  }
}

/** "meera.shetty@acme.com" -> "Meera Shetty" */
function nameFromEmail(email: string): string {
  return email
    .split('@')[0]
    .split(/[._-]+/)
    .filter(Boolean)
    .map((p) => p[0].toUpperCase() + p.slice(1))
    .join(' ');
}
