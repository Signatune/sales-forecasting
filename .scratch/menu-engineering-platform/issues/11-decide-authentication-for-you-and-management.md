# Decide authentication for the app

Type: grilling
Status: open
Blocked by: 10

## Question

How do Mike and a handful of management people sign in, and what does the app do
about who they are?

The audience is settled: **Mike plus management** — a few named people who read
the analysis and act on it. Not staff-facing, not public. That sets the bar:
real auth, but no role hierarchy to design.

Decide:

- **The mechanism.** Supabase Auth is already present in the stack and pairs
  naturally with an RLS-based access path; Google SSO matches an org that
  already lives in Google Workspace (Drive and Chat are on the report path
  today). NextAuth/Auth.js is a third option that decouples auth from Supabase.
  The right answer depends heavily on what ticket 10 chose — if the app talks to
  Postgres directly, Supabase Auth's main advantage evaporates.
- **Who is allowed in.** An allowlist of addresses, a Workspace domain
  restriction, or an invite flow. With this few users, the simplest thing that
  cannot accidentally admit a stranger wins.
- **Whether the app needs roles at all.** Probably not today — but decide
  explicitly rather than by omission, since retrofitting roles once the data
  access path is built is far more expensive than leaving a seam.
- **Session handling and what happens on expiry**, given the app shows a
  business's cost and margin data.

Keep this proportionate: a few trusted users reading internal numbers. The
failure mode to avoid is an auth design so elaborate it delays the app, or so
thin that the margin data is one guessed URL away.
