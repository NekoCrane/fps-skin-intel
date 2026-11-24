# Project Memory

- Project: fps-skin-intel; existing fork UI and weapon-skins branch remain the implementation baseline.
- Parent workspace memory: F:/NekoCrane/MEMORY.md. No credential values belong in either memory file.
- 2026-11-24: User requested scripting to reduce reasoning quota. run_weapon_weekly.py is the deterministic entry; source-registry.json owns sources/jobs, original collectors remain authoritative, local-only/weapon-skins/weekly holds full logs and bounded exception batches. No LLM calls belong in normal collection; never route HTTP/rate-limit retries through a model. Follow docs/weapon-skins-automation.md for native scheduling and publishing.
- 2026-11-24: User revoked the one-year collection window. Retain all verified history; prioritize fresh official skin news in the Monday 11:00 Asia/Shanghai workflow. Unknown base-weapon mapping can remain official news without becoming a counted skin. Weekly scan overlap is an efficiency mechanism, never an admission age limit. Follow docs/weapon-skins.md for discovery, refresh, validation and publishing.
- 2026-11-24: Delta Force collection uses the official Bilibili account 3494376565115651. Enumerating account titles is distinct from mapping individual skins; only explicit weapon/name evidence enters the catalogue. Keep unresolved videos in the source inventory and do not claim frame review from metadata alone.
