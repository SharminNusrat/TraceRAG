export function FileDropzone({ label, hint, accept, directory, file, onChange }) {
  const directoryAttributes = directory ? { webkitdirectory: '', directory: '' } : {};
  const selectedFileLabel = file ? `${Math.ceil(file.size / 1024)} KB selected` : hint;

  return (
    <label className="dropzone">
      <input
        type="file"
        accept={accept}
        multiple={directory}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        {...directoryAttributes}
      />
      <span>↑</span>
      <b>{file?.name ?? label}</b>
      <small>{selectedFileLabel}</small>
      <em>Browse files</em>
    </label>
  );
}
