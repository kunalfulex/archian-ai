# Key rotation checklist (Phase 1.1)

Gitleaks found **no leaks** in the assembled monorepo tree (scan date: 2026-10-15).
Still rotate credentials that may have ever appeared in private `edison-ai` / `edisonai-frontend` history, even if scrubbed from this tree.

## Rotate / reissue (owner action)

- [ ] OpenAI API key(s)
- [ ] Anthropic API key(s)
- [ ] JWT / session secrets (`JWT_SECRET`, related)
- [ ] SQL connector encryption key
- [ ] Salesforce connected-app client secrets (prod + sandbox / Edison SSO apps)
- [ ] Google OAuth client secret + any Picker API keys
- [ ] Stripe secret key + webhook signing secret (cloud / archian-cloud)
- [ ] Cloudflare Turnstile secret
- [ ] Redis / DB passwords if ever committed
- [ ] GitHub tokens used for archian-cloud Render secret file
- [ ] AWS / S3 credentials if used (`edisonaistorage` etc.)
- [ ] Any SMTP / email provider keys

After rotation, update Render (dev / UAT / prod) and local `.env` only — never commit values.

## Local assembly provenance

See `SOURCE_REVISIONS.txt` (gitignored) for source commit SHAs used to build this tree.
