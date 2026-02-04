import { useRef, useState } from "react";
import { uploadPdf } from "../api/upload";

const MAX_BYTES = 10 * 1024 * 1024;

function bytesToMB(bytes: number) {
  return (bytes / (1024 * 1024)).toFixed(2);
}

export default function UploadCard() {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number>(0);
  const [indeterminate, setIndeterminate] = useState(false);

  function validate(file: File): string | null {
    setSuccess(null);

    const isPdf =
      file.type === "application/pdf" ||
      file.name.toLowerCase().endsWith(".pdf");

    if (!isPdf) return "Only PDF files are allowed.";
    if (file.size === 0) return "File is empty.";
    if (file.size > MAX_BYTES) {
      return `File too large. Max is 10 MB (yours is ${bytesToMB(file.size)} MB).`;
    }
    return null;
  }

  async function handleFile(file: File) {
    const validationError = validate(file);
    if (validationError) {
      setError(validationError);
      return;
    }

    setError(null);
    setUploading(true);
    setProgress(0);
    setIndeterminate(false);

    try {
      await uploadPdf(file, (p) => {
        if (p.type === "progress") {
          setIndeterminate(false);
          setProgress(p.percent);
        } else if (p.type === "indeterminate") {
          // still label as uploading, just no % available
          setIndeterminate(true);
        } else if (p.type === "done") {
          setIndeterminate(false);
          setProgress(100);
        }
      });

      setSuccess(`Uploaded successfully: ${file.name}`);
    } catch (e: any) {
      setError(e?.detail || "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div className="container">
      <div className="card">
        <h1 className="title">AccessEd</h1>
        <p className="subtitle">Upload a PDF and check its accessibility</p>

        <div
          className={`dropzone ${dragOver ? "dragover" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Upload PDF"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
        >
          <div className="drop-title">Drag & drop PDF here</div>
          <div className="drop-hint">or click to choose file</div>

          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
              e.currentTarget.value = "";
            }}
          />
        </div>

        <div className="meta">
          Supported:
          <span className="badge">PDF</span>
          <span className="badge">Max 10 MB</span>
        </div>

        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}

        {success && <div className="alert success">{success}</div>}

        {uploading && (
          <div className="progress-wrap">
            <div className="progress-row">
              <span>Uploading…</span>
              <span>{indeterminate ? "…" : `${progress}%`}</span>
            </div>

            <div className="progress-bar">
              <div
                className={`progress-fill ${indeterminate ? "indeterminate" : ""}`}
                style={indeterminate ? undefined : { width: `${progress}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
