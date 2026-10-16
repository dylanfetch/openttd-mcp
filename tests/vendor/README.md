# Vendored Squirrel interpreter source

`squirrel_2.2_stable.tar.gz` (standalone Squirrel source archive) is the
unmodified distribution downloaded from the Squirrel project on SourceForge.
Its Secure Hash Algorithm 256-bit (SHA-256) digest is
`936efb633f290e15b206c96c6fd44075c70e3085c9c967cab38c13e8cb51c799`
(archive identity).

The distribution's `README` (release description) and `HISTORY` (release
record) call it Squirrel 2.2 stable, but its `include/squirrel.h` (public C
header) makes the compiled executable report Squirrel 2.5 stable. OpenTTD 15.1
instead embeds Squirrel 2.2.5 with OpenTTD-specific modifications. The test
harness therefore uses this archive for standalone language behavior and pure
algorithms; live OpenTTD reload verification remains authoritative for game
bindings.

The source license is reproduced in `SQUIRREL_LICENSE.txt` (Squirrel license
notice) and is also present unchanged as `SQUIRREL2/COPYRIGHT` (license file)
inside the archive.
