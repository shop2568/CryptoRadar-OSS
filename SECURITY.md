# Security Policy

## Supported versions

The latest version on the default branch is the supported development version.

## Reporting a vulnerability

Please do not open a public GitHub issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting feature when available, or contact the maintainer privately through the contact method listed on the maintainer's GitHub profile.

Include:

- A short description
- Reproduction steps
- Potential impact
- A suggested fix, if known

Never include real API keys, exchange credentials, wallet secrets, or other sensitive data in a report.

## Secrets policy

CryptoRadar OSS must never contain:

- Exchange API secrets
- Private keys or seed phrases
- Telegram bot tokens
- Production account credentials
- Private production strategy rules

Use environment variables for any future optional integrations.
