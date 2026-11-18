# Save annotations and rectangle peak picking

In a processed Bruker spectrum use **File → Save annotations** or **Ctrl+S**.
The selection determines which `pdata/<procno>` is written. Opening a ZIP and choosing
Save prompts for a destination parent. NMR-go creates a unique `NMR-go-dataset-…`
folder there, copies the complete ZIP contents, saves to the selected processing
and continues from the new folder. Save again to update that folder. Close/open
with unsaved annotations still asks about discarding edits.

Save records bounds and corrections in `intrng`, available normalized values in
`integrals.txt`, and retained picks in `peaklist.xml`. `procs` carries the corresponding
`INTSCL` coefficient when available. NMR-go uses `nmr-go-markup.json` to restore the
explicit anchor or frozen scale, ppm zero, visibility and review annotations. Keep the
sidecar beside the native files for exact NMR-go reopening. The application falls back
to native files when the content hashes change. Confirm interoperability with TopSpin
separately if you need to continue editing there.

Save writes annotations against the existing processed spectrum. If you have applied
phase, baseline, or independent FID processing in NMR-go, use Undo to return to the
on-disk processed spectrum before Save, or export the current view to PDF. Numerical
spectral data and acquisition files are preserved. Appearance and view history remain
session settings. Existing marks apply to this single selected pdata. Reopening another
processing number loads its own annotations.

## Recover the preceding annotation files

Each save creates a dated `.nmr-go-backup-*.zip` beside the files in the selected
processing directory. The archive includes the prior versions of replaced files and
`restore.json`, which lists the files originally absent. Keep these archives locally.
When moving the complete saved set as a ZIP for import, retain the backup archives
separately because input ZIPs do not support nested archives.

1. Close the dataset in NMR-go and any other editor or TopSpin. Preserve a copy of
   the current processed folder before recovery.
2. Open the backup for the save you want to reverse. Copy the files it contains,
   except `restore.json`, back into that same processing folder. Replace the
   corresponding files there.
3. Open `restore.json`. For each name listed under `absent`, move the corresponding
   current file, if present, to your preserved copy outside the processing folder.
   Leave all other spectra and data files in place.
4. After an interrupted save and once all writers are stopped, move any leftover
   `.nmr-go-save.lock` or `.nmr-go-write-…` directory aside. Reopen the dataset.

## Group peak picking

Enter **Pick Peaks** with **K**. Click to pick near an extremum, or drag a rectangle
from a clear part of the plot to pick all sample maxima whose X and Y are within your box.
Dragging the selected peak handle moves that peak. The lower
edge can exclude noise and the upper edge excludes maxima above it. Each flat top
uses a single midpoint sample. Existing peaks at the same sample stay in place.
One **Ctrl+Z** reverses the full addition. Selection changes neither zoom nor
calibration. Use **Ctrl+drag** to pan and the Peaks table to review the records.
