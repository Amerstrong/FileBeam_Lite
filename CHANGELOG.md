# Changelog

All notable changes to FileBeam Lite will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [1.0.0] - 2026-11-21

### Added

- Zero-install mobile browser file transfer.
- Local-network QR-code access.
- Upload and download folders for bidirectional transfer.
- A 10 MB free-edition file-size limit with upgrade guidance.
- Windows standalone executable package with automated HTTP smoke tests.

## [1.0.4] - 2026-11-22

### Fixed

- Prevent stale FileBeam Lite instances from serving an old cached upgrade page.
- Keep the website upgrade button fixed and immediately clickable on mobile.
- Disable HTML and API response caching.

## [1.0.5] - 2026-11-22

### Changed

- Free edition now only links to the official website and contains no activation or license validation logic.
- Pro activation verification lives in the private Pro project and Cloudflare Worker.

