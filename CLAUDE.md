# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Run & Execution
- **Run check-in script**: `python sign.py` (or `uv run sign.py` if using uv)
- **Install dependencies**: `pip install requests pycryptodome pillow loguru` (or `uv pip install requests pycryptodome pillow loguru`)

### Run-time Arguments
The script supports the following command line arguments (each can also be configured in `config.json` or as UPPERCASE environment variables):
- `--cookies <path>`: Cookie file path (defaults to `cookies.txt`)
- `--host <host>`: Check-in host (defaults to `hbkjzy.qmx.chaoxing.com`)
- `--address <address>`: Custom check-in address
- `--lat <latitude>`: Custom latitude coordinate
- `--lng <longitude>`: Custom longitude coordinate
- `--photo <path>`: Path to a custom photo to upload for signing
- `--device <name>`: Simulating device name (defaults to `iPhone 12`)
- `--username <username>`: ChaoXing login username/phone
- `--password <password>`: ChaoXing login password
- `--bark-device-key <key>`: Bark push notification key
- `--bark-device-token <token>`: Bark device token

## System Architecture

The project consists of a single-file automated check-in client (`sign.py`) designed for ChaoXing (超星) location and photo sign-ins, typically run locally or via GitHub Actions.

### Flow of Execution
1. **Authentication**: Loads cookies from `cookies.txt`. If missing or invalid, authenticates via `fanyalogin` (AES key: `u2oh6Vu^HWe4_AES`) and saves updated cookies.
2. **Version Retrieval**: Dynamically fetches the current mobile static JS version (`v` parameter) from the target host.
3. **Session Handshake**: Exchanges user credentials for a pedestal session token (`ermLogin`) and sets the `X-Token` header.
4. **Role Retrieval**: Obtains the student's active role ID (`getInfox`).
5. **Metadata Verification**: Polls `getStudentInfo` to extract the current check-in batch ID, bed ID, student ID, and location requirements.
6. **Location Parsing**: Resolves location coordinates and address string (command args > config/env > batch rules `qdwz`).
7. **Photo Upload (Optional)**: If the batch status requires a photo, uploads a picture to the ChaoXing cloud disk (`pan-yz.chaoxing.com`). If no custom photo is supplied, generates a 600x800 black JPEG using `Pillow`. Supports MD5-based query for cloud deduplication (fast-pass / 秒传).
8. **Payload Encryption**: Bundles check-in metrics into JSON and encrypts them via DES CBC (key: `QRCODENC`), formatting output as a hex string.
9. **Submission & Notification**: Submits payload to `clockIn` and sends push notification status using Bark service.

### Logging Configuration (Loguru)
- **Console Output**: Configured at `INFO` level for high-level step updates.
- **Log Files**: Configured at `DEBUG` level to write to `logs/log_{time:YYYY-MM-DD_HH-mm-ss}.log`.
- Detailed HTTP requests/responses, raw cookies, encryption parameters, and raw JSON payloads should only be logged at the `DEBUG` level to keep the console clean and ensure diagnostics are preserved.
