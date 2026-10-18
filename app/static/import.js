/* Import (redesign wave 12, lane IMPORT; REDESIGN-GOAL dimension 13, Karl's K3 b).
 * File › Import… (Ctrl+I), Import from Media Browser…, and files or a folder dropped from
 * the OS onto the Project panel add footage to the open project. Scaffold: the contract
 * only (judge/WAVE12.md); lane IMPORT builds it. */
(function () {
  'use strict';
  window.rcImport = {
    open() {},          // the Import dialog (pick files / a folder on the board's machine, or upload)
    browse() {},        // the Media Browser (folders on the machine that runs the board)
    drop(files) {},     // files from a DataTransfer (an OS drop), uploaded into the project
  };
})();
