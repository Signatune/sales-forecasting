# Decide the repo shape and hosting

Type: grilling
Status: open

## Question

Where does the Next.js app live, and where does it run?

Today this repo is Python: `pyproject.toml`, a `Makefile`, a `Dockerfile`,
`compose.yaml`, a pytest suite, and three scheduled GitHub Actions workflows
that are the production system. Adding a TypeScript app is the first time two
toolchains share the tree — or the first time they don't.

Decide:

1. **Same repo or separate.** Same repo keeps the schema, the migrations, and
   the app that reads them in one place, so a migration and the app change that
   depends on it land in one commit — worth a lot given how much of this effort
   is schema change. It also means CI has to run two toolchains, and the repo
   root gets a `package.json` next to `pyproject.toml`. A separate repo keeps
   each clean, at the cost of coordinating schema changes across two histories.

2. **If same repo, the layout.** A top-level `web/` directory, or a fuller
   monorepo structure. Whether the existing `Makefile` and `Dockerfile` grow to
   cover the app or stay Python-only. How `.github/workflows` distinguishes
   Python CI from app CI via path filters, so a frontend commit does not run the
   forecast suite and vice versa.

3. **Hosting.** Vercel is the default for Next.js and pairs with the existing
   Supabase database; note the DB connection must use the Session pooler, since
   the direct host is IPv6-only. Alternatives are self-hosting the container
   alongside whatever runs today, or Supabase's own hosting. Weigh cost,
   preview deploys, and whether serverless connection limits are a problem at
   this scale (they probably are not — a handful of users).

4. **Environments.** Whether there is a staging deploy and a non-production
   database, or a single environment. Ties directly to ticket 01's rollback
   story: a migration you can test somewhere first needs somewhere to test it.

Independent enough of 10 and 11 to settle early, and settling it early makes the
later tickets concrete. Leave an ADR.
