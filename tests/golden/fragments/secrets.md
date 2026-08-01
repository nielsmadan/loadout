## Secrets

API keys live in a SOPS-encrypted file (encrypted at rest, outside this conversation's read scope). The zsh wrappers around `claude`/`codex`/`agy`/`opencode`/`nvim`/`mvim`/`neovide` run each tool through `sops exec-env`, injecting the decrypted values into **that tool's subprocess only** — never the parent shell or unrelated processes.

What this means for you (the agent):

- **Don't look for API keys in shell env.** They aren't there. `env | grep -i key`, sourcing `~/.airc`, reading `~/.zshrc`, etc. won't find them.
- **Don't try to decrypt, list, or print contents of the secrets store.** No `sops -d …`, no reading `~/.config/sops/age/keys.txt`, no cataloguing variable names. If an MCP call fails for lack of an env var, surface that to the user — don't try to source the value yourself.
- **Trust auto-injection for HTTP MCPs and CLI tools.** When you invoke an HTTP MCP tool, the relevant token is already in this process's env (injected at launch). You don't need to fetch or check it.
- **If a needed credential genuinely isn't injected**, ask the user. They'll decide whether to add it to the store or pass it some other way.

Architecture details (for if the user asks you to help debug or extend the setup, not for general lookup): `~/rc/CLAUDE.md` has the full description.