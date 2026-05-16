# TSV6 Boot Config Safety

`/boot/firmware/config.txt` is firmware configuration, not runtime state. The
live kiosk should not edit it with repeated `sed -i`, `tee -a`, or append
blocks. Those direct writes are risky on the FAT boot partition, especially if
the card is removed before the host has flushed writes.

The repo-managed owner for boot config writes is:

```bash
sudo bash scripts/install-boot-config.sh
```

That installer validates the current and replacement files, stores a timestamped
backup under `/home/<user>/boot-config-backups/`, writes the golden template from
`config/boot/config.txt.golden`, refreshes
`/boot/firmware/config.txt.last-known-good`, creates `.metadata_never_index` on
the boot partition, and runs `sync` before exiting.

`tsv6-boot-config-guard@.service` runs before `tsv6@<user>.service`. It rejects
missing, empty, NUL-filled, non-ASCII, or display-incomplete boot configs and
restores `config.txt.last-known-good` when possible.

When mounting the SD card on macOS, eject the `bootfs` volume cleanly before
removing the card. Do not rely on pulling the card after Finder looks idle.
