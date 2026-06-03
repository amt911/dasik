# DASIK - Arch Linux System Installer Kit

A Python-based tool for automated Arch Linux installation and system configuration.

## Installation

```bash
pip install .
```

## Usage

```bash
dasik <config-file.json>
```

## Configuration

Place your configuration files in the `config/` directory and reference them when running the tool.

### User passwords (salted hash required)

Each user's `hashed_password` must be a **salted password hash** (sha512crypt,
`$6$…`), not plaintext. Generate one with the built-in verb:

```bash
dasik hash-password        # prompts (hidden), prints the $6$… hash to paste in
```

or with a standard tool:

```bash
mkpasswd -m sha-512        # package: whois
openssl passwd -6          # openssl
```

Changing only the hashes and re-running `dasik apply` updates the passwords.

See [docs/install-from-live-iso.md](docs/install-from-live-iso.md) for installing
from a live ISO / VM.

## Development

To install in development mode:

```bash
pip install -e .
```

## License

MIT
