"""The partition wizard: read the real disks, write a `disks` block.

Three layers, deliberately separate (issue #190):

* :mod:`inventory` — `lsblk -J` parsed into what a human needs to see before
  choosing. Pure, so a recorded payload tests it without a disk.
* :mod:`recipes` — the layouts this repo installs and verifies, as functions
  from a few options to a `disks` stanza. Also pure.
* :mod:`tui` — the curses screens, which only collect choices and hand them to
  the two above.

The wizard **never applies**. Partitioning is the one irreversible thing dasik
does, and an assistant that also formatted would fuse the exploratory half
("what disks are there?") with the destructive one, leaving `plan` no longer the
last gate before a disk is erased.
"""
