# Security and private data

Do not attach credentials, SQLite databases, real chat histories, or personal configuration files when reporting an issue. Use synthetic examples. Model identifiers and screenshots can also reveal paths or server details.

For a vulnerability that exposes credentials, use GitHub's Security tab and private vulnerability reporting if available. Keep exploitable details and secrets out of public issues. If no private reporting channel is available, open an issue requesting one without including sensitive details.

The gateway is intended for personal use on loopback. Do not expose it to the network or turn it into a multi-user service. Context sent to a cloud model can include earlier messages in the chat. There is no automatic fallback to cloud.
