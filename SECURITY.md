# Security and private data

Do not attach credentials, SQLite databases, real chat histories, or personal configuration files when reporting an issue. Use synthetic examples. Model identifiers and screenshots can also reveal paths or server details.

For a vulnerability that exposes credentials, use GitHub's Security tab and private vulnerability reporting if available. Keep exploitable details and secrets out of public issues. If no private reporting channel is available, open an issue requesting one without including sensitive details.

The gateway is intended for personal use on loopback. Do not expose it to the network or turn it into a multi-user service. Context sent to a cloud model can include earlier messages in the chat. There is no automatic fallback to cloud.

Optional API services are configured only in the private Codex directory. Never commit `services.json`, `service-keys/`, or their backups. Service keys are stored in local plaintext files with `0600` permissions, or read from the gateway process environment. Each service receives only its own key; ChatGPT session credentials are reserved for the default OpenAI route. Catalogs contain service model aliases, not endpoint URLs or keys. Requests refuse redirects, and disabled or removed services cannot be used through stale catalog entries.

Selecting an additional service sends the conversation context included by Codex to the user-configured endpoint. Configure only endpoints you intend to use. The gateway itself remains bound to loopback even when an upstream server is on another computer.
