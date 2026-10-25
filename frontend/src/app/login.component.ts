import { Component, EventEmitter, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { environment } from '../environments/environment';

export interface User {
  id?: number;
  name: string;
  email: string;
  role: 'HR' | 'EMPLOYEE';
  token?: string;
}

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
        <input id="wl-email" name="email" type="text" autocomplete="username" [(ngModel)]="email"
          placeholder="name@company.com" [class.invalid]="error && !email.trim()" />

        <label for="wl-password">Password</label>
        <div class="pw">
          <input id="wl-password" name="password" [type]="showPw ? 'text' : 'password'" autocomplete="current-password"
            [(ngModel)]="password" [class.invalid]="error && !password" />
          <button type="button" class="pw-toggle" (click)="showPw = !showPw">{{ showPw ? 'Hide' : 'Show' }}</button>
        </div>

        <div class="login-error" *ngIf="error" role="alert">{{ error }}</div>

        <button class="login-btn" type="submit" [disabled]="busy">{{ busy ? 'Signing in…' : 'Sign in' }}</button>
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

  private http = inject(HttpClient);
  private authUrl = (environment.apiUrl || 'http://127.0.0.1:8000') + '/auth/login';

  submit(): void {
    const emailVal = this.email.trim();
    if (!emailVal) {
      this.error = 'Enter your work email or identifier.';
      return;
    }
    if (!this.password) {
      this.error = 'Enter your password.';
      return;
    }

    this.error = '';
    this.busy = true;

    this.http.post<{ access_token: string; token_type: string; user: { id: number; email: string; role: 'HR' | 'EMPLOYEE' } }>(
      this.authUrl,
      { email: emailVal, password: this.password }
    ).subscribe({
      next: (res) => {
        this.busy = false;
        const loggedUser: User = {
          id: res.user.id,
          email: res.user.email,
          name: nameFromEmail(res.user.email),
          role: res.user.role,
          token: res.access_token
        };
        this.loggedIn.emit(loggedUser);
      },
      error: (err) => {
        this.busy = false;
        const detail = err.error?.detail;
        this.error = typeof detail === 'string' ? detail : 'Unable to sign in. Verify your credentials.';
      }
    });
  }
}

function nameFromEmail(email: string): string {
  const local = email.split('@')[0] || email;
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((p) => p[0].toUpperCase() + p.slice(1))
    .join(' ');
}
