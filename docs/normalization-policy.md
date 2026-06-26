# Phase 0.2 — Locking Down the Normalization Policy

## The two policy decisions, locked

### Policy 1 — Trailing slash: **keep distinct by default**

`/page` and `/page/` are **different keys** in the graph until evidence (redirect, canonical tag, identical content) says otherwise. The `normalize()` function does not touch trailing slashes.

**Two narrow exceptions** (these are not "merging"; they're cleaning up empty-path cases):
- A URL with no path component at all (`http://site.org`) becomes `http://site.org/`. The root path is canonically `/`.
- A path that is just the dot-segment cleanup of `/.` or `/./` collapses to `/` because of RFC dot-segment removal — that's syntactic, not policy.

Everything else stays as-is. The `canonicalize()` layer (post-fetch) is where `/page` and `/page/` get merged if a redirect or canonical tag tells us to.

**Why this way:** some servers (lots of legal/government sites, anything on IIS, many CMSes) really do treat them as different resources. Aggressive merging silently breaks those sites. Conservative merging is recoverable — if `canonicalize()` later learns they're the same, we update `canonical_url`. The reverse (un-merging after a wrong merge) is much harder.

### Policy 2 — Query params: **sort + strip an allowlist + keep everything else**

- **Sort** all params alphabetically by key. Safe in 99%+ of cases for read-only resource URLs. If we ever meet an endpoint where order matters, we can exclude it from sorting via a host/path-based override list (also config-driven).
- **Strip** a maintained allowlist of known tracking/session params. This list lives in a config file, not in the code, because it grows over time. Seed it with:

  ```
  utm_source, utm_medium, utm_campaign, utm_term, utm_content,
  fbclid, gclid, msclkid, mc_eid, mc_cid,
  ref, ref_src, ref_url,
  sessionid, sid, phpsessid, jsessionid, aspsessionid,
  _ga, _gl, igshid, yclid, twclid, __s,vero_id, trk, trkInfo
  ```

  Note this is for *content identity only*. We keep the `original_url` field intact so we can always reconstruct what the site actually served.
- **Keep** all other params, including ones that look junky (`?fbclid=…` is on the strip list, but `?lang=en` is not — `lang` is real content for a multilingual court site).
- **Drop** the query entirely if it's empty after stripping (`http://site.org/x?` → `http://site.org/x`, and `http://site.org/x?fbclid=ABC` → `http://site.org/x`).
- **Duplicate keys**: keep them, sorted by (key, value). Some sites legitimately use `?tag=a&tag=b`. Don't dedupe.
