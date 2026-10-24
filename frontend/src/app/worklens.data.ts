// ---------------------------------------------------------------------------
// WorkLens sample knowledge base for the opening example and optional mock mode.
// ---------------------------------------------------------------------------

export interface Source {
  title?: string;
  section?: string;
  updated?: string;
  fresh?: boolean;
  passage: string; // HTML; <mark> wraps the exact cited span
  chunkId?: string;
}

export interface KBEntry {
  id: string;
  cat: string;
  kw: string[];
  conf: 'high' | 'medium';
  answer: string; // HTML
  sources: Source[];
}

export const KB: KBEntry[] = [
  {
    id: 'leavebal', cat: 'HR', conf: 'high',
    kw: ['leave balance', 'how many leave', 'leaves left', 'leave remaining', 'remaining leave', 'leave left', 'balance'],
    answer: '<p>You have <strong>12 earned leave</strong> and <strong>5 casual leave</strong> days remaining for 2026.</p><p>Earned leave carries over into next year (up to 30 days). Casual leave resets every January and can’t be carried forward.</p>',
    sources: [
      { title: 'Leave Policy 2026', section: '§3.1 Entitlement', updated: 'Jan 2026', fresh: true,
        passage: 'Confirmed employees accrue <mark>1.5 days of earned leave per month</mark> (18 days per year), which may be carried forward up to a maximum of 30 days. <mark>Casual leave is capped at 6 days per calendar year</mark> and does not carry over to the following year.' },
      { title: 'HRMS — My Leave', section: 'Live balance', updated: 'today', fresh: true,
        passage: 'Earned leave available: <mark>12.0 days</mark> · Casual leave available: <mark>5.0 days</mark> · Pending requests: 0 · Last updated from payroll run on 10 Oct 2026.' }
    ]
  },
  {
    id: 'applyleave', cat: 'HR', conf: 'high',
    kw: ['apply for leave', 'apply leave', 'request leave', 'take leave', 'applying for leave', 'book leave', 'how do i apply'],
    answer: '<p>To apply for leave:</p><ul><li>Open the <strong>HRMS portal</strong> → <code>Leave → New Request</code></li><li>Pick the leave type and dates, add a reason, and submit</li><li>It routes to your reporting manager for approval — you’ll get an email once actioned</li></ul><p>Apply at least <strong>3 working days</strong> in advance for planned leave. Sick leave can be applied retroactively within 2 days.</p>',
    sources: [
      { title: 'Leave Policy 2026', section: '§4.2 Applying for leave', updated: 'Jan 2026', fresh: true,
        passage: 'All leave must be requested through the HRMS portal. <mark>Planned leave should be submitted at least three working days in advance</mark> and is subject to manager approval. Sick leave may be regularised within two working days of returning to work.' }
    ]
  },
  {
    id: 'password', cat: 'IT', conf: 'high',
    kw: ['password', 'reset password', 'locked out', 'lock out', "can't log in", 'cant login', 'forgot password', 'unlock account', 'log in'],
    answer: '<p>You can reset it yourself in about a minute:</p><ul><li>Go to <code>reset.company.internal</code> (works without being signed in)</li><li>Verify with the OTP sent to your registered mobile</li><li>Set a new password — min 12 characters, one number, one symbol</li></ul><p>If you’re fully locked out or it’s urgent, call the <strong>IT Helpdesk at ext. 4040</strong> and they can unlock it over a verified call.</p>',
    sources: [
      { title: 'IT Helpdesk Guide', section: '§1 Account access', updated: 'Mar 2026', fresh: true,
        passage: 'Users can reset their own password via the self-service portal at any time. <mark>Passwords must be at least 12 characters</mark> and include a number and a symbol. For a locked account, <mark>contact the IT Helpdesk on extension 4040</mark>; identity is verified before any manual unlock.' }
    ]
  },
  {
    id: 'vpn', cat: 'IT', conf: 'high',
    kw: ['vpn', 'remote access', 'connect from home', 'work from home access', 'access from home'],
    answer: '<p>Use the <strong>company VPN client</strong> (pre-installed on managed laptops):</p><ul><li>Open the VPN app → sign in with your company credentials</li><li>Approve the MFA prompt on your phone</li><li>Select the <code>IN-Bengaluru</code> gateway and connect</li></ul><p>On a personal device, you’ll need to enrol it in MDM first — raise an IT ticket for that.</p>',
    sources: [
      { title: 'Remote Access Guide', section: '§2 VPN setup', updated: 'Feb 2026', fresh: true,
        passage: 'Managed laptops ship with the VPN client pre-installed. <mark>Multi-factor authentication is mandatory</mark> for all remote connections. Personal devices must be enrolled in mobile device management (MDM) before VPN access is granted.' }
    ]
  },
  {
    id: 'reimburse', cat: 'Finance', conf: 'high',
    kw: ['reimburse', 'reimbursement', 'claim expense', 'expense claim', 'internet reimbursement', 'broadband', 'submit expense', 'claim'],
    answer: '<p>Submit reimbursements through <code>HRMS → Expenses → New Claim</code>:</p><ul><li>Attach the invoice/receipt (PDF or image)</li><li>Pick the category — e.g. Internet, Medical, Travel</li><li>Submit before the <strong>5th of the following month</strong> to be included in that cycle</li></ul><p>Internet is reimbursed up to <strong>₹1,500/month</strong> for eligible roles.</p>',
    sources: [
      { title: 'Expense & Reimbursement Policy', section: '§5 Internet & home office', updated: 'Jan 2026', fresh: true,
        passage: 'Eligible employees may claim home internet costs <mark>up to ₹1,500 per month</mark> against a valid invoice. Claims must be submitted via HRMS <mark>by the 5th of the following month</mark>; late claims roll into the next payroll cycle.' }
    ]
  },
  {
    id: 'insurance', cat: 'HR', conf: 'high',
    kw: ['insurance', 'health cover', 'medical cover', 'mediclaim', 'health insurance', 'add dependent', 'dependent', 'cover'],
    answer: '<p>You’re covered under the company <strong>group health policy</strong> — ₹5 lakh floater for you, spouse and up to 2 children.</p><ul><li>Add or update dependents within <strong>30 days of joining</strong> or a life event (marriage, newborn)</li><li>Do it via <code>HRMS → Benefits → Insurance</code></li></ul>',
    sources: [
      { title: 'Benefits Handbook', section: '§2 Group health insurance', updated: 'Jan 2026', fresh: true,
        passage: 'The group policy provides a <mark>₹5,00,000 family floater</mark> covering the employee, spouse and up to two dependent children. <mark>Dependents must be added within 30 days</mark> of joining or a qualifying life event.' }
    ]
  },
  {
    id: 'onboard', cat: 'HR', conf: 'high',
    kw: ['first day', 'new joiner', 'onboarding', 'just joined', 'getting started', 'induction', 'new hire', 'day one'],
    answer: '<p>Welcome aboard! Your first-day checklist:</p><ul><li>Collect your ID card and access badge from Reception (Level 3)</li><li>Your laptop is pre-configured — sign in with the temp credentials emailed to you</li><li>Complete <strong>Day-1 mandatory trainings</strong> in the LMS (security + code of conduct)</li><li>Your manager will set up a welcome 1:1</li></ul>',
    sources: [
      { title: 'Onboarding Checklist', section: 'Week 1', updated: 'Apr 2026', fresh: true,
        passage: 'New joiners should <mark>collect their access badge from Reception on Level 3</mark> and complete the <mark>Day-1 mandatory trainings</mark> (information security and code of conduct) within the first 48 hours.' }
    ]
  },
  {
    id: 'notice', cat: 'HR', conf: 'medium',
    kw: ['notice period', 'resign', 'resignation', 'serving notice', 'leaving the company'],
    answer: '<p>The standard notice period is <strong>60 days</strong> for confirmed employees and <strong>30 days</strong> during probation.</p><p>Notice can sometimes be partially bought out with manager and HR approval — the exact terms depend on your offer letter, so please confirm with HR for your case.</p>',
    sources: [
      { title: 'Employee Handbook', section: '§9 Separation', updated: 'Nov 2025', fresh: true,
        passage: 'Confirmed employees are required to serve a <mark>notice period of 60 days</mark>. During probation the notice period is 30 days. Buy-out of notice is at the discretion of management and HR.' }
    ]
  },
  {
    id: 'room', cat: 'Facilities', conf: 'medium',
    kw: ['meeting room', 'book room', 'conference room', 'desk booking', 'book a desk', 'room booking'],
    answer: '<p>Book rooms and desks in the <strong>Workplace app</strong> (or the kiosk outside each floor):</p><ul><li>Pick floor → room → time slot</li><li>Rooms auto-release if no one checks in within 10 minutes</li></ul>',
    sources: [
      { title: 'Facilities Guide', section: '§3 Room booking', updated: 'Jul 2024', fresh: false,
        passage: 'Meeting rooms are reserved through the Workplace app. <mark>Unclaimed rooms are released automatically after 10 minutes</mark> to free up space for others.' }
    ]
  },
  {
    id: 'appraisal', cat: 'HR', conf: 'medium',
    kw: ['appraisal', 'performance review', 'promotion', 'increment', 'review cycle', 'rating', 'hike'],
    answer: '<p>The appraisal cycle runs <strong>twice a year</strong> — the main review in <strong>March</strong> and a mid-year check-in in September.</p><ul><li>Self-assessment opens ~3 weeks before the cycle</li><li>Manager reviews, then calibration, then outcomes shared</li><li>Increments from the March cycle take effect in April</li></ul>',
    sources: [
      { title: 'Performance Management Guide', section: '§1 Cycle', updated: 'Feb 2026', fresh: true,
        passage: 'Performance is reviewed <mark>twice a year</mark>, with the primary appraisal in March and a lighter mid-year check-in in September. <mark>Revised compensation from the March cycle is effective 1 April.</mark>' }
    ]
  },
  {
    id: 'device', cat: 'IT', conf: 'high',
    kw: ['device', 'laptop', 'personal device', 'company laptop', 'byod', 'install software', 'broken laptop'],
    answer: '<p>Company-issued laptops are the default for all work. A few key rules:</p><ul><li>Keep work on the <strong>managed company device</strong>; personal devices need MDM enrolment for any access</li><li>To install software, raise a request in the <code>IT Service Desk</code> — approvals are usually same-day for whitelisted apps</li><li>For a broken or faulty laptop, log a hardware ticket; a replacement/loaner is issued within 1 business day</li></ul>',
    sources: [
      { title: 'IT Device & Acceptable Use Policy', section: '§2 Devices', updated: 'Mar 2026', fresh: true,
        passage: 'Company work must be performed on <mark>managed company-issued devices</mark>. Software installation requires approval through the IT Service Desk. <mark>Faulty hardware is replaced within one business day</mark> via a loaner device where needed.' }
    ]
  },
  {
    id: 'manager', cat: 'HR', conf: 'high',
    kw: ['my manager', 'who is my manager', 'reporting manager', 'who do i report', 'skip level'],
    answer: '<p>Your reporting manager is <strong>Priya Nair</strong> — Engineering Manager, Platform.</p><p>Your skip-level is <strong>Arjun Rao</strong>, Director of Engineering. You can find the full reporting line on your HRMS profile.</p>',
    sources: [
      { title: 'HRMS — Org Directory', section: 'Your reporting line', updated: 'today', fresh: true,
        passage: 'Meera Shah → reports to <mark>Priya Nair (Engineering Manager, Platform)</mark> → reports to <mark>Arjun Rao (Director, Engineering)</mark>. Team: Platform · Location: Bengaluru.' }
    ]
  },
  {
    id: 'approvals', cat: 'HR', conf: 'high',
    kw: ['pending approval', 'my approvals', 'awaiting approval', 'pending request'],
    answer: '<p>You have <strong>no pending approvals</strong> right now — you’re all caught up.</p><p>Your most recent request (internet reimbursement for Sep 2026) was <strong>approved on 28 Sep</strong>.</p>',
    sources: [
      { title: 'HRMS — My Requests', section: 'Status', updated: 'today', fresh: true,
        passage: 'Open requests: <mark>0</mark>. Recently closed: Internet reimbursement (Sep 2026) — <mark>Approved 28 Sep 2026</mark> by P. Nair.' }
    ]
  }
];

/** One canonical question per KB entry; each matches its own entry's keywords. */
export const QUESTIONS: Record<string, string> = {
  leavebal: 'How many leave days do I have left?',
  applyleave: 'How do I apply for leave?',
  password: 'How do I reset my password?',
  vpn: 'How do I connect to the VPN?',
  reimburse: 'How do I claim internet reimbursement?',
  insurance: 'What does my health insurance cover?',
  onboard: 'What should I do on my first day?',
  notice: 'What is my notice period?',
  room: 'How do I book a meeting room?',
  appraisal: 'When is the appraisal cycle?',
  device: 'Can I use my personal device for work?',
  manager: 'Who is my manager?',
  approvals: 'Do I have any pending approvals?'
};

/** Follow-up questions offered after each answer, by KB entry id. */
export const RELATED: Record<string, string[]> = {
  leavebal: ['applyleave', 'approvals', 'notice'],
  applyleave: ['leavebal', 'approvals', 'manager'],
  password: ['vpn', 'device'],
  vpn: ['password', 'device'],
  reimburse: ['approvals', 'insurance'],
  insurance: ['reimburse', 'leavebal'],
  onboard: ['device', 'manager', 'insurance'],
  notice: ['leavebal', 'appraisal'],
  room: ['device', 'onboard'],
  appraisal: ['manager', 'notice'],
  device: ['vpn', 'password'],
  manager: ['appraisal', 'approvals'],
  approvals: ['reimburse', 'leavebal']
};

/** Offered when nothing in the knowledge base matches. */
export const POPULAR = ['leavebal', 'password', 'insurance'];
