## Summary

Describe what this pull request changes and why.

## Related Issue

Link an issue when applicable, for example `Fixes #123`.

## Validation

Describe how this change was tested. Include automated tests and any small sample workflow used.

- [ ] `python -m pytest -q` passes locally
- [ ] I tested changed behavior on disposable or copied sample media, if applicable
- [ ] I updated documentation when user-visible behavior changed

## Processing Impact

Check every area affected by this change:

- [ ] Filename or destination-path behavior
- [ ] Metadata reading or writing
- [ ] Sidecars or Apple Live Photo pairing
- [ ] CSV/TXT logs, checkpointing, or `--resume-csv`
- [ ] Windows-only behavior or cross-platform compatibility
- [ ] Packaging or command-line interface
- [ ] None of the above

## Privacy And Safety

- [ ] This pull request does not include personal media files, generated logs, private paths, or unredacted sensitive metadata.
