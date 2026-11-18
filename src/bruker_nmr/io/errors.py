"""Stable, sanitized import errors: never embed paths, metadata values or titles."""

DETAILS = {
    "markup_processed_required": "Save requires annotations on the original processed pdata grid.",
    "markup_changed_reopen": "Annotation files or the processed spectrum changed on disk. Reopen the dataset before saving.",
    "markup_save_locked": "This pdata is locked for annotation saving. Close other writers and check for an interrupted save.",
    "markup_restore_required": "Saving was interrupted during recovery. Restore the previous annotation files from the recovery ZIP in pdata.",
    "markup_save_failed": "Could not write the annotations. Check write access and free space. Existing edits remain open.",
    "markup_invalid_destination": "Choose a regular local folder for the extracted dataset.",
    "markup_destination_required": "Choose a parent folder for the extracted dataset.",
    "markup_round_trip_failed": "The current annotations cannot be represented on this processed grid.",
    "markup_value_out_of_range": "An annotation value exceeds the supported Bruker text range.",
    "markup_size_limit": "The annotations exceed the supported file size.",

    "missing_acqus": "Missing acqus. Acquisition cannot be verified; acqu is not substituted.",
    "missing_procs": "Missing procs. Use Process from FID if that opening mode is available.",
    "unsupported_dimension": "Unsupported dimension. Only one-dimensional proton spectra are supported.",
    "unsupported_nucleus": "Unsupported nucleus. This version supports 1H, not 13C or other nuclei.",
    "explicit_selection_required": "Explicit selection required. Choose an experiment and processing number.",
    "imaginary_unavailable_use_fid": "Imaginary unavailable. Phase controls require 1i or Process from FID.",
    "unsupported_processed_window": "Unsupported processed window. Only the full processed grid is supported.",
}


class InputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(DETAILS.get(code, code.replace("_", " ").capitalize()))
