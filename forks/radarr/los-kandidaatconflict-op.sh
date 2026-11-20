#!/usr/bin/env bash
# Radarr 7 en de derde MediaGuard-patch voegen beide importredenen toe aan het
# einde van dezelfde enum. Behoud eerst de upstreamwaarden en daarna de drie eigen redenen.
set -euo pipefail
bestand=src/NzbDrone.Core/MediaFiles/MovieImport/ImportRejectionReason.cs
# Deze hash dekt alle conflictpaden, bestandsmodi en Git-blobs van basis, kandidaat en patch.
[[ "$(git ls-files --unmerged -z | git hash-object --stdin)" == f2cb856d1fd4bc8c7342411a3ea0835963462970 ]]
git checkout --ours -- "$bestand"
git apply <<'PATCH'
--- a/src/NzbDrone.Core/MediaFiles/MovieImport/ImportRejectionReason.cs
+++ b/src/NzbDrone.Core/MediaFiles/MovieImport/ImportRejectionReason.cs
@@ -26,5 +26,8 @@ public enum ImportRejectionReason
     NotQualityUpgrade,
     NotRevisionUpgrade,
     NotCustomFormatUpgrade,
-    NotCustomFormatUpgradeAfterRename
+    NotCustomFormatUpgradeAfterRename,
+    MediaGuardRejected,
+    MediaGuardDeferred,
+    MediaGuardUnavailable
 }
PATCH
git add -- "$bestand"
git am -q --continue
