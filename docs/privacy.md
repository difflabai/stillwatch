# Privacy

The source package contains code, fictional fixtures, screenshots of those fixtures, and public creator attribution. It contains no API keys, exported settings, real notifications, private account identifiers, or machine-specific configuration.

## Local-only mode

Local-only installation does not call a model or upload history. The recorder stores desktop notification app/title/body text in the user's local state directory. This can include sensitive notification content, so the directory is mode `0700` and the database is mode `0600`. Ordinary records expire after seven days or the 1,000-entry bound. Important, unhandled records are retained until handled. The app's Clear History action removes saved messages.

## AI mode

AI mode is explicit opt-in. New notification app/title/body text, local clock context, and the configured criteria are sent to `https://nano-gpt.com/api/v1/decisions`. Bodies are capped at 3,000 characters for classification. Stored history is not replayed or uploaded. Hosting/provider retention is governed by NanoGPT and the chosen upstream model; local storage does not imply that a cloud request is private or zero-retention.

`excluded_apps` accepts case-insensitive shell-style patterns matching the notification's app label or GTK app ID. Excluded apps stay in local history and are not classified remotely. For example, use your actual sensitive app identifiers in your own configuration; no exclusion list is prefilled from another user's applications.

The API key is read from `NANOGPT_API_KEY` or Stillwatch's own private credential file. The installer never scans unrelated environment files, processes, keyrings or applications to find a key. Keys are not placed in command arguments, notification text, screenshot fixtures, logs or repository assets.

Diagnostics log error types rather than notification contents. Both screenshot generation and automated tests use isolated, temporary state directories. Screenshot generation renders only the app's own widget tree and never captures the user's entire desktop.

Uninstallation retains local history, policy and credentials. Remove those files yourself after closing the apps if you also want to remove private state. XDG environment overrides determine the exact directories; the default locations are `~/.config/stillwatch` and `~/.local/state/stillwatch`.

## Paired devices

Pairing explicitly shares notifications recorded after installation, their classifications, device labels, and dismissal state over an existing authenticated SSH connection. Both devices keep their own local SQLite copy. The AI key is read only by the chosen authoritative server and is never part of replication. Only that device makes model requests; satellites do not require a cloud key. Moving an existing deployment's authority requires a separate, explicit credential setup on the new server.

Private `pairing.json` contains SSH host aliases or addresses; it is never a repository asset. SSH runs with batch authentication and strict host-key checking, without a public listener or third-party relay. The shared operation log obeys notification retention. Small event-ID dismissal tombstones remain to prevent old offline deliveries from reviving an acknowledged event; they contain no message text. Old unshared history is not exported automatically when pairing.
